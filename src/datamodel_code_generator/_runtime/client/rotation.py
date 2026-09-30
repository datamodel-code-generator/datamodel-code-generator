"""The refresh token grant: one provider's rotating token family, renewed by one shared job at a time.

The family keeps its current token set, the refresh tokens it spent, and the failure that stopped it. A refresh token a
request may have delivered is spent: the family never sends it again, whatever the answer. A family with a token load
reads its persisted token set before its first acquisition, before each refresh, after invalid_grant, and on request.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import TYPE_CHECKING, Literal, cast

from .auth import BearerCredential, TokenPersistenceContext, TokenSet, TokenVersion
from .errors import (
    AuthConfigurationError,
    AuthReauthorizationRequiredError,
    AuthStateConflictError,
    AuthStateUncertainError,
    AuthTokenLoadError,
    AuthTokenStoreConflictError,
    DeliveryState,
    InsufficientScopeError,
    TokenExpiredError,
)
from .oauth import (
    Exchanged,
    InvalidTokenResponseError,
    consumable_failure,
    token_material,
    unusable_success,
    within,
)
from .refresh import (
    Again,
    AsyncTokens,
    AsyncWaiter,
    Failed,
    Job,
    Published,
    SharedRefresh,
    SyncTokens,
    SyncWaiter,
    checked_context,
    clone_error,
    refresh_margin,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    from .admission import CallAdmission
    from .auth import AccessToken, AsyncTokenLoad, CredentialContext, RefreshState, TokenLoad
    from .errors import AuthFailureKind, ReauthorizationCondition, SDKError, TokenLoadPurpose, TokenPersistencePurpose
    from .oauth import AsyncTokenEndpoint, Endpoint, TokenEndpoint
    from .options import OAuthProviderOptions
    from .refresh import Outcome


@dataclass(frozen=True, slots=True)
class Adopted(Published):
    """Material of a token set a refresh or a load made the family's current one."""

    token_set: TokenSet


@dataclass(frozen=True, slots=True)
class Reloaded(Again):
    """A token set a load made current without material to serve, after which every waiter claims again.

    A token set that can never serve is still made current, so no older one comes back, and stops the family.
    """

    token_set: TokenSet
    stopped: Failed | None = None


@dataclass(frozen=True, slots=True)
class Refused(Again):
    """A reload's failure, raised to the caller that requested it alone; the family stays as it was."""

    error: SDKError


@dataclass(frozen=True, slots=True)
class Loading:
    """A step of a job reading the persisted token set, for its runner to perform."""

    context: TokenPersistenceContext


@dataclass(frozen=True, slots=True)
class Sending:
    """A step of a job sending the refresh request, for its runner to perform."""

    form: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Unread:
    """What a load step returns once the load raised."""

    error: Exception


class RotationJob(Job):
    """A job of a refresh token family: its initial load, a refresh, or an explicit reload.

    A refresh records under the family's lock which read of the persisted token set it runs, if any, so a read that
    outlives its session or stops fails the job as that read's failure.
    """

    __slots__ = ("kind", "reading")

    def __init__(self, kind: Literal["load", "refresh", "reload"]) -> None:
        """Start queued; only a refresh sends a token request."""
        super().__init__(exchanges=kind == "refresh")
        self.kind = kind
        self.reading: Literal["before_refresh", "invalid_grant_reload"] | None = None

    @property
    def purpose(self) -> Literal["initial_load", "reload"]:
        """Return why a load job reads the persisted token set."""
        return "initial_load" if self.kind == "load" else "reload"


def checked_token_set(value: object) -> TokenSet:
    """Refuse a token set whose access token has a naive expiry or another type than Bearer."""
    if not isinstance(value, TokenSet):
        raise AuthConfigurationError(field_path=("token_set",), condition="invalid_type")
    access = value.access_token
    if (expires_at := access.expires_at) is not None and expires_at.utcoffset() is None:
        raise AuthConfigurationError(field_path=("token_set", "access_token", "expires_at"), condition="invalid_value")
    if access.token_type.lower() != "bearer":
        raise AuthConfigurationError(
            field_path=("token_set", "access_token", "token_type"), condition="unsupported_token_type"
        )
    return value


