"""Target ownership manifests: canonical JSON, identities, state, and ownership plans."""

from __future__ import annotations

import hashlib
import json
import os
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, TypeAlias, cast
from urllib.parse import urlsplit
from urllib.request import url2pathname

from typing_extensions import TypeIs

from datamodel_code_generator._api_types import (
    APIGenerationError,
    ArtifactAction,
    Diagnostic,
    OperationRef,
    SchemaRef,
    TargetEditWarning,
    TargetStateWarning,
)

if TYPE_CHECKING:
    from datamodel_code_generator._api_types import TargetKind
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue
    from datamodel_code_generator._target_contract import (
        GeneratedTypeContractBatch,
        ModelArtifact,
        OperationId,
        SourceDocumentId,
        SourceLocation,
    )

MANIFEST_NAME: Final = ".dcg-target-manifest.json"
GENERATOR_NAME: Final = "datamodel-code-generator"
ROOT_URN: Final = "urn:dcg:root"
ROOT_POINTER: Final = "/inputs/root"
Observed: TypeAlias = "tuple[str, int] | None"
JSONObject: TypeAlias = "dict[str, JSONValue]"

_SHA256_HEX_LENGTH: Final = 64
_HASH_PREFIX: Final = "sha256:"
_MANIFEST_KEYS: Final = frozenset({"format", "generator", "target", "model", "files"})


def canonical_bytes(value: JSONValue) -> bytes:
    """Encode JSON with sorted keys, no whitespace, UTF-8, and no non-finite numbers."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def canonical_document(value: JSONValue) -> bytes:
    """Encode one persistent JSON file: canonical bytes and exactly one trailing LF."""
    return canonical_bytes(value) + b"\n"


def sha256(data: bytes) -> str:
    """Return the lowercase hexadecimal SHA-256 of bytes."""
    return hashlib.sha256(data).hexdigest()


def observe(data: bytes | None) -> Observed:
    """Return the digest and size of a file's bytes, or None for a missing file."""
    return None if data is None else (sha256(data), len(data))


def observe_file(path: Path) -> Observed:
    """Observe the regular file at *path* as it is now."""
    return observe(path.read_bytes() if path.is_file() else None)


def target_identity(kind: TargetKind, package: str) -> str:
    """Identify a target by its kind and package at its own root, without a cross-target registry."""
    return sha256(canonical_bytes({"kind": kind, "package": package, "root_uri": "."}))


@cache
def runtime_revision() -> str:
    """Digest the private runtime sources that targets copy into generated packages."""
    from datamodel_code_generator import _runtime  # noqa: PLC0415

    root = Path(_runtime.__file__).parent
    sources = sorted((path.relative_to(root).as_posix(), path) for path in root.rglob("*.py"))
    return sha256(canonical_bytes([{"path": name, "sha256": sha256(path.read_bytes())} for name, path in sources]))


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
    """Return the credential-free URI a manifest records for a document identity."""
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
        """Return the manifest pointer of an explicitly named document, the root when unnamed."""
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


def model_record(*, output: str, artifacts: Sequence[ModelArtifact]) -> JSONObject:
    """Record where the models live and one hash of their files, in ordinary emit order."""
    listing: list[JSONValue] = [
        {"path": "/".join(artifact.path), "sha256": sha256(artifact.content)} for artifact in artifacts
    ]
    return {"output": output, "sha256": sha256(canonical_bytes(listing))}


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetState:
    """A target root's previous manifest, as read: the files it owns, or nothing on first generation."""

    files: Mapping[PurePosixPath, str] = field(default_factory=lambda: MappingProxyType({}))
    snapshot: Observed = None


def _is_hash(value: object) -> TypeIs[str]:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_HEX_LENGTH
        and all(char in "0123456789abcdef" for char in value)
    )


def _is_contained(path: str) -> bool:
    posix, windows = PurePosixPath(path), PureWindowsPath(path)
    return bool(posix.parts) and posix.as_posix() == path and not windows.anchor and ".." not in windows.parts


