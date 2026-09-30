"""Store refreshed and replaced token sets of a rotating family, keeping one whose store failed until it is stored."""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
import time
from typing import TYPE_CHECKING, Any

from tests.data.python.client_oauth import (
    LIMIT,
    Adapter,
    AsyncAdapter,
    AsyncResponse,
    Caller,
    failure_line,
    started,
    watched,
)
from tests.data.python.client_oauth_refresh import (
    _ROTATED,
    _TOKEN,
    _aoutcome,
    _context,
    _failure,
    _HeldClose,
    _material,
    _outcome,
    _reloaded,
    _reply,
    _Sent,
    _set_line,
    _tokens,
)
from tests.data.python.client_runtime import Exchange, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_THIRD = {**_ROTATED, "access_token": "access-3", "refresh_token": "refresh-3"}


class _Lost:
    """A store step that saves the token set, then raises as if its answer never arrived."""

    def __init__(self, error: BaseException) -> None:
        self.error = error


class _Persisted:
    """A token load and store sharing one record, which a store replaces only at the revision it expects.

    Loads answer from `reads` first, then with the record. Each store follows its script entry: None saves, an exception
    raises instead, and `_Lost` saves before raising. The store with the held index, or every store, waits for the gate.
    """

    def __init__(  # noqa: PLR0913
        self,
        errors: ModuleType,
        record: Any = None,
        *script: object,
        reads: tuple[object, ...] = (),
        gate: threading.Event | None = None,
        held: int | None = None,
    ) -> None:
        self.errors = errors
        self.record = record
        self.script = list(script)
        self.reads = list(reads)
        self.gate = gate
        self.held = held
        self.contexts: list[Any] = []
        self.stores: list[tuple[int, int | None, str]] = []
        self.entered = threading.Event()

    def load(self, context: Any) -> Any:
        self.contexts.append(context)
        if isinstance(answer := self.reads.pop(0) if self.reads else self.record, BaseException):
            raise answer
        return answer

    def store(self, token_set: Any, *, expected_revision: int | None, context: Any) -> None:
        if self._entered(token_set, expected_revision, context) and self.gate is not None:
            self.gate.wait(LIMIT)
        self._stored(token_set, expected_revision)

    def _entered(self, token_set: Any, expected_revision: int | None, context: Any) -> bool:
        """Record a store and return whether it is the held one."""
        self.contexts.append(context)
        self.stores.append((token_set.revision, expected_revision, context.purpose))
        self.entered.set()
        return self.held in {None, len(self.stores) - 1}

    def _stored(self, token_set: Any, expected_revision: int | None) -> None:
        if isinstance(step := self.script.pop(0) if self.script else None, BaseException):
            raise step
        if (record := self.record) != token_set:
            if (observed := None if record is None else record.revision) != expected_revision:
                raise self.errors.AuthTokenStoreConflictError(observed_revision=observed)
            self.record = token_set
        if isinstance(step, _Lost):
            raise step.error


class _AsyncPersisted(_Persisted):
    """The asyncio counterpart, whose held store first sleeps for the delay."""

    def __init__(
        self, errors: ModuleType, record: Any = None, *script: object, delay: float = 0, held: int | None = None
    ) -> None:
        super().__init__(errors, record, *script, held=held)
        self.delay = delay

    async def load(self, context: Any) -> Any:  # ty: ignore[invalid-method-override]
        return super().load(context)

    async def store(self, token_set: Any, *, expected_revision: int | None, context: Any) -> None:  # ty: ignore[invalid-method-override]
        if self._entered(token_set, expected_revision, context):
            await asyncio.sleep(self.delay)
        self._stored(token_set, expected_revision)


def _family(auth: ModuleType, persisted: _Persisted, adapter: object, **settings: Any) -> Any:
    return auth.RefreshTokenProvider(
        _TOKEN,
        client_id="c",
        load=persisted,
        store=persisted,
        client_auth_method="none",
        token_transport=adapter,
        **settings,
    )