def cache_key(endpoint: Endpoint, client_id: str, method: str, audience: str | None, scopes: tuple[str, ...]) -> str:
    """Return the fingerprint of a token family's fixed configuration, without tokens or secrets.

    It names the token endpoint by its canonical origin with the path and query given, so a default port changes
    nothing; callbacks still keep one storage namespace per persistent family, which the fingerprint does not replace.
    """
    import json  # noqa: PLC0415
    from urllib.parse import urlsplit  # noqa: PLC0415

    parts = urlsplit(endpoint.url)
    url = endpoint.origin + parts.path + (f"?{parts.query}" if parts.query else "")
    canonical = json.dumps(
        [url, client_id, method, audience, sorted(set(scopes))], ensure_ascii=False, separators=(",", ":")
    )
    return "oauth-refresh-v1:" + hashlib.sha256(canonical.encode()).hexdigest()


def _renewal(
    access: AccessToken, received: datetime, receipt: float, *, renewable: bool = True
) -> tuple[float | None, float | None]:
    """Return the monotonic times from which an access token is no longer served and at which it expires.

    Both are None when it does not expire. A token a refresh can renew is renewed a tenth of its lifetime, at most
    thirty seconds, before it expires.
    """
    if (expires_at := access.expires_at) is None:
        return None, None
    expires = receipt + (ttl := (expires_at - received).total_seconds())
    return expires - (refresh_margin(ttl) if renewable else 0.0), expires


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