def _owned(manifest: object, kind: TargetKind, package: str) -> dict[PurePosixPath, str] | None:
    """Return the files a current manifest owns, or None for an old or unknown manifest."""
    match manifest:
        case {
            "format": 1 as version,
            "generator": {"name": str(), "version": str()},
            "target": {"kind": str() as recorded_kind, "package": str() as recorded_package},
            "model": {"output": str(), "sha256": model},
            "files": dict() as files,
        } if type(version) is int and manifest.keys() == _MANIFEST_KEYS and _is_hash(model):
            owned = {
                PurePosixPath(path): digest.removeprefix(_HASH_PREFIX)
                for path, digest in files.items()
                if _is_contained(path) and isinstance(digest, str) and digest.startswith(_HASH_PREFIX)
            }
            if len(owned) != len(files) or not all(map(_is_hash, owned.values())):
                return None
            if (recorded_kind, recorded_package) != (kind, package):
                raise APIGenerationError((
                    Diagnostic(
                        code="E_OUTPUT_CONFLICT",
                        severity="error",
                        stage="ownership",
                        message="The manifest belongs to another target",
                        artifact_path=MANIFEST_NAME,
                        target_id=target_identity(kind, package),
                    ),
                ))
            return owned
    return None


def shown(path: Path, cwd: Path) -> Path:
    """Spell an output path for a message: relative to the working directory when it lies inside it."""
    return location.relative_to(cwd) if (location := cwd / path.expanduser()).is_relative_to(cwd) else location


def read_target_state(root: Path, kind: TargetKind, package: str, output: Path) -> TargetState:
    """Read a target root's previous manifest; an old or unknown one owns nothing, so nothing is deleted."""
    if not (path := root / MANIFEST_NAME).is_file():
        return TargetState()
    data = path.read_bytes()
    try:
        manifest = json.loads(data)
    except ValueError:
        manifest = None
    if (owned := _owned(manifest, kind, package)) is not None:
        return TargetState(files=MappingProxyType(owned), snapshot=observe(data))
    warnings.warn(
        f"{(output / MANIFEST_NAME).as_posix()}: The manifest has an old or unknown format, "
        "so the target owns no files and deletes none",
        TargetStateWarning,
        stacklevel=2,
    )
    return TargetState(snapshot=observe(data))


@dataclass(frozen=True, slots=True, kw_only=True)
class PlannedFile:
    """One target file a renderer produced; the target owns every file it plans."""

    path: PurePosixPath
    content: bytes


@dataclass(frozen=True, slots=True, kw_only=True)
class FilePlan:
    """What publication does with one target path, and the bytes the path holds afterwards."""

    path: PurePosixPath
    action: ArtifactAction
    content: bytes | None
    observed: Observed


def plan_files(root: Path, state: TargetState, planned: Sequence[PlannedFile], target_id: str) -> tuple[FilePlan, ...]:
    """Compare planned files with the previous manifest and the actual files, refusing only unmanaged files.

    Like model generation, a run rewrites the files the target owns, including ones edited by hand, recreates
    missing ones, and deletes owned files the plan no longer has.
    """
    conflicts: list[Diagnostic] = []
    plans: list[FilePlan] = []

    def current(path: PurePosixPath) -> bytes | None:
        location = root.joinpath(*path.parts)
        return location.read_bytes() if location.is_file() else None

    for item in planned:
        if (content := current(item.path)) is not None and item.path not in state.files:
            conflicts.append(
                Diagnostic(
                    code="E_OUTPUT_CONFLICT",
                    severity="error",
                    stage="ownership",
                    message="An unmanaged file occupies a path the target owns",
                    artifact_path=item.path.as_posix(),
                    target_id=target_id,
                )
            )
            continue
        plans.append(
            FilePlan(
                path=item.path,
                action="unchanged" if content == item.content else "write",
                content=item.content,
                observed=observe(content),
            )
        )
    kept = {item.path for item in planned}
    plans.extend(
        FilePlan(path=path, action="delete", content=None, observed=observe(content))
        for path in state.files
        if path not in kept and (content := current(path)) is not None
    )
    if conflicts:
        raise APIGenerationError(tuple(conflicts))
    return tuple(plans)


def hand_edits(state: TargetState, plans: Sequence[FilePlan], output: Path) -> None:
    """Warn about each owned file whose bytes differ from the hash its last generation recorded."""
    for plan in plans:
        if (
            (observed := plan.observed) is not None
            and (recorded := state.files.get(plan.path)) is not None
            and observed[0] != recorded
        ):
            warnings.warn(
                f"{output.joinpath(*plan.path.parts).as_posix()}: The owned file changed since the last generation, "
                "and this generation discards the change",
                TargetEditWarning,
                stacklevel=2,
            )


def manifest_files(plans: Sequence[FilePlan]) -> JSONObject:
    """Record every file the target keeps, with its hash."""
    return {
        plan.path.as_posix(): f"{_HASH_PREFIX}{sha256(content)}"
        for plan in plans
        if (content := plan.content) is not None
    }
