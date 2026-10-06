"""Exercise public auth values, provider primitives and configuration through generated packages."""

from __future__ import annotations

import importlib
import inspect
import os
import subprocess
import sys
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, get_type_hints

from tests.data.python.client_runtime import arecord, record, run

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

    def __repr__(self) -> str:
        msg = "Provider repr must not be read by auth values."
        raise RuntimeError(msg)


class _Signer:
    def __init__(self, capabilities: object, result: object) -> None:
        self.capabilities = capabilities
        self.result = result
        self.calls = 0

    def sign(self, request: object) -> object:
        del request
        self.calls += 1
        return self.result

    def __repr__(self) -> str:
        msg = "Signer repr must not be read by auth values."
        raise RuntimeError(msg)


def _values(auth: ModuleType, options: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    version, foreign = auth.TokenVersion(), auth.TokenVersion()
    lines.append(
        f"  version identity same={version == version} distinct={version != foreign}"
        f" hash entries={len({version: 1, foreign: 2})} fields={tuple(item.name for item in fields(version))}"
        f" slots={not hasattr(version, '__dict__')}"
    )
    scopes = ["write", "read", "read", "Read"]
    token = auth.AccessToken("access-secret", scopes=scopes, audience="audience-secret")
    scopes.append("late")
    unknown = auth.AccessToken("access-secret")
    empty = auth.AccessToken("access-secret", scopes=())
    equivalent = auth.AccessToken("access-secret", scopes=("read", "write", "Read"), audience="audience-secret")
    for expiry in (datetime(2026, 1, 1), datetime(2026, 1, 1, tzinfo=timezone.utc)):
        declared = auth.AccessToken("secret", expires_at=expiry)
        lines.append(f"  token retains expiry={declared.expires_at is expiry} aware={expiry.tzinfo is not None}")
    lines.append(
        f"  scopes unknown={unknown.scopes!r} empty={empty.scopes!r} canonical={token.scopes!r}"
        f" equal={token == equivalent} unknown differs={unknown != empty}"
    )
    deadline = options.Deadline.after(30)
    context = auth.CredentialContext(
        scheme="oauth",
        required_scopes=["write", "read", "read"],
        audience=None,
        origin="https://example.com",
        deadline=deadline,
    )
    lines.append(
        f"  context scopes={context.required_scopes} deadline identity={context.deadline is deadline}"
        f" audience={context.audience!r}"
    )
    record(lines, "context requires keywords", lambda: auth.CredentialContext("scheme", (), None, "origin", None))
    headers = responses.HeadersView((("X-Input", "input-secret"),))
    signing = auth.SigningInput(
        "POST",
        "https://example.com/secret?q=secret",
        "https://example.com",
        b"q=secret",
        headers,
        b"digest-secret",
        1,
        2,
    )
    lines.append(
        f"  signing bytes={signing.query!r} headers identity={signing.headers is headers} indexes={(signing.attempt_index, signing.hop_index)}"
    )
    signed_headers = [["X-Signature", "signature-secret"], ["X-Signature", "second-secret"]]
    signed_query = [["signature", "query-secret"]]
    signature = auth.SignatureFields(signed_headers, signed_query)
    signed_headers[0][1] = "changed"
    signed_query.clear()
    lines.append(f"  signature copied={signature.headers!r} query={signature.query!r}")
    origins, names, query = ["https://example.com"], ["X-Signature"], ["signature"]
    capabilities = auth.SignerCapabilities(origins, names, query, True)
    origins.clear()
    names.clear()
    query.clear()
    lines.append(
        f"  capabilities copied={(capabilities.allowed_origins, capabilities.managed_headers, capabilities.managed_query)} digest={capabilities.requires_body_digest}"
    )
    values = (
        version,
        token,
        auth.ApiKeyCredential("api-secret"),
        auth.BasicCredential("user-secret", "password-secret"),
        auth.BearerCredential(token, version),
        context,
        signing,
        signature,
        capabilities,
    )
    lines.append(f"  secret-free records={tuple(repr(value) for value in values)}")
    for value, name, replacement in (
        (token, "value", "changed"),
        (values[2], "value", "changed"),
        (values[3], "username", "changed"),
        (values[4], "version", foreign),
        (context, "required_scopes", ()),
        (signing, "query", b"changed"),
        (signature, "headers", ()),
        (capabilities, "requires_body_digest", False),
    ):
        previous = getattr(value, name)
        try:
            setattr(value, name, replacement)
        except (AttributeError, TypeError):
            lines.append(f"  immutable {type(value).__name__}.{name} unchanged={getattr(value, name) is previous}")
        else:
            lines.append(f"  mutable {type(value).__name__}.{name}")
    for name, value in (("value", 1), ("token_type", None), ("expires_at", "later"), ("audience", False)):
        record(
            lines,
            f"token invalid {name}",
            lambda name=name, value=value: auth.AccessToken(**{"value": "secret", name: value}),
        )
    record(lines, "API key invalid value", lambda: auth.ApiKeyCredential(1))
    record(lines, "Basic invalid username", lambda: auth.BasicCredential(None, "secret"))
    record(lines, "Basic invalid password", lambda: auth.BasicCredential("user", False))
    record(lines, "bearer invalid token", lambda: auth.BearerCredential("secret", version))
    record(lines, "bearer invalid version", lambda: auth.BearerCredential(token, "version"))
    parameters = {
        "scheme": "oauth",
        "required_scopes": (),
        "audience": None,
        "origin": "https://example.com",
        "deadline": None,
    }
    for name, value in (("scheme", None), ("audience", 1), ("origin", False), ("deadline", 30)):
        record(
            lines,
            f"context invalid {name}",
            lambda name=name, value=value: auth.CredentialContext(**{**parameters, name: value}),
        )
    for name in ("allowed_origins", "managed_headers", "managed_query"):
        for value in ("name", [1]):
            record(
                lines,
                f"capabilities invalid {name} {value!r}",
                lambda name=name, value=value: auth.SignerCapabilities(**{
                    "allowed_origins": (),
                    "managed_headers": (),
                    "managed_query": (),
                    "requires_body_digest": False,
                    name: value,
                }),
            )
    record(lines, "capabilities invalid digest flag", lambda: auth.SignerCapabilities((), (), (), 1))
    for value in (None, "header", [1], [["name"]], [["name", "value", "extra"]], [["name", 1]]):
        record(lines, f"signature invalid pairs {value!r}", lambda value=value: auth.SignatureFields(value, ()))
    record(lines, "signature empty fields", lambda: auth.SignatureFields((), ()))


def _scope_values(auth: ModuleType, lines: list[str]) -> None:
    requirements = {
        "scheme": "oauth",
        "audience": None,
        "origin": "https://example.com",
        "deadline": None,
    }
    invalid = (
        "read",
        {"read"},
        [1],
        [None],
        [""],
        ["read write"],
        ['read"write'],
        ["read\\write"],
        ["read\twrite"],
        ["read\x7fwrite"],
        ["réad"],
    )
    for value in invalid:
        record(lines, f"token scopes invalid {value!r}", lambda value=value: auth.AccessToken("secret", scopes=value))
        record(
            lines,
            f"required scopes invalid {value!r}",
            lambda value=value: auth.CredentialContext(required_scopes=value, **requirements),
        )
    record(
        lines, "known requirements reject None", lambda: auth.CredentialContext(required_scopes=None, **requirements)
    )
    iterator = iter(["read"])
    record(lines, "scopes reject iterator", lambda: auth.AccessToken("secret", scopes=iterator))
    lines.append(f"  rejected iterator untouched={next(iterator)!r}")
    for value in ([], ["read,write", "read", "read"], ["!", "#", "[", "]", "~"]):
        token = auth.AccessToken("secret", scopes=value)
        lines.append(f"  accepted canonical scopes={token.scopes!r}")


def _configuration(auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    provider = _Provider(auth.ApiKeyCredential("secret"))
    signer = _Signer(auth.SignerCapabilities((), ("X-Signature",), (), False), auth.SignatureFields((), ()))
    credentials = {"first": provider, "same-provider": provider, "borrowed": provider}
    signers, origins, schemes = [signer], ["https://example.com"], ["first"]
    config = auth.AuthConfig(credentials, allowed_origins=origins, anonymous_schemes=schemes, signers=signers)
    credentials.clear()
    signers.clear()
    origins.clear()
    schemes.clear()
    lines.append(
        f"  auth copied names={tuple(config.credentials)} origins={config.allowed_origins} anonymous={config.anonymous_schemes}"
    )
    lines.append(
        f"  provider identities={config.credentials['first'] is provider, config.credentials['borrowed'] is provider} signer identity={config.signers[0] is signer} callbacks={(provider.calls, provider.closes, signer.calls)}"
    )
    record(lines, "auth safe repr", lambda: repr(config))
    record(lines, "auth immutable mapping", lambda: config.credentials.__setitem__("changed", provider))
    for value, name, replacement in ((config, "selection", 0), (config, "credentials", {})):
        previous = getattr(value, name)
        try:
            setattr(value, name, replacement)
        except (AttributeError, TypeError):
            lines.append(f"  immutable {type(value).__name__}.{name} unchanged={getattr(value, name) is previous}")
        else:
            lines.append(f"  mutable {type(value).__name__}.{name}")
    for value in (None, [], {1: provider}, {"scheme": 1}):
        record(lines, f"auth invalid credentials {type(value).__name__}", lambda value=value: auth.AuthConfig(value))
    for name in ("allowed_origins", "anonymous_schemes", "signers"):
        for label, value in (("text", "value"), ("member", [1])):
            record(
                lines,
                f"auth invalid {name} {label}",
                lambda name=name, value=value: auth.AuthConfig({}, **{name: value}),
            )
    for value in (None, True, -1, 0.5, "1"):
        record(lines, f"auth invalid selection {value!r}", lambda value=value: auth.AuthConfig({}, selection=value))
    record(lines, "auth invalid anonymous bool", lambda: auth.AuthConfig({}, send_on_anonymous=1))
    record(lines, "auth controls keyword only", lambda: auth.AuthConfig({}, 0))
    explicit = auth.AuthConfig({}, selection=0, send_on_anonymous=True)
    lines.append(
        f"  auth default selection={config.selection is options.UNSET} explicit={(explicit.selection, explicit.send_on_anonymous)}"
    )
    for owner in (options.ClientOptions, options.RequestOptions):
        lines.append(
            f"  {owner.__name__} auth omitted={owner().auth is options.UNSET} identity={owner(auth=config).auth is config} clear={owner(auth=None).auth is None}"
        )
        record(lines, f"{owner.__name__} invalid auth", lambda owner=owner: owner(auth={}))


def _providers(auth: ModuleType, lines: list[str]) -> None:
    context = auth.CredentialContext(
        scheme="bearer",
        required_scopes=(),
        audience=None,
        origin="https://example.com",
        deadline=None,
    )
    materials = (
        auth.ApiKeyCredential("api-secret"),
        auth.BasicCredential("user", "password"),
        auth.BearerCredential(auth.AccessToken("token"), auth.TokenVersion()),
    )
    for material in materials:
        provider = auth.StaticCredentialProvider(material)
        lines.append(
            f"  static {type(material).__name__} identity={provider.get(context) is material and provider.get(context) is material}"
        )
    record(lines, "static invalid material", lambda: auth.StaticCredentialProvider("secret"))
    record(lines, "static token invalid material", lambda: auth.StaticTokenProvider("secret"))
    for scopes in (None, (), ("read",)):
        token = auth.AccessToken("token", scopes=scopes)
        provider = auth.StaticTokenProvider(token)
        first, second = provider.get(context), provider.get(context)
        lines.append(
            f"  static token identity={first is second} token={first.token is token} version={first.version is second.version} scopes={first.token.scopes!r} refresh={hasattr(provider, 'refresh')}"
        )

    async def asynchronous() -> None:
        for material in materials:
            provider = auth.AsyncStaticCredentialProvider(material)
            lines.append(
                f"  async static {type(material).__name__} identity={await provider.get(context) is material and await provider.get(context) is material}"
            )
        for scopes in (None, (), ("read",)):
            token = auth.AccessToken("token", scopes=scopes)
            provider = auth.AsyncStaticTokenProvider(token)
            first, second = await provider.get(context), await provider.get(context)
            lines.append(
                f"  async static token identity={first is second} token={first.token is token} version={first.version is second.version} scopes={first.token.scopes!r} refresh={hasattr(provider, 'refresh')}"
            )

    run(asynchronous)
    variable = "DCG_GENERATED_AUTH_VALUES_FIXTURE"
    previous = os.environ.pop(variable, None)
    try:
        for owner in (auth.EnvironmentCredentialProvider, auth.AsyncEnvironmentCredentialProvider):
            for value in (None, "", "bad=name", "bad\0name"):
                record(
                    lines, f"{owner.__name__} invalid variable {value!r}", lambda owner=owner, value=value: owner(value)
                )
            for kind in ("basic", None):
                record(
                    lines,
                    f"{owner.__name__} invalid kind {kind!r}",
                    lambda owner=owner, kind=kind: owner(variable, kind=kind),
                )
        api = auth.EnvironmentCredentialProvider(variable)
        bearer = auth.EnvironmentCredentialProvider(variable, kind="bearer")
        lines.append("  missing environment construction succeeded")
        record(lines, "missing environment get", lambda: api.get(context))
        for value in ("first", "second", ""):
            os.environ[variable] = value
            material, token = api.get(context), bearer.get(context)
            lines.append(
                f"  environment current={(material.value, token.token.value)} grants={token.token.scopes!r} expiry={token.token.expires_at!r} version={type(token.version).__name__}"
            )
        os.environ.pop(variable)

        async def environment() -> None:
            api = auth.AsyncEnvironmentCredentialProvider(variable)
            bearer = auth.AsyncEnvironmentCredentialProvider(variable, kind="bearer")
            await arecord(lines, "missing async environment get", lambda: api.get(context))
            for value in ("first", "second", ""):
                os.environ[variable] = value
                material, token = await api.get(context), await bearer.get(context)
                lines.append(
                    f"  async environment current={(material.value, token.token.value)} grants={token.token.scopes!r} expiry={token.token.expires_at!r} version={type(token.version).__name__}"
                )

        run(environment)
    finally:
        if previous is None:
            os.environ.pop(variable, None)
        else:
            os.environ[variable] = previous


def _reflection(package: ModuleType, auth: ModuleType, lines: list[str]) -> None:
    lines.append(f"  auth exports={auth.__all__}")
    for name in auth.__all__:
        owner = getattr(auth, name)
        if not isinstance(owner, type):
            continue
        lines.append(f"  hints {name}={tuple(key for key in get_type_hints(owner) if not key.startswith('_'))}")
        if (
            hasattr(owner, "__dataclass_fields__")
            or name.endswith("Provider")
            and not getattr(owner, "_is_protocol", False)
        ):
            lines.append(
                f"  constructor {name}={tuple((key, item.kind.name) for key, item in inspect.signature(owner).parameters.items())}"
            )
            lines.append(f"  init hints {name}={tuple(get_type_hints(owner.__init__))}")
        for method in ("get", "invalidate", "refresh", "close", "aclose", "sign"):
            if hasattr(owner, method):
                member = getattr(owner, method)
                lines.append(
                    f"  method {name}.{method} async={inspect.iscoroutinefunction(member)} hints={tuple(get_type_hints(member))}"
                )
    script = """
import importlib
import importlib.abc
import sys
from typing import get_type_hints

sys.path.insert(0, sys.argv[1])
blocked = {"httpcore2", "anyio", "asyncio", "pydantic", "msgspec", "datamodel_code_generator"}
blocked.add(sys.argv[2] + "_models")
execution = {
    sys.argv[2] + "._runtime.client." + name
    for name in ("auth_policy", "auth_challenges", "client", "logical", "native", "bodies", "oauth", "refresh")
}

class Blocked(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in blocked or fullname in execution:
            raise ImportError("Unexpected optional import: " + fullname)

sys.meta_path.insert(0, Blocked())
auth = importlib.import_module(sys.argv[2] + ".auth")
options = importlib.import_module(sys.argv[2] + ".options")
for name in auth.__all__:
    value = getattr(auth, name)
    if isinstance(value, type):
        get_type_hints(value, include_extras=True)
        for method in ("get", "invalidate", "refresh", "close", "aclose", "sign"):
            if hasattr(value, method):
                get_type_hints(getattr(value, method), include_extras=True)
for name in ("ClientOptions", "RequestOptions"):
    get_type_hints(getattr(options, name), include_extras=True)
print("  isolated auth imports=" + str(not any(name in sys.modules for name in blocked)))
print("  auth execution remains unloaded=" + str(not any(name in sys.modules for name in execution)))
print("  auth annotations load httpx2=" + str("httpx2" in sys.modules))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(Path(inspect.getfile(package)).parent.parent), package.__name__],
        check=True,
        capture_output=True,
        text=True,
    )
    lines.extend(result.stdout.splitlines())


def auth_values(package: ModuleType, lines: list[str]) -> None:
    """Report immutable public auth records, explicit provider behavior and option inputs."""
    auth = importlib.import_module(f"{package.__name__}.auth")
    options = importlib.import_module(f"{package.__name__}.options")
    responses = importlib.import_module(f"{package.__name__}.responses")
    _values(auth, options, responses, lines)
    _scope_values(auth, lines)
    _configuration(auth, options, lines)
    _providers(auth, lines)
    _reflection(package, auth, lines)
