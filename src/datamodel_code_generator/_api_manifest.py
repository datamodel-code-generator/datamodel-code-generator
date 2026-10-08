"""Target inputs and identities: canonical JSON, document identities, and the pointers that name documents."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final, TypeAlias, cast
from urllib.parse import urlsplit
from urllib.request import url2pathname

from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, OperationRef, SchemaRef

if TYPE_CHECKING:
    from datamodel_code_generator._api_types import TargetKind
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue
    from datamodel_code_generator._target_contract import (
        GeneratedTypeContractBatch,
        OperationId,
        SourceDocumentId,
        SourceLocation,
    )

ROOT_URN: Final = "urn:dcg:root"
ROOT_POINTER: Final = "/inputs/root"
JSONObject: TypeAlias = "dict[str, JSONValue]"


def canonical_bytes(value: JSONValue) -> bytes:
    """Encode JSON with sorted keys, no whitespace, UTF-8, and no non-finite numbers."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha256(data: bytes) -> str:
    """Return the lowercase hexadecimal SHA-256 of bytes."""
    return hashlib.sha256(data).hexdigest()


def target_identity(kind: TargetKind, package: str) -> str:
    """Identify a target by its kind and package at its own root, without a cross-target registry."""
    return sha256(canonical_bytes({"kind": kind, "package": package, "root_uri": "."}))


def config_error(*, code: str, option_path: str | None, message: str) -> APIGenerationError:
    """Raise one configuration diagnostic as an API generation error."""
    return APIGenerationError((
        Diagnostic(code=code, severity="error", stage="config", message=message, option_path=option_path),
    ))


def relative_uri(path: Path, root: Path, option_path: str) -> str:
    """Return the POSIX URI of a path relative to the resolved target root."""
    try:
        return PurePosixPath(*Path(os.path.relpath(path.resolve(), root)).parts).as_posix()
    except ValueError:
        raise config_error(
            code="E_CONFIG_VALUE",
            option_path=option_path,
            message="A persistent path cannot be expressed relative to the target root",
        ) from None


def document_identity(document: str, base: Path) -> str:
    """Canonicalize a document name: URLs and URNs as written, paths as resolved file URIs."""
    parts = urlsplit(document)
    match parts.scheme:
        case "file":
            return Path(url2pathname(parts.path)).resolve().as_uri()
        case scheme if len(scheme) > 1:
            return document
    return (base / document).resolve().as_uri()


@dataclass(frozen=True, slots=True)
class RootInput:
    """Identify the root input for selectors and the directory its loader resolves relative references in."""

    identity: str
    base: Path


def persistent_uri(identity: str, root: Path, option_path: str) -> str:
    """Return the credential-free URI of a document identity, relative to the target root for a file."""
    from datamodel_code_generator.remote_lock import (  # noqa: PLC0415
        _display_url,  # pyright: ignore[reportPrivateUsage]
    )

    parts = urlsplit(identity)
    match parts.scheme:
        case "file":
            return relative_uri(Path(url2pathname(parts.path)), root, option_path)
        case "http" | "https":
            return _display_url(identity)
    return identity


class DocumentTable:
    """Locate the accepted attempt's documents: the root, then the others in URI order."""

    __slots__ = ("_lookup", "pointers", "root_uri", "uris")

    def __init__(self, batch: GeneratedTypeContractBatch, source: RootInput, root: Path) -> None:
        """Order the non-root documents by their persistent URI, one pointer per identity, without reading them."""
        first, *others = batch.documents
        self.root_uri = persistent_uri(source.identity, root, "input")
        located = [(document_identity(document.uri, source.base), document) for document in others]
        entries = sorted(
            (persistent_uri(identity, root, "input"), identity, document.uri, document.id)
            for identity, document in located
        )
        self.pointers: dict[SourceDocumentId, str] = {first.id: ROOT_POINTER}
        self.uris: dict[SourceDocumentId, str] = {first.id: self.root_uri}
        self._lookup: dict[str, str] = {source.identity: ROOT_POINTER, first.uri: ROOT_POINTER}
        seen: dict[str, str] = {}
        for uri, identity, name, document in entries:
            pointer = seen.setdefault(identity, f"/inputs/documents/{len(seen)}")
            self.pointers[document] = self._lookup[identity] = self._lookup[name] = pointer
            self.uris[document] = uri

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
