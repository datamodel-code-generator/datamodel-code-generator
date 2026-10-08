"""Declare the runtime modules a client package copies from its capabilities, and what it exports.

The capabilities are the security schemes, the helpers, and the model codec kinds. The copy set never follows the
modules' imports: each capability names every module it needs, including those its modules import only for
annotations or inside functions, so that the package type-checks as copied.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from datamodel_code_generator._runtime.client.security import SecurityScheme
from datamodel_code_generator._target_contract import LiteralMapping, LiteralScalar

if TYPE_CHECKING:
    from collections.abc import Iterable

    from datamodel_code_generator._client.codec_plan import ClientCodecs
    from datamodel_code_generator._client.plan import ClientPlan
    from datamodel_code_generator._target_contract import GeneratedTypeContractBatch

Security: TypeAlias = Literal["api_key", "basic", "bearer", "client_credentials", "refresh_token"]
Helper: TypeAlias = Literal[
    "pagination", "polling", "streams", "websocket", "webhooks", "cache", "uploads", "compression"
]

_CORE: Final = (
    "client/auth.py",
    "client/auth_challenges.py",
    "client/auth_policy.py",
    "client/bodies.py",
    "client/body_sources.py",
    "client/client.py",
    "client/codecs.py",
    "client/coding.py",
    "client/errors.py",
    "client/events.py",
    "client/hooks.py",
    "client/logical.py",
    "client/media.py",
    "client/multipart.py",
    "client/native.py",
    "client/operations.py",
    "client/options.py",
    "client/paths.py",
    "client/raw.py",
    "client/redirects.py",
    "client/responses.py",
    "client/retry.py",
    "client/scopes.py",
    "client/security.py",
    "client/timing.py",
    "client/urls.py",
    "model_codecs/errors.py",
    "model_codecs/media.py",
    "model_codecs/parameters.py",
    "model_codecs/unset.py",
    "model_codecs/wire.py",
    "protocols/errors.py",
    "protocols/records.py",
    "protocols/references.py",
    "protocols/resume.py",
    "protocols/sources.py",
)
_PROTOCOLS: Final = (
    "protocols/client.py",
    "protocols/caches.py",
    "protocols/options.py",
    "protocols/origins.py",
    "protocols/websocket_types.py",
)
_OAUTH: Final = ("client/grants.py", "client/oauth.py", "client/refresh.py")
_PAGES: Final = ("protocols/links.py", "protocols/pagination.py", "protocols/values.py", "protocols/writes.py")
_HELPERS: Final[dict[Helper, tuple[str, ...]]] = {
    "pagination": _PAGES,
    "polling": (*_PAGES, "protocols/polling.py"),
    "streams": (*_PAGES, "protocols/streams.py"),
    "uploads": (*_PAGES, "protocols/uploads.py"),
    "cache": ("protocols/cache.py", "protocols/cache_stores.py"),
    "websocket": ("protocols/websocket.py", "protocols/websocket_connectors.py", "protocols/websocket_native.py"),
    "webhooks": ("protocols/values.py", "protocols/webhook_events.py", "protocols/webhooks.py"),
    "compression": ("client/compression.py",),
}
_VERIFIED: Final = ("protocols/signatures.py", "protocols/verification.py", "protocols/webhook_keys.py")
_SIGNATURES: Final[dict[str, tuple[str, ...]]] = {
    "none": (),
    "adapter": ("protocols/adapters.py",),
    "ed25519": (*_VERIFIED, "protocols/public_keys.py"),
    "rsa-pss-sha256": (*_VERIFIED, "protocols/public_keys.py"),
}
_KINDS: Final[dict[str, Helper]] = {
    "pagination": "pagination",
    "polling": "polling",
    "sse": "streams",
    "ndjson": "streams",
    "websocket": "websocket",
    "cache": "cache",
    "resumable_upload": "uploads",
    "webhook": "webhooks",
}
_BACKENDS: Final[dict[str, tuple[str, ...]]] = {
    "pydantic": ("model_codecs/native.py",),
    "pydantic_dataclass": ("model_codecs/native.py",),
    "msgspec": ("model_codecs/native.py",),
    "stdlib": ("model_codecs/native.py", "model_codecs/stdlib.py"),
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Capabilities:
    """What a client package declares: its usable security schemes, its helpers, and its model codec kinds.

    `signatures` holds the signature kinds of the webhook helpers; `keywords` marks a package whose generated keyword
    records name the runtime's unpacked-argument types.
    """

    security: frozenset[Security]
    helpers: frozenset[Helper]
    signatures: frozenset[str]
    backends: frozenset[str]
    keywords: bool

    @property
    def oauth(self) -> bool:
        """Whether an OAuth 2 flow is declared."""
        return bool(self.security & {"client_credentials", "refresh_token"})

    @property
    def protocols(self) -> bool:
        """Whether a helper that `ProtocolClientOptions` configures is declared."""
        return bool(self.helpers - {"webhooks", "compression"})

    def modules(self) -> tuple[str, ...]:
        """Return the runtime modules the declared capabilities need, in ascending path order."""
        modules = {*_CORE, *(module for kind in self.backends for module in _BACKENDS[kind])}
        modules.update(module for helper in self.helpers for module in _HELPERS[helper])
        modules.update(module for kind in self.signatures for module in _SIGNATURES.get(kind, _VERIFIED))
        if self.protocols:
            modules.update(_PROTOCOLS)
        if "webhooks" in self.helpers:
            modules.add("protocols/options.py")
            modules.update(("protocols/caches.py", "protocols/origins.py", "protocols/websocket_types.py"))
        if self.oauth:
            modules.update(_OAUTH)
        if self.keywords:
            modules.add("client/arguments.py")
        return tuple(sorted(modules))


def _flows(facts: dict[str, object]) -> Iterable[Security]:
    """Return the OAuth capabilities of one OAuth 2 or OpenID Connect declaration."""
    flows = facts.get("flows")
    names = (
        {key.value for key, _ in flows.entries if isinstance(key, LiteralScalar)}
        if isinstance(flows, LiteralMapping)
        else set()
    )
    if "clientCredentials" in names:
        yield "client_credentials"
    if facts.get("type") == "openIdConnect" or names - {"clientCredentials"}:
        yield "refresh_token"


def declared_security(plan: ClientPlan, batch: GeneratedTypeContractBatch) -> frozenset[Security]:
    """Return the credential kinds of the usable schemes that the package's root or its operations name.

    A bearer scheme declared as OAuth 2 also declares each of its flows' token providers.
    """
    schemes = {scheme for scheme in plan.security_schemes if isinstance(scheme, SecurityScheme)}
    schemes.update(
        item.scheme
        for spec in plan.operations
        if spec.security is not None
        for alternative in spec.security.alternatives
        for item in alternative
    )
    kinds: set[Security] = {scheme.kind for scheme in schemes}
    bearer = {scheme.name for scheme in schemes if scheme.kind == "bearer"}
    for declaration in batch.security_schemes:
        if declaration.name in bearer:
            kinds.update(_flows({name: getattr(value, "value", value) for name, value in declaration.facts}))
    return frozenset(kinds)


def declared_helpers(kinds: Iterable[str], plan: ClientPlan) -> frozenset[Helper]:
    """Return the helpers of the package's helper kinds and its operations' request codings."""
    found: set[Helper] = {_KINDS[kind] for kind in kinds}
    if any("gzip" in spec.accepted_content_encodings for spec in plan.operations):
        found.add("compression")
    return frozenset(found)


def declared_backends(codecs: ClientCodecs) -> frozenset[str]:
    """Return the codec kinds of the package's model uses."""
    return frozenset(use.kind for use in codecs.uses)
