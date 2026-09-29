"""The refresh token grant: one provider's rotating token family, renewed by one shared job at a time.

The family keeps its current token set, the refresh tokens it spent, and the failure that stopped it. A refresh token a
request may have delivered is spent: the family never sends it again, whatever the answer.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import TYPE_CHECKING

from .auth import BearerCredential, TokenSet, TokenVersion
from .errors import (
    AuthConfigurationError,
    AuthReauthorizationRequiredError,
    AuthStateConflictError,
    AuthStateUncertainError,
    DeliveryState,
    InsufficientScopeError,
    TokenExpiredError,
)
from .oauth import InvalidTokenResponseError, consumable_failure, token_material, unusable_success
from .refresh import (
    AsyncTokens,
    Failed,
    Published,
    SharedRefresh,
    SyncTokens,
    checked_context,
    clone_error,
    refresh_margin,
)

if TYPE_CHECKING:
    from .admission import CallAdmission
    from .auth import AccessToken, CredentialContext, RefreshState
    from .errors import AuthFailureKind
    from .oauth import AsyncTokenEndpoint, Exchanged, TokenEndpoint
    from .options import OAuthProviderOptions
    from .refresh import Job, Outcome


@dataclass(frozen=True, slots=True)
class Rotated(Published):
    """Material a refresh obtained, with the token set replacing the family's current one."""

    token_set: TokenSet


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


def _renewal(access: AccessToken, received: datetime, receipt: float, *, renewable: bool = True) -> float | None:
    """Return the monotonic time from which an access token is no longer served, or None when it does not expire.

    A token a refresh can renew is renewed a tenth of its lifetime, at most thirty seconds, before it expires.
    """
    if (expires_at := access.expires_at) is None:
        return None
    ttl = (expires_at - received).total_seconds()
    return receipt + ttl - (refresh_margin(ttl) if renewable else 0.0)


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


class RotationFamily(SharedRefresh):
    """A refresh token family: its current token set, the refresh tokens it spent, and what stopped it.

    The constructor's token set becomes current at the first acquisition. A stopped family raises a new instance of the
    failure that stopped it until a newer token set replaces the current one.
    """

    __slots__ = ("_audience", "_initial", "_spent", "_stopped", "tokens")

    def __init__(self, options: OAuthProviderOptions, initial: TokenSet, audience: str | None) -> None:
        """Keep the constructor's token set and the fixed audience; nothing is current, spent, or stopped yet."""
        super().__init__(options)
        self._initial = initial
        self._audience = audience
        self.tokens: TokenSet | None = None
        self._spent: set[bytes] = set()
        self._stopped: Failed | None = None

    def usable(self, context: CredentialContext, *, force: bool) -> Published | None:
        """Return usable material, refuse a caller the family cannot serve, or return None to refresh or join.

        A token claiming another audience than the fixed one, or known grants missing a required scope, refuse the
        caller without a refresh, which neither changes nor widens them; a family without a refresh token stops once its
        access token expired or a refresh is forced.
        """
        if self._active is None:
            if (stopped := self._stopped) is not None:
                raise clone_error(stopped.error)
            if self.tokens is None:
                self._adopt(self._initial, _material(self._initial))
        tokens = self.tokens
        assert tokens is not None
        if (audience := tokens.access_token.audience) is not None and audience != self._audience:
            raise AuthConfigurationError(field_path=("audience",), condition="audience_mismatch")
        self._refuse_scopes(tokens.access_token.scopes, context)
        if (cached := super().usable(context, force=force)) is not None or self._active is not None:
            return cached
        if tokens.refresh_token is None:
            stopped = self._stop(
                Failed(
                    AuthReauthorizationRequiredError(
                        condition="no_refresh_token",
                        state="REAUTH_REQUIRED",
                        delivery_state=DeliveryState.NOT_SENT,
                        phase="admission",
                        provider_id=self.provider_id,
                    ),
                    "REAUTH_REQUIRED",
                )
            )
            raise clone_error(stopped.error)
        return None

    def served(self, material: BearerCredential, context: CredentialContext) -> BearerCredential:
        """Refuse material a refresh narrowed below the scopes the caller requires; the family is unchanged."""
        self._refuse_scopes(material.token.scopes, context)
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

    def _rotated(self, exchanged: Exchanged, used: TokenSet) -> Rotated:
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
        return Rotated(
            BearerCredential(access, TokenVersion()),
            _renewal(access, exchanged.received, exchanged.receipt),
            TokenSet(access, used.refresh_token if issued is None else issued, used.revision + 1),
        )

    def settle(self, job: Job, outcome: Outcome) -> tuple[RefreshState, str | None]:
        """Adopt a refreshed token set, or commit a failure: one after a possible send spends the refresh token.

        A failure that sent nothing leaves the family as it was, so a later acquisition sends the same refresh token.
        """
        del job
        tokens = self.tokens
        assert tokens is not None
        used = tokens.refresh_token
        assert used is not None
        if isinstance(outcome, Rotated):
            if outcome.token_set.refresh_token != used:
                self._spent.add(_digest(used))
            self._adopt(outcome.token_set, outcome)
            return "READY", None
        assert isinstance(outcome, Failed)
        if outcome.state != "FAILED_NOT_SENT":
            self._spent.add(_digest(used))
            self._stop(outcome)
        return outcome.state, outcome.error.reason_code

    def timed_out(self, job: Job) -> Failed:
        """Leave the outcome of a refresh that may have been delivered unknown once its session ends."""
        return self._uncertain(job, "deadline") if job.progress.sent else super().timed_out(job)

    def failed(self, error: BaseException, job: Job) -> Failed:
        """Leave the outcome of a refresh that may have been delivered unknown once its work stopped."""
        return self._uncertain(job, "stopped", error) if job.progress.sent else super().failed(error, job)

    def exchange_needed(self, version: object) -> bool:
        """Return whether replacing a rejected version needs a refresh, which a stopped family never sends.

        Neither does a family without a refresh token.
        """
        with self.lock:
            if self._stopped is not None or (self.tokens or self._initial).refresh_token is None:
                return False
        return super().exchange_needed(version)

    def replace(self, token_set: object) -> None:
        """Adopt an explicitly supplied token set, restarting a stopped family.

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
            if checked.revision <= (self.tokens or self._initial).revision:
                raise AuthConfigurationError(field_path=("token_set", "revision"), condition="stale_revision")
            if (refresh := checked.refresh_token) is not None and self._reused(refresh):
                raise AuthConfigurationError(field_path=("token_set", "refresh_token"), condition="spent_token")
            if (material := _material(checked)) is None and refresh is None:
                raise AuthConfigurationError(field_path=("token_set",), condition="unusable_token")
            self._adopt(checked, material)

    def _refuse_scopes(self, granted: tuple[str, ...] | None, context: CredentialContext) -> None:
        if granted is not None and not set(context.required_scopes).issubset(granted):
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

    def _adopt(self, token_set: TokenSet, material: Published | None) -> None:
        self.tokens = token_set
        self.cache = material
        self.state = "READY"
        self._stopped = None

    def _stop(self, failed: Failed) -> Failed:
        self._stopped = failed
        self.state = failed.state
        self.cache = None
        return failed


def _material(token_set: TokenSet) -> Published | None:
    """Return new material for a supplied token set's access token, or None once it expired."""
    access = token_set.access_token
    received, receipt = datetime.now(timezone.utc), monotonic()
    if (expires_at := access.expires_at) is not None and expires_at <= received:
        return None
    renewal = _renewal(access, received, receipt, renewable=token_set.refresh_token is not None)
    return Published(BearerCredential(access, TokenVersion()), renewal)


