"""Exercise whole-auth option replacement and accepted provider identities over real TLS."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from tests.data.python.client_runtime import arecord, record, run
from tests.data.python.fixture_native import NativeFixture

if TYPE_CHECKING:
    from types import ModuleType


class _Provider:
    def __init__(self, material: object) -> None:
        self.material = material
        self.calls = 0
        self.closes = 0

    def get(self, context: object) -> object:
        del context
        self.calls += 1
        return self.material

    def close(self) -> None:
        self.closes += 1


class _AsyncProvider:
    def __init__(self, material: object) -> None:
        self.material = material
        self.calls = 0
        self.closes = 0

    async def get(self, context: object) -> object:
        del context
        self.calls += 1
        return self.material

    async def aclose(self) -> None:
        self.closes += 1


class _Signer:
    def __init__(self, capabilities: object, fields: object) -> None:
        self.capabilities = capabilities
        self.fields = fields
        self.calls = 0

    def sign(self, request: object) -> object:
        del request
        self.calls += 1
        return self.fields


class _AsyncSigner:
    def __init__(self, capabilities: object, fields: object) -> None:
        self.capabilities = capabilities
        self.fields = fields
        self.calls = 0

    async def sign(self, request: object) -> object:
        del request
        self.calls += 1
        return self.fields


def _layered(
    package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str], *, asynchronous: bool, http2: bool
) -> None:
    mode = "async" if asynchronous else "sync"
    protocol = "h2" if http2 else "h1"
    server = NativeFixture(http2=http2)
    server.content_type = b"application/octet-stream"
    provider_type = _AsyncProvider if asynchronous else _Provider
    signer_type = _AsyncSigner if asynchronous else _Signer
    root = provider_type(auth.BearerCredential(auth.AccessToken("root-token"), auth.TokenVersion()))
    root_key = provider_type(auth.ApiKeyCredential("root-key"))
    view_key = provider_type(auth.ApiKeyCredential("view-key"))
    view_token = provider_type(auth.BearerCredential(auth.AccessToken("view-token"), auth.TokenVersion()))
    call_key = provider_type(auth.ApiKeyCredential("call-key"))
    unused = provider_type(auth.BasicCredential("unused", "unused"))
    providers = (root, root_key, view_key, view_token, call_key, unused)
    signer = signer_type(
        auth.SignerCapabilities((server.url,), ("X-Layer-Signature",), ()),
        auth.SignatureFields((("X-Layer-Signature", "root-signature"),), ()),
    )
    root_config = auth.AuthConfig(
        {
            "bearer": root,
            "bearer_alias": root,
            "header_key": root_key,
        },
        selection=1,
        signers=(signer,),
        allowed_origins=(server.url,),
    )
    view_config = auth.AuthConfig(
        {
            "header_key": view_key,
            "bearer": view_token,
            "basic": unused,
        },
    )
    call_config = auth.AuthConfig({"header_key": call_key})
    client_options = options.ClientOptions(
        base_url=server.url,
        transport=options.TransportOptions(ssl_context=server.verify, http2=http2),
        auth=root_config,
    )
    try:
        if asynchronous:

            async def asynchronous_calls() -> None:
                async with package.AsyncClient(options=client_options) as client:
                    await arecord(lines, f"{mode} {protocol} root selection", client.auth.or_auth)
                    inherited = client.with_options(options.RequestOptions())
                    await arecord(lines, "inherited view", inherited.auth.or_auth)
                    await arecord(
                        lines, "inherited request", lambda: inherited.auth.or_auth(options=options.RequestOptions())
                    )
                    view = inherited.with_options(options.RequestOptions(auth=view_config))
                    await arecord(lines, "replacement resets selection and signer", view.auth.or_auth)
                    await arecord(
                        lines,
                        "request replaces credentials",
                        lambda: view.auth.or_auth(options=options.RequestOptions(auth=call_config)),
                    )
                    await arecord(
                        lines,
                        "request does not merge credentials",
                        lambda: view.auth.bearer(options=options.RequestOptions(auth=call_config)),
                    )
                    await arecord(
                        lines,
                        "request clears on anonymous",
                        lambda: view.auth.anonymous(options=options.RequestOptions(auth=None)),
                    )
                    await arecord(
                        lines,
                        "request clear rejects required",
                        lambda: view.auth.or_auth(options=options.RequestOptions(auth=None)),
                    )
                    cleared = view.with_options(options.RequestOptions(auth=None))
                    await arecord(lines, "view clear selects anonymity", cleared.auth.optional_token_first)
                    child = cleared.with_options(options.RequestOptions())
                    await arecord(lines, "cleared view inherits None", child.auth.empty_security)
                    await arecord(lines, "cleared view rejects required", child.auth.inherited_auth)
                    lines.append(
                        f"  {mode} {protocol} before root close={tuple(provider.closes for provider in providers)}"
                    )

            run(asynchronous_calls)
        else:
            with package.Client(options=client_options) as client:
                record(lines, f"{mode} {protocol} root selection", client.auth.or_auth)
                inherited = client.with_options(options.RequestOptions())
                record(lines, "inherited view", inherited.auth.or_auth)
                record(lines, "inherited request", lambda: inherited.auth.or_auth(options=options.RequestOptions()))
                view = inherited.with_options(options.RequestOptions(auth=view_config))
                record(lines, "replacement resets selection and signer", view.auth.or_auth)
                record(
                    lines,
                    "request replaces credentials",
                    lambda: view.auth.or_auth(options=options.RequestOptions(auth=call_config)),
                )
                record(
                    lines,
                    "request does not merge credentials",
                    lambda: view.auth.bearer(options=options.RequestOptions(auth=call_config)),
                )
                record(
                    lines,
                    "request clears on anonymous",
                    lambda: view.auth.anonymous(options=options.RequestOptions(auth=None)),
                )
                record(
                    lines,
                    "request clear rejects required",
                    lambda: view.auth.or_auth(options=options.RequestOptions(auth=None)),
                )
                cleared = view.with_options(options.RequestOptions(auth=None))
                record(lines, "view clear selects anonymity", cleared.auth.optional_token_first)
                child = cleared.with_options(options.RequestOptions())
                record(lines, "cleared view inherits None", child.auth.empty_security)
                record(lines, "cleared view rejects required", child.auth.inherited_auth)
                lines.append(
                    f"  {mode} {protocol} before root close={tuple(provider.closes for provider in providers)}"
                )
        for (_, path, body), fields in zip(server.requests, server.request_headers, strict=True):
            managed = tuple(
                (name.lower(), value)
                for name, value in fields
                if name.lower() in {b"authorization", b"x-api-key", b"x-layer-signature"}
            )
            lines.append(f"  {mode} {protocol} wire path={path!r} body={body!r} auth={managed!r}")
        lines.append(
            f"  {mode} {protocol} calls={tuple(provider.calls for provider in providers)} signer calls={signer.calls} closes={tuple(provider.closes for provider in providers)}"
        )
        lines.append(f"  {mode} {protocol} total sends={len(server.requests)}")
    finally:
        server.stop()


def auth_options(package: ModuleType, lines: list[str]) -> None:
    """Report client, view and request auth layering through public sync and async clients."""
    auth = importlib.import_module(f"{package.__name__}.auth")
    options = importlib.import_module(f"{package.__name__}.options")
    for asynchronous in (False, True):
        for http2 in (False, True):
            _layered(package, auth, options, lines, asynchronous=asynchronous, http2=http2)
