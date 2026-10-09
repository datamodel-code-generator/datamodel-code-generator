"""Declare the runtime modules a client package copies from its capabilities, and what it exports.

The capabilities are the security schemes, the helpers, the model codec kinds, and the request bodies and response
headers the operations declare. The copy set never follows the modules' imports: each capability names every module it
needs, including those its modules import only for annotations or inside functions, so that the package type-checks as
copied, and the core modules name no other module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Iterable

    from datamodel_code_generator._client.codec_plan import ClientCodecs
    from datamodel_code_generator._client.plan import ClientPlan

Security: TypeAlias = Literal["api_key", "basic", "bearer", "client_credentials", "refresh_token"]
RawBody: TypeAlias = Literal["bytes", "binary", "multipart"]
Helper: TypeAlias = Literal[
    "pagination", "polling", "streams", "websocket", "webhooks", "cache", "uploads", "compression"
]

_CORE: Final = (
    "client/client.py",
    "client/content.py",
    "client/errors.py",
    "client/logical.py",
    "client/media.py",
    "client/native.py",
    "client/operations.py",
    "client/options.py",
    "client/paths.py",
    "client/positions.py",
    "client/raw.py",
    "client/responses.py",
    "client/retry.py",
    "client/timing.py",
    "client/urls.py",
    "model_codecs/errors.py",
    "model_codecs/media.py",
    "model_codecs/parameters.py",
    "model_codecs/unset.py",
)
_PROTOCOLS: Final = (
    "protocols/client.py",
    "protocols/client_options.py",
    "protocols/caches.py",
    "protocols/names.py",
    "protocols/options.py",
    "protocols/origins.py",
)
_SCHEMES: Final = ("client/security.py",)
_AUTH: Final = (*_SCHEMES, "client/auth.py")
_OAUTH: Final = (*_AUTH, "client/oauth.py")
_BINARY: Final = ("client/bodies.py", "client/body_sources.py")
_MULTIPART: Final = (*_BINARY, "client/multipart.py")
_HEADERS: Final = ("client/codecs.py", "model_codecs/parameter_reads.py")
_BASE: Final = ("protocols/errors.py", "protocols/records.py", "protocols/references.py")
_PAGES: Final = (*_BASE, "protocols/links.py", "protocols/pagination.py", "protocols/values.py", "protocols/writes.py")
_RESUMED: Final = (*_PAGES, "protocols/resume.py")
_HELPERS: Final[dict[Helper, tuple[str, ...]]] = {
    "pagination": _PAGES,
    "polling": (*_RESUMED, "protocols/polling.py"),
    "streams": (*_RESUMED, "protocols/streams.py"),
    "uploads": (*_RESUMED, "protocols/sources.py", "protocols/uploads.py"),
    "cache": (*_BASE, "protocols/cache.py", "protocols/cache_stores.py"),
    "websocket": (*_BASE, "protocols/websocket.py"),
    "webhooks": (*_BASE, "protocols/values.py", "protocols/webhook_events.py", "protocols/webhooks.py"),
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
    records name the runtime's unpacked-argument types. `schemes` marks a package that declares security schemes or
    operation security, usable or not; `binary_bodies` and `multipart_requests` one whose operations send binary or
    form-data bodies, `form_data` one with a URL-encoded body or response no schema describes, `response_headers` one
    that decodes declared response headers, and `multipart_responses` one that reads multipart responses.
    """

    security: frozenset[Security]
    helpers: frozenset[Helper]
    signatures: frozenset[str]
    backends: frozenset[str]
    keywords: bool
    schemes: bool = False
    binary_bodies: bool = False
    multipart_requests: bool = False
    form_data: bool = False
    response_headers: bool = False
    multipart_responses: bool = False

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
        if self.schemes:
            modules.update(_SCHEMES)
        if self.security:
            modules.update(_OAUTH if self.oauth else _AUTH)
        if self.keywords:
            modules.add("client/arguments.py")
        if self.binary_bodies:
            modules.update(_BINARY)
        if self.multipart_requests:
            modules.update(_MULTIPART)
        if self.response_headers:
            modules.update(_HEADERS)
        if self.multipart_responses:
            modules.update((*_MULTIPART, "client/multipart_responses.py"))
        return tuple(sorted(modules))

    @property
    def raw_body(self) -> RawBody:
        """Return the bodies `request_raw` takes: those of the request media the package declares, else bytes."""
        return "multipart" if self.multipart_requests else "binary" if self.binary_bodies else "bytes"


def declared_security(plan: ClientPlan) -> frozenset[Security]:
    """Return the credential kinds of the schemes the package's operations require, and their OAuth flows."""
    return frozenset((
        *(credential.scheme.kind for credential in plan.credentials),
        *(flow for credential in plan.credentials for flow, _ in credential.flows),
    ))


def declared_helpers(kinds: Iterable[str], plan: ClientPlan) -> frozenset[Helper]:
    """Return the helpers of the package's helper kinds and its operations' request codings."""
    found: set[Helper] = {_KINDS[kind] for kind in kinds}
    if any("gzip" in spec.accepted_content_encodings for spec in plan.operations):
        found.add("compression")
    return frozenset(found)


def declared_backends(codecs: ClientCodecs) -> frozenset[str]:
    """Return the codec kinds of the package's model uses."""
    return frozenset(use.kind for use in codecs.uses)