class RotationFamily(SharedRefresh):
    """A refresh token family: its current token set, the refresh tokens it spent, and what stopped it.

    Without a token load, the constructor's token set becomes current at the first acquisition; with one, the first
    acquisition loads the persisted token set and keeps the newer of the two. A stopped family raises a new instance of
    the failure that stopped it until a newer token set replaces the current one.
    """

    __slots__ = ("_audience", "_cache_key", "_initial", "_initialized", "_loads", "_spent", "_stopped", "tokens")

    def __init__(
        self, options: OAuthProviderOptions, initial: TokenSet | None, audience: str | None, key: str, *, loads: bool
    ) -> None:
        """Keep the constructor's token set, the fixed audience, and the cache key; nothing is current yet."""
        super().__init__(options)
        self._initial = initial
        self._audience = audience
        self._cache_key = key
        self._loads = loads
        self._initialized = False
        self.tokens: TokenSet | None = None
        self._spent: set[bytes] = set()
        self._stopped: Failed | None = None

    def usable(self, context: CredentialContext, *, force: bool) -> Published | None:
        """Return usable material, refuse a caller the family cannot serve, or return None to acquire or join.

        A token claiming another audience than the fixed one, or known grants missing a required scope, refuse the
        caller without a refresh, which neither changes nor widens them; a family without a refresh token stops once its
        access token expired or a refresh is forced.
        """
        if self._active is None:
            if (stopped := self._stopped) is not None:
                raise clone_error(stopped.error)
            if not self._initialized and not self._loads:
                initial = self._initial
                assert initial is not None
                self._adopt(initial, _material(initial))
        if (tokens := self.tokens) is None:
            return None
        self._refuse(tokens.access_token, context)
        if (cached := super().usable(context, force=force)) is not None or self._active is not None:
            return cached
        if tokens.refresh_token is None:
            raise clone_error(self._stop(self._reauthorization("no_refresh_token", "admission")).error)
        return None

    def next_job(self) -> Job:
        """Return the initial load of a family never made current, or a refresh; the caller holds the lock."""
        return RotationJob("refresh" if self._initialized else "load")

    def served(self, material: BearerCredential, context: CredentialContext) -> BearerCredential:
        """Refuse material a job delivered that the caller cannot use; the family is unchanged."""
        self._refuse(material.token, context)
        return material

    def spending(self, job: Job) -> TokenSet | None:
        """Return the token set whose refresh token an admitted job sends, or None once the job already ended."""
        with self.lock:
            return self.tokens if job.outcome is None else None

    def outcome(self, exchanged: Exchanged, job: Job, used: TokenSet) -> Outcome:
        """Map a refresh exchange to the family's next token set, or to the failure the job commits."""
        provider_id, refresh_id = self.provider_id, job.refresh_id
        if exchanged.outcome != "success":
            state, error = consumable_failure(exchanged, provider_id=provider_id, refresh_id=refresh_id)
            return Failed(error, state)
        try:
            rotated = self._rotated(exchanged, used)
        except (ValueError, TokenExpiredError) as cause:
            return Failed(
                unusable_success(exchanged, cause, provider_id=provider_id, refresh_id=refresh_id), "UNCERTAIN"
            )
        return rotated

    def _rotated(self, exchanged: Exchanged, used: TokenSet) -> Adopted:
        """Build the next token set from a successful answer, refusing a refresh token the family spent before.

        An omitted refresh token keeps the one sent, and an omitted scope keeps the grants of the access token it
        replaces.
        """
        assert exchanged.fields is not None
        assert exchanged.received is not None
        assert exchanged.receipt is not None
        access, issued, _ = token_material(exchanged.fields, exchanged.received, used.access_token.scopes)
        if issued is not None and issued != used.refresh_token:
            with self.lock:
                reused = self._reused(issued)
            if reused:
                msg = "The token response returns a refresh token the family spent."
                raise InvalidTokenResponseError(msg)
        refresh_at, expires = _renewal(access, exchanged.received, exchanged.receipt)
        return Adopted(
            BearerCredential(access, TokenVersion()),
            refresh_at,
            TokenSet(access, used.refresh_token if issued is None else issued, used.revision + 1),
            expires_at=expires,
        )

    def running(self, job: RotationJob) -> Generator[Loading | Sending, object, Outcome]:
        """Run a job as the steps its runner performs, and return the outcome the job commits.

        A refresh of a family with a load reads the persisted token set right before its request, and again once the
        endpoint answers invalid_grant.
        """
        if job.kind != "refresh":
            return self.loaded(job, (yield Loading(self._persistence(job, job.purpose))))
        if (used := self.spending(job)) is None:
            assert job.outcome is not None
            return job.outcome
        if self._loads:
            reply = yield self._reading(job, "before_refresh")
            if not isinstance(chosen := self._using(job, self.preloaded(job, reply, used)), TokenSet):
                return chosen
            used = chosen
        assert used.refresh_token is not None
        exchanged = yield Sending(_form(used.refresh_token))
        assert isinstance(exchanged, Exchanged)
        if not (self._loads and exchanged.outcome == "rejected" and exchanged.oauth_error == "invalid_grant"):
            return self.outcome(exchanged, job, used)
        if (ended := self._rejecting(job, used.refresh_token)) is not None:
            return ended
        reply = yield self._reading(job, "invalid_grant_reload")
        return self.regranted(job, reply, used)  # noqa: B901 - Its runner receives the outcome as the generator's value.

    def _reading(self, job: RotationJob, purpose: Literal["before_refresh", "invalid_grant_reload"]) -> Loading:
        with self.lock:
            job.reading = purpose
        return Loading(self._persistence(job, purpose))

    def _using(self, job: RotationJob, chosen: TokenSet | Outcome) -> TokenSet | Outcome:
        """Make the token set a refresh sends current once its read ended, or return how the job ended instead."""
        if not isinstance(chosen, TokenSet):
            return chosen
        with self.lock:
            if (ended := job.outcome) is not None:
                return ended
            self.tokens = chosen
            job.reading = None
        return chosen

    def _rejecting(self, job: RotationJob, token: str) -> Outcome | None:
        """Spend a refresh token answered invalid_grant and commit REAUTH_REQUIRED before reading again.

        Return the job's outcome once it already ended.
        """
        with self.lock:
            self._spent.add(_digest(token))
            if job.outcome is None:
                job.reading = "invalid_grant_reload"
                self.state = "REAUTH_REQUIRED"
                self.cache = None
            return job.outcome

    def _persistence(self, job: RotationJob, purpose: TokenPersistencePurpose) -> TokenPersistenceContext:
        assert job.session is not None
        return TokenPersistenceContext(
            provider_id=self.provider_id,
            cache_key=self._cache_key,
            session_id=job.refresh_id,
            deadline=job.session.deadline,
            purpose=purpose,
        )

    def loaded(self, job: RotationJob, reply: object) -> Outcome:
        """Make the newer of the persisted and the current token set current, by revision.

        The same revision must hold the same token set. A family made current keeps a newer one only if it does not
        bring back a spent refresh token; one that can serve nothing stops for reauthorization, except that a family
        stopped by a refresh keeps its state.
        """
        if isinstance(loaded := _stored(reply), Exception):
            return self.unloaded(job, loaded)
        current = self.tokens or self._initial
        if loaded is not None and current is not None and loaded.revision == current.revision and loaded != current:
            return _refused(job, self._conflict(job, current, loaded, self._load_state(job)))
        newer = loaded is not None and (current is None or loaded.revision > current.revision)
        chosen = loaded if newer else current
        if chosen is None:
            return self._reauthorization("missing_token", "load", job)
        if self._spends(chosen) or (not newer and self._initialized):
            return Again()
        return self._adoptable(job, chosen, newer=newer)

    def preloaded(self, job: RotationJob, reply: object, used: TokenSet) -> TokenSet | Outcome:
        """Return the token set a refresh sends, after reading the persisted one right before it, or end the job.

        A failed read, or another token set at the same revision, ends the job without a request and leaves the family
        as it was; an older token set, or one bringing back a spent refresh token, changes nothing. A newer token set
        that can serve needs no request, although the admission the refresh paid for is kept; a newer expired one is
        refreshed instead, and one that can never serve stops the family.
        """
        if isinstance(loaded := _stored(reply), Exception):
            return self._unread(job, loaded)
        if loaded is None or loaded.revision < used.revision or loaded == used:
            return used
        if loaded.revision == used.revision:
            return Failed(self._conflict(job, used, loaded, "FAILED_NOT_SENT"), "FAILED_NOT_SENT")
        if self._spends(loaded):
            return used
        if loaded.refresh_token is not None and _material(loaded) is None:
            return loaded
        return self._adoptable(job, loaded, newer=True)

    def regranted(self, job: RotationJob, reply: object, used: TokenSet) -> Outcome:
        """Make a newer usable persisted token set current after invalid_grant, or require reauthorization.

        Only a newer token set with an unexpired access token and a refresh token the family never spent recovers; a
        failed read or a conflict becomes the cause of the reauthorization error.
        """
        if isinstance(loaded := _stored(reply), Exception):
            return self._unread(job, loaded)
        cause: BaseException | None = None
        if loaded is not None and loaded.revision == used.revision and loaded != used:
            cause = self._conflict(job, used, loaded, "REAUTH_REQUIRED")
        elif (
            loaded is not None
            and loaded.revision > used.revision
            and not self._spends(loaded)
            and (material := _material(loaded)) is not None
        ):
            return Adopted(material.material, material.refresh_at, loaded)
        return self._ungranted(job, cause)

    def _unread(self, job: RotationJob, cause: BaseException) -> Failed:
        """Fail a refresh whose read of the persisted token set failed, outlived its session, or stopped.

        A failed read before the request leaves the family as it was; one after invalid_grant requires reauthorization.
        """
        if job.reading == "before_refresh":
            return Failed(self._load_error(job, "before_refresh", "FAILED_NOT_SENT", cause), "FAILED_NOT_SENT")
        return self._ungranted(job, self._load_error(job, "invalid_grant_reload", "REAUTH_REQUIRED", cause))

    def _adoptable(self, job: RotationJob, chosen: TokenSet, *, newer: bool) -> Outcome:
        """Return how a load makes a token set current: with its material, to refresh, or stopping the family.

        A family a refresh stopped keeps its state instead of adopting a token set that can never serve.
        """
        if (material := _material(chosen)) is not None:
            return Adopted(material.material, material.refresh_at, chosen, expires_at=material.expires_at)
        if chosen.refresh_token is not None:
            return Reloaded(chosen)
        if (stopped := self._stopped) is not None and stopped.state != "LOAD_FAILED":
            return Again()
        condition: ReauthorizationCondition = "unusable_loaded_token" if newer else "no_refresh_token"
        return Reloaded(chosen, self._reauthorization(condition, "load", job))

    def unloaded(self, job: RotationJob, cause: BaseException) -> Outcome:
        """Fail a load that raised, returned something else than a token set or None, or outlived its session."""
        return _refused(job, self._load_error(job, job.purpose, self._load_state(job), cause))

    def reloading(self, waiter: SyncWaiter | AsyncWaiter) -> RotationJob:
        """Admit an explicit reload as the family's job, refused while the family is closing or any job runs."""
        if not self._loads:
            raise AuthConfigurationError(field_path=("load",), condition="missing_value")
        with self.lock:
            if (lifecycle := self.lifecycle) != "OPEN" or self._active is not None or self.busy():
                raise AuthStateConflictError(
                    action="reload_token_set",
                    state="EXCHANGING" if lifecycle == "OPEN" else lifecycle,
                    delivery_state=DeliveryState.NOT_SENT,
                    provider_id=self.provider_id,
                )
            job = RotationJob("reload")
            admitted = self.enlist(job, waiter)
            assert admitted
            return job

    def reloaded(self, job: RotationJob) -> TokenSet | None:
        """Raise a reload's failure, or return the current token set, or None once the family stopped."""
        if isinstance(outcome := job.outcome, Refused):
            raise clone_error(outcome.error)
        with self.lock:
            return None if self._stopped is not None else self.tokens

    def settle(self, job: Job, outcome: Outcome) -> tuple[RefreshState, str | None]:
        """Apply a job's outcome: a refreshed or loaded token set becomes current, and a failure stops the family.

        A refresh failure after a possible send spends the refresh token; one that sent nothing, and a reload's
        failure, leave the family as it was.
        """
        assert isinstance(job, RotationJob)
        if isinstance(outcome, Again):
            return self._settled(outcome)
        used = None if job.kind != "refresh" or self.tokens is None else self.tokens.refresh_token
        if isinstance(outcome, Adopted):
            if job.progress.sent and used is not None and outcome.token_set.refresh_token != used:
                self._spent.add(_digest(used))
            self._adopt(outcome.token_set, outcome)
            return "READY", None
        assert isinstance(outcome, Failed)
        if job.kind == "refresh":
            if outcome.state == "FAILED_NOT_SENT":
                return "FAILED_NOT_SENT", outcome.error.reason_code
            assert used is not None
            self._spent.add(_digest(used))
        self._stop(outcome)
        return outcome.state, outcome.error.reason_code

    def _settled(self, outcome: Again) -> tuple[RefreshState, str | None]:
        """Apply a load's outcome after which every waiter claims again; a reload's failure changes nothing."""
        if isinstance(outcome, Refused):
            return "FAILED_NOT_SENT", outcome.error.reason_code
        if isinstance(outcome, Reloaded):
            self._adopt(outcome.token_set, None)
            if (stopped := outcome.stopped) is not None:
                self._stop(stopped)
                return stopped.state, stopped.error.reason_code
            return "READY", None
        state = self.state
        assert state != "UNINITIALIZED"
        return state, None

    def timed_out(self, job: Job) -> Outcome:
        """Fail a job whose session ended: a refresh that may have been delivered leaves its outcome unknown."""
        assert isinstance(job, RotationJob)
        if job.kind != "refresh":
            return self.unloaded(job, TimeoutError())
        if job.reading is not None:
            return self._unread(job, TimeoutError())
        return self._uncertain(job, "deadline") if job.progress.sent else super().timed_out(job)

    def failed(self, error: BaseException, job: Job) -> Outcome:
        """Fail a job whose work stopped: a refresh that may have been delivered leaves its outcome unknown."""
        assert isinstance(job, RotationJob)
        if job.kind != "refresh":
            return self.unloaded(job, error)
        if job.reading is not None:
            return self._unread(job, error)
        return self._uncertain(job, "stopped", error) if job.progress.sent else super().failed(error, job)

    def exchange_needed(self, version: object) -> bool:
        """Return whether replacing a rejected version needs a refresh, which a stopped family never sends.

        Neither does a family without a refresh token.
        """
        with self.lock:
            if self._stopped is not None or (current := self.tokens or self._initial) is None:
                return False
            if current.refresh_token is None:
                return False
        return super().exchange_needed(version)

    def replace(self, token_set: object) -> None:
        """Adopt an explicitly supplied token set, restarting a stopped family and completing its initialization.

        It needs a revision above the current one, and a refresh token the family never spent, or no refresh token and
        an unexpired access token; no job may be running, and the family must be open.
        """
        checked = checked_token_set(token_set)
        with self.lock:
            if (lifecycle := self.lifecycle) != "OPEN" or self._active is not None:
                raise AuthStateConflictError(
                    action="replace_token_set",
                    state="EXCHANGING" if lifecycle == "OPEN" else lifecycle,
                    delivery_state=DeliveryState.NOT_SENT,
                    provider_id=self.provider_id,
                )
            if (current := self.tokens or self._initial) is not None and checked.revision <= current.revision:
                raise AuthConfigurationError(field_path=("token_set", "revision"), condition="stale_revision")
            if (refresh := checked.refresh_token) is not None and self._reused(refresh):
                raise AuthConfigurationError(field_path=("token_set", "refresh_token"), condition="spent_token")
            if (material := _material(checked)) is None and refresh is None:
                raise AuthConfigurationError(field_path=("token_set",), condition="unusable_token")
            self._adopt(checked, material)

    def _load_state(self, job: RotationJob) -> str:
        return "LOAD_FAILED" if job.kind == "load" else self.state

    def _load_error(
        self, job: RotationJob, purpose: TokenLoadPurpose, state: str, cause: BaseException
    ) -> AuthTokenLoadError:
        return AuthTokenLoadError(
            purpose=purpose,
            state=state,
            delivery_state=DeliveryState.NOT_SENT,
            phase="load",
            cause=cause,
            provider_id=self.provider_id,
            refresh_id=job.refresh_id,
        )

    def _conflict(
        self, job: RotationJob, current: TokenSet, loaded: TokenSet, state: str
    ) -> AuthTokenStoreConflictError:
        return AuthTokenStoreConflictError(
            action="load",
            expected_revision=current.revision,
            observed_revision=loaded.revision,
            state=state,
            delivery_state=DeliveryState.NOT_SENT,
            phase="load",
            provider_id=self.provider_id,
            refresh_id=job.refresh_id,
        )

    def _ungranted(self, job: RotationJob, cause: BaseException | None) -> Failed:
        return self._reauthorization("invalid_grant", "unknown", job, delivery_state=job.progress.delivery, cause=cause)

    def _reauthorization(
        self,
        condition: ReauthorizationCondition,
        phase: Literal["admission", "load", "unknown"],
        job: Job | None = None,
        *,
        delivery_state: DeliveryState = DeliveryState.NOT_SENT,
        cause: BaseException | None = None,
    ) -> Failed:
        return Failed(
            AuthReauthorizationRequiredError(
                condition=condition,
                state="REAUTH_REQUIRED",
                delivery_state=delivery_state,
                phase=phase,
                cause=cause,
                provider_id=self.provider_id,
                refresh_id=None if job is None else job.refresh_id,
            ),
            "REAUTH_REQUIRED",
        )

    def _refuse(self, token: AccessToken, context: CredentialContext) -> None:
        """Refuse a token claiming another audience than the fixed one, or whose known grants lack a required scope."""
        if (audience := token.audience) is not None and audience != self._audience:
            raise AuthConfigurationError(field_path=("audience",), condition="audience_mismatch")
        if (granted := token.scopes) is not None and not set(context.required_scopes).issubset(granted):
            raise InsufficientScopeError(
                required_scopes=context.required_scopes,
                granted_scopes=granted,
                state=self.state,
                delivery_state=DeliveryState.NOT_SENT,
                phase="admission",
                provider_id=self.provider_id,
            )

    def _uncertain(self, job: Job, failure_kind: AuthFailureKind, cause: BaseException | None = None) -> Failed:
        return Failed(
            AuthStateUncertainError(
                failure_kind=failure_kind,
                state="UNCERTAIN",
                delivery_state=job.progress.delivery,
                cause=cause,
                provider_id=self.provider_id,
                refresh_id=job.refresh_id,
            ),
            "UNCERTAIN",
        )

    def _reused(self, token: str) -> bool:
        return _digest(token) in self._spent

    def _spends(self, token_set: TokenSet) -> bool:
        """Return whether a token set brings back a refresh token the family spent."""
        with self.lock:
            return token_set.refresh_token is not None and self._reused(token_set.refresh_token)

    def _adopt(self, token_set: TokenSet, material: Published | None) -> None:
        self.tokens = token_set
        self.cache = material
        self.state = "READY"
        self._initialized = True
        self._stopped = None

    def _stop(self, failed: Failed) -> Failed:
        self._stopped = failed
        self.state = failed.state
        self.cache = None
        return failed