def _form(refresh_token: str) -> tuple[tuple[str, str], ...]:
    return (("grant_type", "refresh_token"), ("refresh_token", refresh_token))


class SyncRotation(SyncTokens):
    """A synchronous refresh token family."""

    __slots__ = ("_family",)

    def __init__(
        self, options: OAuthProviderOptions, endpoint: TokenEndpoint, initial: TokenSet, audience: str | None
    ) -> None:
        """Keep the endpoint and the constructor's token set; nothing runs until the first acquisition."""
        self._family = RotationFamily(options, initial, audience)
        super().__init__(self._family, endpoint, audience)

    def obtain(self, context: object, *, force: bool, admission: CallAdmission | None = None) -> BearerCredential:
        """Return usable material or refresh it, after checking the caller's context and before serving it."""
        checked = checked_context(context, self._audience)
        return self._family.served(self._refresh.obtain(checked, force=force, admission=admission), checked)

    def replace(self, token_set: object) -> None:
        """Adopt an explicitly supplied token set."""
        self._family.replace(token_set)

    def _acquire(self, job: Job) -> Outcome:
        """Refresh on a worker, unless the job ended before the worker started."""
        family = self._family
        if (used := family.spending(job)) is None:
            assert job.outcome is not None
            return job.outcome
        endpoint = self._endpoint
        endpoint.prepare()
        assert job.session is not None
        assert used.refresh_token is not None
        exchanged = endpoint.exchange(_form(used.refresh_token), job.session, job.progress, "FAILED_NOT_SENT")
        return family.outcome(exchanged, job, used)


class AsyncRotation(AsyncTokens):
    """An asyncio refresh token family."""

    __slots__ = ("_family",)

    def __init__(
        self, options: OAuthProviderOptions, endpoint: AsyncTokenEndpoint, initial: TokenSet, audience: str | None
    ) -> None:
        """Keep the endpoint and the constructor's token set; nothing runs until the first acquisition."""
        self._family = RotationFamily(options, initial, audience)
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

    async def _acquire(self, job: Job) -> Outcome:
        """Refresh in a task, unless the job ended before the task started, as behind a blocked event loop."""
        family = self._family
        if (used := family.spending(job)) is None:
            assert job.outcome is not None
            return job.outcome
        endpoint = self._endpoint
        endpoint.prepare()
        assert job.session is not None
        assert used.refresh_token is not None
        exchanged = await endpoint.exchange(_form(used.refresh_token), job.session, job.progress, "FAILED_NOT_SENT")
        return family.outcome(exchanged, job, used)
