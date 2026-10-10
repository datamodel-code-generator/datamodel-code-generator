"""Target documents: canonical JSON, document identities, and the pointers that name documents."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, TypeAlias, cast
from urllib.parse import urlsplit
from urllib.request import url2pathname

from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, OperationRef, SchemaRef
from datamodel_code_generator._url_redaction import redact_url

if TYPE_CHECKING:
    from datamodel_code_generator._runtime.model_codecs.media import JSONValue
    from datamodel_code_generator._target_contract import (
        GeneratedTypeContractBatch,
        OperationId,
        SourceDocumentId,
        SourceLocation,
    )

ROOT_URN: Final = "urn:dcg:root"
ROOT_POINTER: Final = "/inputs/root"
JSONObject: TypeAlias = "dict[str, JSONValue]"


def escape_pointer_token(token: str | int) -> str:
    """Escape one RFC 6901 reference token."""
    return token.replace("~", "~0").replace("/", "~1") if isinstance(token, str) else str(token)


def pointer_tokens(pointer: str) -> list[str]:
    """Split an RFC 6901 pointer into its unescaped reference tokens."""
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")] if pointer else []


def canonical_bytes(value: JSONValue) -> bytes:
    """Encode JSON with sorted keys, no whitespace, UTF-8, and no non-finite numbers."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha256(data: bytes) -> str:
    """Return the lowercase hexadecimal SHA-256 of bytes."""
    return hashlib.sha256(data).hexdigest()


def config_error(*, code: str, option_path: str | None, message: str) -> APIGenerationError:
    """Raise one configuration diagnostic as an API generation error."""
    return APIGenerationError((
        Diagnostic(code=code, severity="error", stage="config", message=message, option_path=option_path),
    ))


def document_identity(document: str, base: Path) -> str:
    """Canonicalize a document name: URLs and URNs as written, paths as resolved file URIs."""
    parts = urlsplit(document)
    match parts.scheme:
        case "file":
            return Path(url2pathname(parts.path)).resolve().as_uri()
        case scheme if len(scheme) > 1:
            return document
    return (base / document).resolve().as_uri()


def named_document(written: str | None, identity: str | None) -> str:
    """Say which document a reference names in a message: as written, then the file a relative path resolved to.

    A reference that keeps only its resolved identity is named by that file. A URL stays as written, an absolute
    path is named once, and the root document, which a reference names by leaving it out, adds nothing.
    """
    if written is None:
        return ""
    parts = urlsplit(identity or written)
    if parts.scheme != "file":
        return f" in {redact_url(written)!r}"
    file = Path(url2pathname(parts.path)).as_posix()
    return f" in {file!r}" if written == identity or Path(written).as_posix() == file else f" in {written!r} ({file})"


@dataclass(frozen=True, slots=True)
class RootInput:
    """Identify the root input for selectors and the directory its loader resolves relative references in."""

    identity: str
    base: Path


class DocumentTable:
    """Locate the accepted attempt's documents: the root, then the others in identity order."""

    __slots__ = ("_lookup", "pointers")

    def __init__(self, batch: GeneratedTypeContractBatch, source: RootInput) -> None:
        """Order the non-root documents by their identity, one pointer per identity, without reading them."""
        first, *others = batch.documents
        entries = sorted(
            (document_identity(document.uri, source.base), document.uri, document.id) for document in others
        )
        self.pointers: dict[SourceDocumentId, str] = {first.id: ROOT_POINTER}
        self._lookup: dict[str, str] = {source.identity: ROOT_POINTER, first.uri: ROOT_POINTER}
        seen: dict[str, str] = {}
        for identity, name, document in entries:
            pointer = seen.setdefault(identity, f"/inputs/documents/{len(seen)}")
            self.pointers[document] = self._lookup[identity] = self._lookup[name] = pointer

    def pointer(self, document: str | None, base: Path) -> str | None:
        """Return the pointer of an explicitly named document, the root when unnamed."""
        if document is None:
            return ROOT_POINTER
        return self._lookup.get(document) or self._lookup.get(document_identity(document, base))

    def source(self, location: SourceLocation) -> JSONObject:
        """Return the persistent reference of one source location."""
        return {"document": self.pointers[location.document], "pointer": location.pointer}

    def operation(self, operation: OperationId) -> JSONObject:
        """Return the persistent reference of an operation's root use site."""
        return self.source(operation.use_site)


def portable(value: object, refer: Callable[[OperationRef | SchemaRef], JSONValue]) -> JSONValue:
    """Project a validated helper setting into canonical JSON, with references as source references."""
    match value:
        case OperationRef() | SchemaRef():
            return refer(value)
        case Mapping():
            return {
                key if isinstance(key, str) else canonical_bytes(portable(key, refer)).decode(): portable(item, refer)
                for key, item in value.items()
            }
        case list() | tuple():
            return [portable(item, refer) for item in value]
    return cast("JSONValue", value)


def shown(path: Path, cwd: Path) -> Path:
    """Spell an output path for a message: relative to the working directory when it lies inside it."""
    return location.relative_to(cwd) if (location := cwd / path.expanduser()).is_relative_to(cwd) else location
