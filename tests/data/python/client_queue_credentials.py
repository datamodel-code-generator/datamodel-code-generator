"""Observe queue credential refusals and persisted metadata through generated public APIs and local TLS."""

from __future__ import annotations

import inspect
import json
from datetime import timedelta
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx2

from tests.data.python.client_queues import _AsyncStore, _Static, _Store, _Time
from tests.data.python.client_runtime import Exchange, raw_response, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_INPUT = Path(__file__).parents[1] / "generation_platform/client/queue-credential-inputs.json"


class _Provider(_Static):
    """Count actual credential acquisition through the public provider contract."""

    def __init__(self, auth: ModuleType, asynchronous: bool) -> None:
        super().__init__(auth, "current-secret")
        self.calls = 0
        self.during: Callable[[], None] | None = None
        if asynchronous:
            self.get = self.aget

    def get(self, context: object) -> object:
        self.calls += 1
        if self.during is not None:
            self.during()
        return super().get(context)

    async def aget(self, context: object) -> object:
        self.calls += 1
        if self.during is not None:
            self.during()
        return super().get(context)


class _Signer:
    """Supply declared secret positions through the public signer contract."""

    def __init__(self, auth: ModuleType, asynchronous: bool) -> None:
        self.auth = auth
        self.calls = 0
        self.capabilities = auth.SignerCapabilities(
            allowed_origins=("https://api.example.com",),
            managed_headers=("X-Signature",),
            managed_query=("signed",),
            requires_body_digest=False,
        )
        if asynchronous:
            self.sign = self.asign

    def sign(self, context: object) -> object:
        del context
        self.calls += 1
        return self.auth.SignatureFields(headers=(("X-Signature", "current-signature"),), query=())

    async def asign(self, context: object) -> object:
        return self.sign_sync(context)

    def sign_sync(self, context: object) -> object:
        return _Signer.sign(self, context)


class _Events:
    """Record public call identities and cancel only after a real response has completed decoding."""

    def __init__(self, token: Any = None, stop: bool = False, fault: bool = False, stop_at: str = "call_end") -> None:
        self.token, self.stop, self.fault, self.stop_at = token, stop, fault, stop_at
        self.call_id: str | None = None

    def on_event(self, event: Any) -> None:
        self.call_id = event.call_id
        if self.fault and event.name == "response_headers":
            msg = "public hook fault after headers"
            raise RuntimeError(msg)
        if self.stop and event.name == self.stop_at:
            self.token.cancel()