def _refused(job: RotationJob, error: SDKError) -> Outcome:
    """Fail an initial load, leaving the family LOAD_FAILED, or keep a reload's failure for its caller alone."""
    return Failed(error, "LOAD_FAILED") if job.kind == "load" else Refused(error)


def _stored(reply: object) -> TokenSet | Exception | None:
    """Return the token set a load step read, None when nothing is stored, or why the read failed."""
    if isinstance(reply, Unread):
        return reply.error
    if reply is None:
        return None
    try:
        return checked_token_set(reply)
    except AuthConfigurationError as error:
        return error


def _material(token_set: TokenSet) -> Published | None:
    """Return new material for a supplied token set's access token, or None once it expired."""
    access = token_set.access_token
    received, receipt = datetime.now(timezone.utc), monotonic()
    if (expires_at := access.expires_at) is not None and expires_at <= received:
        return None
    refresh_at, expires = _renewal(access, received, receipt, renewable=token_set.refresh_token is not None)
    return Published(BearerCredential(access, TokenVersion()), refresh_at, expires_at=expires)


def _form(refresh_token: str) -> tuple[tuple[str, str], ...]:
    return (("grant_type", "refresh_token"), ("refresh_token", refresh_token))


class SyncRotation(SyncTokens):
    """A synchronous refresh token family."""

    __slots__ = ("_family", "_load")

    def __init__(  # noqa: PLR0913
        self,
        options: OAuthProviderOptions,
        endpoint: TokenEndpoint,
        initial: TokenSet | None,
        audience: str | None,
        *,
        key: str,
        load: TokenLoad | None,
    ) -> None:
        """Keep the endpoint, the constructor's token set, and the load; nothing runs until the first acquisition."""
        self._family = RotationFamily(options, initial, audience, key, loads=load is not None)
        self._load = load
        super().__init__(self._family, endpoint, audience)

    def obtain(self, context: object, *, force: bool, admission: CallAdmission | None = None) -> BearerCredential:
        """Return usable material or refresh it, after checking the caller's context and before serving it."""
        checked = checked_context(context, self._audience)
        return self._family.served(self._refresh.obtain(checked, force=force, admission=admission), checked)

    def replace(self, token_set: object) -> None:
        """Adopt an explicitly supplied token set."""
        self._family.replace(token_set)

    def reload(self) -> TokenSet | None:
        """Load the persisted token set once, keeping it if it is newer, and return the current one."""
        waiter = SyncWaiter()
        job = self._family.reloading(waiter)
        self._refresh.perform(job, waiter)
        return self._family.reloaded(job)

    def _acquire(self, job: Job) -> Outcome:
        """Perform a job's steps on a worker: its loads and its refresh request."""
        assert isinstance(job, RotationJob)
        steps = self._family.running(job)
        reply: object = None
        while True:
            try:
                step = steps.send(reply)
            except StopIteration as done:
                return cast("Outcome", done.value)
            reply = self._read(step.context) if isinstance(step, Loading) else self._send(step, job)

    def _read(self, context: TokenPersistenceContext) -> object:
        assert self._load is not None
        try:
            return self._load.load(context)
        except Exception as error:  # noqa: BLE001 - A failing load is its step's reply.
            return Unread(error)

    def _send(self, step: Sending, job: Job) -> Exchanged:
        endpoint = self._endpoint
        endpoint.prepare()
        assert job.session is not None
        return endpoint.exchange(step.form, job.session, job.progress, "FAILED_NOT_SENT")