def _kept(persisted: _Persisted) -> str:
    return f"stores={persisted.stores} record={_set_line(persisted.record)}"


def _after_work(call: Callable[[], object]) -> str:
    """Report an explicit operation once the family's late work returned, which refuses the operation until then."""
    deadline = time.monotonic() + LIMIT
    while "state=EXCHANGING" in (line := _reloaded(call)) and time.monotonic() < deadline:
        time.sleep(0.01)
    return line


async def _areloaded(call: Callable[[], Any]) -> str:
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001
        return failure_line(error)
    return _set_line(result)


def _store_configuration(auth: ModuleType, errors: ModuleType, transports: ModuleType, lines: list[str]) -> None:
    """Refuse a store without a load and stores of the other mode, and a retry with nothing pending."""
    provider, async_provider = auth.RefreshTokenProvider, auth.AsyncRefreshTokenProvider
    for label, call in (
        (
            "store without a load",
            lambda: provider(
                _TOKEN, client_id="c", token_set=_tokens(auth), store=_Persisted(errors), client_auth_method="none"
            ),
        ),
        (
            "async store of a sync provider",
            lambda: provider(
                _TOKEN, client_id="c", load=_Persisted(errors), store=_AsyncPersisted(errors), client_auth_method="none"
            ),
        ),
        (
            "sync store of an async provider",
            lambda: async_provider(
                _TOKEN, client_id="c", load=_AsyncPersisted(errors), store=_Persisted(errors), client_auth_method="none"
            ),
        ),
        (
            "async store without a load",
            lambda: async_provider(_TOKEN, client_id="c", store=_AsyncPersisted(errors), client_auth_method="none"),
        ),
    ):
        lines.append(f"  {label} = {_outcome(call)}")
    persisted = _Persisted(errors, _tokens(auth, revision=1))
    with _family(auth, persisted, Adapter(transports)) as family:
        lines.append(f"  retry before the first acquisition = {_reloaded(family.retry_store)}")
        family.get(_context(auth))
        lines.append(f"  retry with nothing pending = {_reloaded(family.retry_store)} {_kept(persisted)}")
        for label, expected in (("a negative revision", -1), ("a revision of another type", True)):
            lines.append(
                f"  retry expecting {label} ="
                f" {_reloaded(lambda expected=expected: family.retry_store(expected_revision=expected))}"
            )
    lines.append(f"  retry once closed = {_reloaded(family.retry_store)}")