async def _value(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _metadata(lines: list[str], label: str, entry: Any, events: _Events) -> None:
    """Describe a public stored outcome, retaining duplicate ordinary headers and call counters."""
    result = entry.result
    info = None if result is None else result.response
    category = None if result is None else result.category
    lines.append(f"  {label}: state={entry.state} category={category} deliveries={entry.delivery_count}")
    if info is not None:
        counters = tuple(
            getattr(info, name)
            for name in (
                "resource_attempt_count",
                "redirect_count",
                "auth_exchange_count",
                "network_send_count",
                "network_send_budget_used",
                "auth_exchange_budget_used",
                "auth_refresh_pending",
                "wire_send_count",
            )
        )
        lines.extend((
            (
                f"    status={info.status_code} request_id={info.request_id!r} "
                f"call_same={info.call_id == events.call_id} "
                f"counters={counters} refresh_ids={info.auth_refresh_ids}"
            ),
            f"    headers={info.headers.items()}",
        ))
    lines.append(
        f"    outcome_body={hasattr(result, 'body') or hasattr(result, 'data')} "
        f"payload_secret={b'argument-secret' in entry.payload}"
    )


async def _mode(package: ModuleType, lines: list[str], asynchronous: bool) -> None:  # ruff: ignore[too-many-locals, too-many-branches]
    inputs = json.loads(_INPUT.read_text(encoding="utf-8"))
    public = import_module(f"{package.__name__}.protocols")
    options = import_module(f"{package.__name__}.options")
    auth = import_module(f"{package.__name__}.auth")
    codecs = import_module(f"{package.__name__}.types.cases")
    native_type = package.AsyncClient if asynchronous else package.Client
    lines.append("async" if asynchronous else "sync")
    clock, exchange = _Time(), Exchange([])
    native = exchange.async_client() if asynchronous else exchange.client()
    provider, signer = _Provider(auth, asynchronous), _Signer(auth, asynchronous)
    credential = auth.AuthConfig(
        {"bearer": provider}, send_on_anonymous=True, anonymous_schemes=("bearer",), signers=(signer,)
    )

    def client(store: _Store, events: _Events | None = None, **settings: Any) -> Any:
        return native_type(
            http_client=native,
            options=options.ClientOptions(
                protocols=options.ProtocolClientOptions(
                    queue_stores={"outbox": _AsyncStore(store) if asynchronous else store},
                    security=public.ProtocolSecurityContext(credential_partition="tenant-a"),
                ),
                retry=options.RetryOptions(max_retries=0),
                auth=credential,
                clock=options.Clock(monotonic=clock.monotonic, time=clock.time, random=clock.random),
                hooks=() if events is None else (events,),
                **settings,
            ),
        )

    async def close(api: Any) -> None:
        await _value(api.aclose() if asynchronous else api.close())

    for control in inputs["arguments"]:
        operation = control["operation"]
        name, location = (
            ("whole", "querystring")
            if operation == "whole"
            else (
                ("X-Catalog-Key", "header")
                if operation == "header"
                else (
                    ("token", "query")
                    if operation == "direct"
                    else (("language", "query") if operation == "plain" else ("filter", "query"))
                )
            )
        )
        codec = getattr(codecs, f"Get{operation.title()}RequestCodecs").parameter(location=location, name=name)
        argument = codec.from_wire(control["value"])
        argument_name = name.lower().replace("-", "_")
        for restored in (False, True) if control["refused"] else (False,):
            store = _Store(public)
            api = client(store)
            queue = api.protocols.outbox
            before_provider, before_signer, before_sends = provider.calls, signer.calls, len(exchange.lines)
            status = "accepted"
            try:  # ruff: ignore[too-many-statements-in-try-clause]
                if restored:
                    safe = {"public": "tea"} if isinstance(control["value"], dict) else ""
                    seed = {argument_name: codec.from_wire(safe)} if isinstance(safe, dict) else {}
                    receipt = await _value(getattr(queue.operations, operation).enqueue(**seed))
                    entry = await _value(queue.inspect(receipt.entry_id))
                    payload = json.loads(entry.payload)
                    payload["arguments"] = [[control["value"]]]
                    store.tamper(receipt.entry_id, payload=json.dumps(payload).encode())
                    await _value(queue.drain())
                    status = (await _value(queue.inspect(receipt.entry_id))).state
                else:
                    receipt = await _value(getattr(queue.operations, operation).enqueue(**{argument_name: argument}))
                    exchange.respond(raw_response(204))
                    await _value(queue.drain())
                    status = (await _value(queue.inspect(receipt.entry_id))).state
            except Exception as error:  # ruff: ignore[blind-except]
                status = f"{type(error).__name__}/{getattr(error, 'condition', None)}"
            lines.append(
                f"  {control['case']} {'restore' if restored else 'save'}: {status} entries={len(store.entries)} "
                f"providers={provider.calls - before_provider} signers={signer.calls - before_signer} "
                f"sends={(len(exchange.lines) - before_sends) // 2}"
            )
            await close(api)

    for control in inputs["outcomes"]:
        store = _Store(public)
        token = options.CancelToken()
        events = _Events(
            token, control.get("stop", False), control.get("hook_error", False), control.get("stop_at", "call_end")
        )
        api = client(store, events, cancel_token=token)
        queue = api.protocols.outbox
        receipt = await _value(getattr(queue.operations, control.get("operation", "matrix")).enqueue())

        def respond(
            request: httpx2.Request,
            control: dict[str, Any] = control,
            store: _Store = store,
            entry_id: str = receipt.entry_id,
        ) -> httpx2.Response:
            del request
            if control.get("cancel"):
                store.tamper(entry_id, cancel_requested=True)
            body = control.get("body", '{"message":"opaque business data"}').encode()
            return httpx2.Response(control["status"], headers=inputs["headers"], content=body)

        exchange.respond(respond)
        try:
            await _value(queue.drain())
        except Exception as error:  # ruff: ignore[blind-except]
            info = getattr(error, "info", None)
            lines.append(
                f"  {control['case']} raised={type(error).__name__} "
                f"original_secret={info is not None and info.headers.get('Set-Cookie') == 'session=response-secret'}"
            )
        entry = await _value(queue.inspect(receipt.entry_id))
        _metadata(lines, control["case"], entry, events)
        await close(api)

    store = _Store(public)
    events = _Events()
    api = client(store, events)
    exchange.respond(
        lambda _: httpx2.Response(200, headers=inputs["headers"], content=b'{"message":"opaque business data"}')
    )
    response = await _value(api.cases.with_response.get_matrix())
    lines.append(
        f"  ordinary: cookie={response.info.headers.get('Set-Cookie')!r} "
        f"request_id={response.info.request_id!r} business={response.data.message!r}"
    )
    for action in ("retry_unknown", "cancel-leased", "deferred-copy", "missing-alias"):
        receipt = await _value(api.protocols.outbox.operations.matrix.enqueue())
        raw = public.QueueOutcome(
            category="unknown" if action == "retry_unknown" else "retryable", response=response.info
        )
        store.tamper(receipt.entry_id, result=raw, state="delivery_unknown" if action == "retry_unknown" else "pending")
        if action == "retry_unknown":
            await _value(api.protocols.outbox.retry_unknown(receipt.entry_id))
        elif action == "cancel-leased":
            store.claim(now=clock.now(), lease_until=clock.now() + timedelta(seconds=90), limit=100)
            await _value(api.protocols.outbox.cancel(receipt.entry_id))
        elif action == "deferred-copy":
            token = options.CancelToken()

            def during(entry_id: str = receipt.entry_id, raw: Any = raw, token: Any = token) -> None:
                store.tamper(entry_id, result=raw)
                token.cancel()

            provider.during = during
            try:
                await _value(api.protocols.outbox.drain(options=options.RequestOptions(cancel_token=token)))
            except Exception as error:  # ruff: ignore[blind-except]
                lines.append(f"  deferred-copy raised={type(error).__name__}")
        else:
            store.tamper(receipt.entry_id, operation_alias="missing")
            try:
                await _value(api.protocols.outbox.drain())
            except Exception as error:  # ruff: ignore[blind-except]
                lines.append(f"  missing-alias raised={type(error).__name__}")
        provider.during = None
        entry = await _value(api.protocols.outbox.inspect(receipt.entry_id))
        _metadata(lines, action, entry, events)
        store.entries.clear()
    await close(api)
    await _value(native.aclose() if asynchronous else native.close())


def queue_credentials(package: ModuleType, lines: list[str]) -> None:
    """Exercise saving, restoration and settlement in both execution modes."""

    async def scenario() -> None:
        await _mode(package, lines, False)
        await _mode(package, lines, True)

    run(scenario)