class AsyncRotation(AsyncTokens):
    """An asyncio refresh token family."""

    __slots__ = ("_family", "_load")

    def __init__(  # noqa: PLR0913
        self,
        options: OAuthProviderOptions,
        endpoint: AsyncTokenEndpoint,
        initial: TokenSet | None,
        audience: str | None,
        *,
        key: str,
        load: AsyncTokenLoad | None,
    ) -> None:
        """Keep the endpoint, the constructor's token set, and the load; nothing runs until the first acquisition."""
        self._family = RotationFamily(options, initial, audience, key, loads=load is not None)
        self._load = load
        super().__init__(self._family, endpoint, audience)

    async def obtain(self, context: object, *, force: bool, admission: CallAdmission | None = None) -> BearerCredential:
        """Return usable material or refresh it, after checking the caller's context and loop, and before serving it."""
        checked = checked_context(context, self._audience)
        self._endpoint.bind()
        return self._family.served(await self._refresh.obtain(checked, force=force, admission=admission), checked)

    def replace(self, token_set: object) -> None:
        """Adopt an explicitly supplied token set on the family's event loop."""
        self._endpoint.bind()
        self._family.replace(token_set)

    async def reload(self) -> TokenSet | None:
        """Load the persisted token set once on the family's event loop, as the synchronous family does."""
        from asyncio import get_running_loop  # noqa: PLC0415

        self._endpoint.bind()
        waiter = AsyncWaiter(get_running_loop().create_future())
        job = self._family.reloading(waiter)
        await self._refresh.perform(job, waiter)
        return self._family.reloaded(job)

    async def _acquire(self, job: Job) -> Outcome:
        """Perform a job's steps in a task: its loads and its refresh request."""
        assert isinstance(job, RotationJob)
        steps = self._family.running(job)
        reply: object = None
        while True:
            try:
                step = steps.send(reply)
            except StopIteration as done:
                return cast("Outcome", done.value)
            reply = await (self._read(step.context) if isinstance(step, Loading) else self._send(step, job))

    async def _read(self, context: TokenPersistenceContext) -> object:
        assert self._load is not None
        try:
            return await within(self._load.load(context), context.deadline.remaining())
        except Exception as error:  # noqa: BLE001 - A failing load is its step's reply.
            return Unread(error)

    async def _send(self, step: Sending, job: Job) -> Exchanged:
        endpoint = self._endpoint
        endpoint.prepare()
        assert job.session is not None
        return await endpoint.exchange(step.form, job.session, job.progress, "FAILED_NOT_SENT")