def _stores(auth: ModuleType, errors: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Store each refreshed token set once before it becomes current, expecting the stored revision last confirmed."""
    persisted = _Persisted(errors)
    adapter = _Sent(transports, _reply(responses, _ROTATED), _reply(responses, _THIRD))
    with _family(auth, persisted, adapter, token_set=_tokens(auth)) as family:
        current = family.get(_context(auth))
        lines.append(f"  constructor token set with nothing stored = {_material(current)} {_kept(persisted)}")
        for label in ("first refresh", "second refresh"):
            family.invalidate(current.version)
            current = family.get(_context(auth))
            lines.append(f"  {label} = {_material(current)} {_kept(persisted)}")
        context = persisted.contexts[-1]
        lines.append(
            f"    purposes={[each.purpose for each in persisted.contexts]} sent={adapter.refresh_tokens}"
            f" same cache key={context.cache_key == persisted.contexts[0].cache_key}"
            f" receipt={family.refresh_snapshot(context.session_id)}"
        )
    for label, stored, initial in (
        ("refresh of a loaded token set", _tokens(auth, "stored", "refresh-4", revision=4, minutes=-1), None),
        (
            "refresh beside an older stored token set",
            _tokens(auth, "old", "refresh-0", revision=3),
            _tokens(auth, revision=5, minutes=-1),
        ),
    ):
        persisted = _Persisted(errors, stored)
        adapter = _Sent(transports, _reply(responses, _ROTATED))
        with _family(auth, persisted, adapter, token_set=initial) as family:
            lines.append(
                f"  {label} = {_outcome(lambda family=family: family.get(_context(auth)))} {_kept(persisted)}"
                f" sent={adapter.refresh_tokens}"
            )
    for label, script in (("", ()), (" into a failing store", (RuntimeError("store"),))):
        persisted = _Persisted(errors, None, *script)
        adapter = _Sent(transports, _reply(responses, {**_ROTATED, "scope": "read"}))
        with _family(auth, persisted, adapter, token_set=_tokens(auth, minutes=-1, scopes=("read", "write"))) as family:
            lines.append(
                f"  write caller of a refresh granting read{label} ="
                f" {_outcome(lambda family=family: family.get(_context(auth, 'write')))}"
            )
            if script:
                lines.append(f"    retry = {_reloaded(family.retry_store)}")
                lines.append(f"    write caller = {_outcome(lambda family=family: family.get(_context(auth, 'write')))}")
            lines.append(
                f"    read caller = {_outcome(lambda family=family: family.get(_context(auth, 'read')))} {_kept(persisted)}"
                f" sent={adapter.refresh_tokens}"
            )


def _store_failures(
    auth: ModuleType, errors: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    """Keep a refreshed token set whose store failed until `retry_store` stores it, without another token request."""
    kept = {key: value for key, value in _ROTATED.items() if key != "refresh_token"}
    for label, step, reply in (
        ("store failure", RuntimeError("store"), _ROTATED),
        ("store conflict", errors.AuthTokenStoreConflictError(observed_revision=4), _ROTATED),
        ("store failing once it saved", _Lost(RuntimeError("lost")), _ROTATED),
        ("store interrupted", KeyboardInterrupt(), _ROTATED),
        ("store failure of a refresh keeping its refresh token", RuntimeError("store"), kept),
    ):
        persisted = _Persisted(errors, None, step)
        adapter = _Sent(transports, _reply(responses, reply), _reply(responses, _THIRD))
        with _family(auth, persisted, adapter, token_set=_tokens(auth, revision=1, minutes=-1)) as family:
            lines.append(f"  {label} = {_outcome(lambda family=family: family.get(_context(auth)))}")
            lines.append(
                f"    later get = {_outcome(lambda family=family: family.get(_context(auth)))}"
                f" sent={adapter.refresh_tokens} receipt={family.refresh_snapshot(persisted.contexts[-1].session_id)}"
            )
            lines.append(
                f"    retry = {_reloaded(family.retry_store)} {_kept(persisted)}"
                f" receipt={family.refresh_snapshot(persisted.contexts[-1].session_id)}"
            )
            family.invalidate(family.get(_context(auth)).version)
            lines.append(
                f"    refresh after the retry = {_outcome(lambda family=family: family.get(_context(auth)))}"
                f" sent={adapter.refresh_tokens}"
            )
    persisted = _Persisted(errors, None, RuntimeError("store"))
    adapter = _Sent(transports, _reply(responses, _ROTATED))
    with _family(auth, persisted, adapter, token_set=_tokens(auth, revision=1, minutes=-1)) as family:
        _failure(lambda: family.get(_context(auth)))
        for label, call in (
            ("reload while a store is pending", family.reload_token_set),
            (
                "replace at the pending revision",
                lambda: family.replace_token_set(_tokens(auth, "access-9", "refresh-9", revision=2)),
            ),
            (
                "replace bringing back the spent refresh token",
                lambda: family.replace_token_set(_tokens(auth, "access-9", "refresh-1", revision=3)),
            ),
            (
                "persisted replacement of the pending token set",
                lambda: family.replace_token_set(_tokens(auth, "access-8", "refresh-8", revision=3)),
            ),
            ("retry once replaced", family.retry_store),
        ):
            lines.append(f"  {label} = {_reloaded(call)}")
        lines.append(
            f"    get = {_outcome(lambda: family.get(_context(auth)))} {_kept(persisted)} sent={adapter.refresh_tokens}"
        )
    persisted = _Persisted(errors, None, RuntimeError("store"))
    adapter = _Sent(transports, _reply(responses, _ROTATED))
    with _family(auth, persisted, adapter, token_set=_tokens(auth, revision=1, minutes=-1)) as family:
        _failure(lambda: family.get(_context(auth)))
        replaced = _tokens(auth, "access-9", "refresh-9", revision=3)
        lines.append(
            f"  replacement of a pending token set without persisting ="
            f" {_reloaded(lambda: family.replace_token_set(replaced, persist=False))}"
        )
        lines.append(f"    get = {_outcome(lambda: family.get(_context(auth)))} {_kept(persisted)}")


def _replacements(
    auth: ModuleType, errors: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    """Store a replacing token set before it becomes current, expecting the confirmed revision, unless not persisted."""
    replaced = _tokens(auth, "replaced", "refresh-9", revision=5)
    for label, persisted, retries in (
        ("persisted replacement after a failed initial load", _Persisted(errors, reads=(RuntimeError("load"),)), ()),
        (
            "persisted replacement beside another stored token set",
            _Persisted(errors, _tokens(auth, "other", "refresh-7", revision=3), reads=(RuntimeError("load"),)),
            ({}, {"expected_revision": 5}, {"expected_revision": 3}),
        ),
        ("persisted replacement of a loaded token set", _Persisted(errors, _tokens(auth, revision=2)), ()),
        (
            "persisted replacement with a failing store",
            _Persisted(errors, _tokens(auth, revision=2), RuntimeError("store"), RuntimeError("store")),
            ({}, {}),
        ),
    ):
        with _family(auth, persisted, _Sent(transports)) as family:
            _failure(lambda family=family: family.get(_context(auth)))
            lines.append(f"  {label} = {_reloaded(lambda family=family: family.replace_token_set(replaced))}")
            lines.append(
                f"    get = {_outcome(lambda family=family: family.get(_context(auth)))} {_kept(persisted)}"
                f" loads={sum(context.purpose == 'initial_load' for context in persisted.contexts)}"
            )
            for arguments in retries:
                lines.append(
                    f"    retry {arguments} ="
                    f" {_reloaded(lambda family=family, arguments=arguments: family.retry_store(**arguments))}"
                    f" {_kept(persisted)}"
                )
                lines.append(f"    get = {_outcome(lambda family=family: family.get(_context(auth)))}")
    persisted = _Persisted(errors, _tokens(auth, revision=2))
    adapter = _Sent(transports, _reply(responses, _ROTATED))
    with _family(auth, persisted, adapter) as family:
        family.get(_context(auth))
        lines.append(
            f"  replacement not persisted = {_reloaded(lambda: family.replace_token_set(replaced, persist=False))}"
            f" {_kept(persisted)}"
        )
        family.invalidate(family.get(_context(auth)).version)
        lines.append(
            f"    refresh = {_outcome(lambda: family.get(_context(auth)))} {_kept(persisted)} sent={adapter.refresh_tokens}"
        )
    persisted = _Persisted(errors, _tokens(auth, revision=2))
    adapter = _Sent(transports, _reply(responses, _ROTATED))
    with _family(auth, persisted, adapter) as family:
        family.get(_context(auth))
        expired = _tokens(auth, "replaced", "refresh-9", revision=5, minutes=-1)
        lines.append(
            f"  persisted replacement with an expired access token = "
            f"{_reloaded(lambda: family.replace_token_set(expired))} {_kept(persisted)}"
        )
        lines.append(
            f"    get = {_outcome(lambda: family.get(_context(auth)))} {_kept(persisted)} sent={adapter.refresh_tokens}"
        )


def _store_concurrency(  # noqa: PLR0915
    auth: ModuleType,
    errors: ModuleType,
    options: ModuleType,
    transports: ModuleType,
    responses: ModuleType,
    lines: list[str],
) -> None:
    """Join a running store, refuse explicit operations meanwhile, share one retry, and close or expire during a store."""
    gate = threading.Event()
    persisted = _Persisted(errors, gate=gate)
    adapter = _Sent(transports, _reply(responses, _ROTATED))
    with _family(
        auth,
        persisted,
        adapter,
        token_set=_tokens(auth, minutes=-1),
        options=auth.OAuthProviderOptions(max_concurrent_refreshes=4),
    ) as family:
        starter = started(lambda: family.get(_context(auth)), persisted.entered, _outcome)
        token = watched(options)
        joiner = started(lambda: family.get(_context(auth, cancel_token=token)), token.checked, _outcome)
        for label, call in (
            ("retry during a refresh", family.retry_store),
            (
                "replace during a refresh",
                lambda: family.replace_token_set(_tokens(auth, "access-9", "refresh-9", revision=5)),
            ),
        ):
            lines.append(f"  {label} = {_reloaded(call)}")
        gate.set()
        starter.join(LIMIT)
        joiner.join(LIMIT)
        lines.append(
            f"  get joining a store = {joiner.line} after {starter.line} {_kept(persisted)} sent={adapter.refresh_tokens}"
        )
    gate = threading.Event()
    persisted = _Persisted(errors, None, RuntimeError("store"), gate=gate, held=1)
    adapter = _Sent(transports, _reply(responses, _ROTATED))
    with _family(
        auth, persisted, adapter, token_set=_tokens(auth, minutes=-1), options=auth.OAuthProviderOptions(max_waiters=2)
    ) as family:
        _failure(lambda: family.get(_context(auth)))
        persisted.entered.clear()
        first = started(family.retry_store, persisted.entered, _reloaded)
        lines.append(
            f"  retry expecting another revision during a retry ="
            f" {_reloaded(lambda: family.retry_store(expected_revision=0))}"
        )

        def opening(call: Callable[[], object]) -> str:
            if "limit_kind=waiters" in (line := _reloaded(call)):
                gate.set()
            return line

        callers = [
            Caller(family.retry_store, opening),
            Caller(lambda: family.retry_store(expected_revision=None), opening),
        ]
        for caller in callers:
            caller.start()
        for caller in (first, *callers):
            caller.join(LIMIT)
        lines.append(
            f"  concurrent retries = {first.line} / {sorted(caller.line for caller in callers)} {_kept(persisted)}"
        )
    gate = threading.Event()
    persisted = _Persisted(errors, gate=gate)
    family = _family(auth, persisted, _Sent(transports, _reply(responses, _ROTATED)), token_set=_tokens(auth, minutes=-1))
    starter = started(lambda: family.get(_context(auth)), persisted.entered, _outcome)
    released = family.request_close()
    lines.append(f"  retry while closing = {_reloaded(family.retry_store)}")
    gate.set()
    starter.join(LIMIT)
    lines.append(
        f"  provider closing while its store runs = {starter.line} released={released.result(LIMIT)} {_kept(persisted)}"
    )
    gate = threading.Event()
    persisted = _Persisted(errors, gate=gate)
    adapter = _Sent(transports, _reply(responses, _ROTATED))
    with _family(
        auth,
        persisted,
        adapter,
        token_set=_tokens(auth, minutes=-1),
        options=auth.OAuthProviderOptions(refresh_timeout=0.5),
    ) as family:
        lines.append(f"  store outliving its session = {_outcome(lambda: family.get(_context(auth)))}")
        gate.set()
        lines.append(
            f"    retry once the store returned = {_after_work(family.retry_store)} {_kept(persisted)}"
            f" sent={adapter.refresh_tokens}"
        )
    gate = threading.Event()
    persisted = _Persisted(errors)
    adapter = _Sent(transports, _HeldClose(responses, 200, _ROTATED, gate))
    with _family(
        auth,
        persisted,
        adapter,
        token_set=_tokens(auth, minutes=-1),
        options=auth.OAuthProviderOptions(refresh_timeout=0.5),
    ) as family:
        lines.append(f"  refresh answered once its session ended = {_outcome(lambda: family.get(_context(auth)))}")
        replaced = _tokens(auth, "replaced", "refresh-9", revision=5)
        lines.append(f"    persisted replacement while its work runs = {_reloaded(lambda: family.replace_token_set(replaced))}")
        lines.append(
            f"    replacement not persisted meanwhile ="
            f" {_reloaded(lambda: family.replace_token_set(replaced, persist=False))}"
        )
        gate.set()
        lines.append(f"    retry once the refresh returned = {_after_work(family.retry_store)} {_kept(persisted)}")


def _store_calls(
    package: ModuleType,
    auth: ModuleType,
    errors: ModuleType,
    options: ModuleType,
    transports: ModuleType,
    responses: ModuleType,
    lines: list[str],
) -> None:
    """Fail a call without sending it while a store is pending, and send it once `retry_store` stored the token set."""
    exchange = Exchange(lines)
    exchange.respond(raw_response(200, b"ok", "application/octet-stream"))
    persisted = _Persisted(errors, None, RuntimeError("store"))
    family = _family(auth, persisted, _Sent(transports, _reply(responses, _ROTATED)), token_set=_tokens(auth, minutes=-1))
    with (
        family,
        exchange.client() as native,
        package.Client(http_client=native, options=options.ClientOptions(auth=auth.AuthConfig({"oauth": family}))) as api,
    ):
        record(lines, "call refreshing into a failing store", api.auth.with_response.oauth_empty)
        family.retry_store()
        record(lines, "call once the retry stored the token set", api.auth.with_response.oauth_empty)


async def _async_stores(
    auth: ModuleType, errors: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    """Store, retry, and replace in an asyncio family, each store within its session."""

    def provider(persisted: _AsyncPersisted, *replies: object, **settings: Any) -> Any:
        return auth.AsyncRefreshTokenProvider(
            _TOKEN,
            client_id="c",
            token_set=_tokens(auth, minutes=-1),
            load=persisted,
            store=persisted,
            client_auth_method="none",
            token_transport=AsyncAdapter(transports, *replies),
            **settings,
        )

    rotated = AsyncResponse(responses, 200, json.dumps(_ROTATED).encode())
    persisted = _AsyncPersisted(errors, None, RuntimeError("store"))
    async with provider(persisted, rotated) as family:
        lines.append(f"  async store failure = {await _aoutcome(lambda: family.get(_context(auth)))}")
        retries = await asyncio.gather(
            _areloaded(family.retry_store),
            _areloaded(lambda: family.retry_store(expected_revision=None)),
            _aoutcome(lambda: family.get(_context(auth))),
        )
        lines.append(f"    concurrent retries and a get = {retries} {_kept(persisted)}")
        replaced = _tokens(auth, "replaced", "refresh-9", revision=5)
        lines.append(
            f"    persisted replacement = {await _areloaded(lambda: family.replace_token_set(replaced))} {_kept(persisted)}"
        )
        lines.append(f"    retry with nothing pending = {await _areloaded(family.retry_store)}")
    persisted = _AsyncPersisted(errors, delay=1.5)
    async with provider(
        persisted,
        AsyncResponse(responses, 200, json.dumps(_ROTATED).encode()),
        options=auth.OAuthProviderOptions(refresh_timeout=0.5),
    ) as family:
        lines.append(
            f"  async store outliving its session = {await _aoutcome(lambda: family.get(_context(auth)))}"
            f" {_kept(persisted)}"
        )


def oauth_refresh_store(package: ModuleType, lines: list[str]) -> None:
    """Exercise storing, retrying, and replacing the token sets of refresh token families in both execution modes."""
    auth, options, transports, responses, errors = (
        importlib.import_module(f"{package.__name__}.{name}")
        for name in ("auth", "options", "transports", "responses", "errors")
    )
    _store_configuration(auth, errors, transports, lines)
    _stores(auth, errors, transports, responses, lines)
    _store_failures(auth, errors, transports, responses, lines)
    _replacements(auth, errors, transports, responses, lines)
    _store_concurrency(auth, errors, options, transports, responses, lines)
    _store_calls(package, auth, errors, options, transports, responses, lines)

    async def flows() -> None:
        await _async_stores(auth, errors, transports, responses, lines)

    run(flows)
