"""The names a client target reserves, and the rules its explicit names and header tokens follow.

Each helper kind's names are here too: the label documentation and messages call it by, and its options keyword.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, NamedTuple

from datamodel_code_generator._runtime.model_codecs.media import TOKEN
from datamodel_code_generator._target_naming import explicit_name, windows_device
from datamodel_code_generator._target_unicode import is_nfkc, is_python_name

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from datamodel_code_generator._format_types import PythonVersion

TYPE_EXPORTS: Final = frozenset({"JSONValue"})
RESERVED_MEMBERS: Final = frozenset({
    "aclose",
    "close",
    "protocols",
    "request_raw",
    "with_options",
    "with_raw_response",
    "with_response",
    "with_streaming_response",
})
RESERVED_ARGUMENTS: Final = frozenset({"body", "media_type", "options", "response_media_type", "self"})
VIEW_KEYWORDS: Final = (
    "base_url",
    "server",
    "timeout",
    "total_timeout",
    "max_retries",
    "retry",
    "default_headers",
    "default_query",
    "follow_redirects",
    "auth",
)
CLIENT_KEYWORDS: Final = (
    *VIEW_KEYWORDS,
    "compression",
    "clock",
    "http_client",
    "helper_defaults",
    "cache_stores",
    "allowed_origins",
)


class HelperKindNames(NamedTuple):
    """How documentation and messages call a helper kind, and the keyword its methods take the kind's options by."""

    label: str
    options: str


HELPER_KINDS: Final = {
    "pagination": HelperKindNames("pagination", "pagination_options"),
    "polling": HelperKindNames("polling", "poll_options"),
    "sse": HelperKindNames("SSE", "stream_options"),
    "ndjson": HelperKindNames("NDJSON", "stream_options"),
    "websocket": HelperKindNames("WebSocket", "ws_options"),
    "cache": HelperKindNames("cache", "cache_options"),
    "resumable_upload": HelperKindNames("upload", "upload_options"),
}
HELPER_ARGUMENTS: Final = frozenset({"items", "source", "state", *(names.options for names in HELPER_KINDS.values())})


def namespace_problem(value: object, target_python_version: PythonVersion) -> str | None:
    """Return why a resource namespace is invalid, or None for dotted identifiers that name no member or device.

    Its first part also names the resource's package in the `types` package, so it cannot be a name `types` exports.
    The refusal says why the name is reserved.
    """
    if not isinstance(value, str) or not all(explicit_name(part, target_python_version) for part in value.split(".")):
        return "A resource namespace must be dotted Python identifiers"
    for index, part in enumerate(value.split(".")):
        if part in RESERVED_MEMBERS:
            reason = "a member of the clients and their resources"
        elif windows_device(part, target_python_version):
            reason = "a device name on Windows"
        elif not index and part in TYPE_EXPORTS:
            reason = "which the `types` package exports"
        else:
            continue
        return f"A resource namespace cannot use the reserved name {part!r}, {reason}"
    return None


def token(value: object) -> bool:
    """Return whether a value is an HTTP token, such as a header name."""
    return isinstance(value, str) and TOKEN.fullmatch(value) is not None


def helper_name_problem(value: str, target_python_version: PythonVersion) -> str | None:
    """Return why a helper name is invalid, or None for dotted public identifiers of the target in NFKC form."""
    for part in value.split("."):
        if not is_python_name(part, target_python_version):
            return f"The helper name {value!r} must be Python identifiers separated by dots, without keywords"
        if (
            not is_nfkc(part, target_python_version)
            or part.startswith("_")
            or windows_device(part, target_python_version)
        ):
            return f"The helper name {value!r} cannot use the part {part!r}"
    return None


def pascal_parts(parts: Iterable[str], target_python_version: PythonVersion) -> str:
    """Return the PascalCase join of the parts of a dotted name, as the target capitalizes them."""
    from datamodel_code_generator.reference import snake_to_upper_camel  # noqa: PLC0415

    return "".join(snake_to_upper_camel(part, target_python_version=target_python_version) for part in parts)


def helper_classes(name: str, kind: str, target_python_version: PythonVersion) -> Iterator[tuple[tuple[str, ...], str]]:
    """Yield the class of each namespace a helper's dotted name opens, then the helper's own class, with their parts.

    A namespace class ends in `Protocols` and a helper class in its kind, both after the PascalCase parts.
    """
    parts = tuple(name.split("."))
    for end in range(1, len(parts)):
        yield parts[:end], f"{pascal_parts(parts[:end], target_python_version)}Protocols"
    yield parts, f"{pascal_parts(parts, target_python_version)}{pascal_parts((kind,), target_python_version)}"
