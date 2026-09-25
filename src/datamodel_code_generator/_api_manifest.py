"""Target ownership manifests and model inventories: canonical JSON, identities, state, and ownership plans."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from functools import cache
from math import isfinite
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, TypeAlias
from urllib.parse import urlsplit
from urllib.request import url2pathname

from typing_extensions import TypeIs

from datamodel_code_generator._api_types import APIGenerationError, ArtifactAction, Diagnostic
from datamodel_code_generator._codec_declarations import OperationRef, SchemaRef

if TYPE_CHECKING:
    from datamodel_code_generator._api_types import TargetKind
    from datamodel_code_generator._generation_contract import (
        GeneratedTypeContractBatch,
        OperationId,
        SourceDocumentId,
        SourceLocation,
        TypeUseId,
    )
    from datamodel_code_generator._openapi_artifacts import ModelArtifact
    from datamodel_code_generator._openapi_generation import SourceLease
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue
    from datamodel_code_generator._source import YamlValue

MANIFEST_NAME: Final = ".dcg-target-manifest.json"
INVENTORY_PATH: Final = PurePosixPath(".dcg-state", "model-artifacts.json")
GENERATOR_NAME: Final = "datamodel-code-generator"
ROOT_URN: Final = "urn:dcg:root"
ROOT_POINTER: Final = "/inputs/root"
Ownership: TypeAlias = Literal["owned", "create_only"]
RootKind: TypeAlias = Literal["file", "url", "text", "mapping"]
JSONObject: TypeAlias = "dict[str, JSONValue]"

_SHA256_HEX_LENGTH: Final = 64
_RECORD_KEYS: Final = {
    "target": frozenset({"id", "kind", "package", "root_uri"}),
    "generator": frozenset({"name", "version", "runtime_revision"}),
    "inputs": frozenset({"root", "documents", "provenance", "target_config"}),
    "model": frozenset({"output_uri", "package", "mode", "fingerprint", "artifacts"}),
    "selection": frozenset({"rules", "reason", "selected_operations", "excluded_operations"}),
    "extensions": frozenset({"hooks", "templates", "formatters"}),
}
_MANIFEST_KEYS: Final = frozenset({"schema_version", *_RECORD_KEYS, "files", "diagnostics", "bindings", "target_data"})
_INVENTORY_KEYS: Final = frozenset({"schema_version", "model"})
_FILE_KEYS: Final = frozenset({"path", "kind", "ownership", "sha256", "size", "group"})
_DECLARED_ROLES: Final = frozenset({
    "parameter",
    "response_header",
    "request_encoding_header",
    "response_encoding_header",
})


def canonical_bytes(value: JSONValue) -> bytes:
    """Encode JSON with sorted keys, no whitespace, UTF-8, and no non-finite numbers."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def canonical_document(value: JSONValue) -> bytes:
    """Encode one persistent JSON file: canonical bytes and exactly one trailing LF."""
    return canonical_bytes(value) + b"\n"


def sha256(data: bytes) -> str:
    """Return the lowercase hexadecimal SHA-256 of bytes."""
    return hashlib.sha256(data).hexdigest()


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


def json_object() -> JSONObject:
    """Return a new empty JSON object."""
    return {}


def json_projection(value: YamlValue) -> JSONValue:
    """Project a loaded document into JSON values: keys become strings and non-finite numbers null."""
    match value:
        case dict():
            return {str(key): json_projection(item) for key, item in value.items()}
        case list():
            return [json_projection(item) for item in value]
        case float() if not isfinite(value):
            return None
    return value


def config_error(*, code: str, option_path: str, message: str) -> APIGenerationError:
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

    kind: RootKind
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


def _digest(lease: SourceLease, document: SourceDocumentId) -> str:
    from datamodel_code_generator._generation_contract import SourceLocation  # noqa: PLC0415

    return sha256(canonical_bytes(json_projection(lease.borrow(SourceLocation(document, "", "schema")))))


class DocumentTable:
    """Map the accepted attempt's documents to the manifest: the root, then the others by URI and digest."""

    __slots__ = ("_lookup", "documents", "pointers", "root", "root_uri")

    def __init__(self, batch: GeneratedTypeContractBatch, lease: SourceLease, source: RootInput, root: Path) -> None:
        """Digest each borrowed document once and order the non-root ones by URI, then digest."""
        first, *others = batch.documents
        self.root_uri = persistent_uri(source.identity, root, "input")
        self.root: JSONObject = {"kind": source.kind, "uri": self.root_uri, "digest": _digest(lease, first.id)}
        located = [(document, document_identity(document.uri, source.base)) for document in others]
        entries = sorted(
            (
                persistent_uri(identity, root, "input"),
                _digest(lease, document.id),
                document.id,
                identity,
                document.uri,
            )
            for document, identity in located
        )
        self.documents: list[JSONValue] = []
        self.pointers: dict[SourceDocumentId, str] = {first.id: ROOT_POINTER}
        self._lookup: dict[str, str] = {source.identity: ROOT_POINTER, first.uri: ROOT_POINTER}
        seen: dict[tuple[str, str], str] = {}
        for uri, digest, document, identity, name in entries:
            if (pointer := seen.get((uri, digest))) is None:
                pointer = seen[uri, digest] = f"/inputs/documents/{len(self.documents)}"
                self.documents.append({"uri": uri, "digest": digest})
            self.pointers[document] = self._lookup[identity] = self._lookup[name] = pointer

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

    def use(self, use: TypeUseId) -> JSONObject:
        """Return the persistent identity of one type use."""
        from datamodel_code_generator._generation_contract import OperationId  # noqa: PLC0415

        owner = use.owner
        return {
            "owner": {"kind": "operation", "source": self.operation(owner)}
            if isinstance(owner, OperationId)
            else {"kind": "schema", "source": self.source(owner)},
            "role": use.role,
            "use_site": self.source(use.use_site),
            "schema": self.source(use.schema_site),
            "declaration": self.source(use.declaration.location) if use.role in _DECLARED_ROLES else None,
            "direction": use.direction,
            "projection": use.projection,
            "location": use.location,
            "name": use.name,
            "status": use.status,
            "media": use.media,
        }


def portable(  # noqa: PLR0911
    value: object, locate: Callable[[Path], str], refer: Callable[[OperationRef | SchemaRef], JSONValue]
) -> JSONValue:
    """Project a configuration value into canonical JSON, with paths as target-relative URIs."""
    match value:
        case Enum():
            return portable(value.value, locate, refer)
        case None | bool() | int() | str():
            return value
        case float() if isfinite(value):
            return value
        case PurePath():
            return locate(Path(value))
        case OperationRef() | SchemaRef():
            return refer(value)
        case Mapping():
            return {str(key): portable(item, locate, refer) for key, item in value.items()}
        case set() | frozenset():
            return sorted((portable(item, locate, refer) for item in value), key=canonical_bytes)
        case list() | tuple():
            return [portable(item, locate, refer) for item in value]
        case _ if is_dataclass(value) and not isinstance(value, type):
            return {item.name: portable(getattr(value, item.name), locate, refer) for item in fields(value)}
    raise TypeError(type(value).__qualname__)


def model_record(*, output_uri: str, package: str, mode: str, artifacts: Sequence[ModelArtifact]) -> JSONObject:
    """Describe the model artifacts a target binds, with their fingerprint, in ordinary emit order."""
    hashes = [("/".join(artifact.path), sha256(artifact.content), len(artifact.content)) for artifact in artifacts]
    fingerprint = sha256(
        canonical_bytes({
            "schema_version": 1,
            "model_package": package,
            "artifacts": [{"path": path, "kind": "model", "sha256": digest} for path, digest, _ in hashes],
        })
    )
    return {
        "output_uri": output_uri,
        "package": package,
        "mode": mode,
        "fingerprint": fingerprint,
        "artifacts": [{"path": path, "kind": "model", "sha256": digest, "size": size} for path, digest, size in hashes],
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordedFile:
    """One file of a previous manifest; create-only files carry no hash."""

    path: PurePosixPath
    kind: str
    ownership: Ownership
    sha256: str | None
    group: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetState:
    """A target root's previous manifest and inventory, as read, or nothing on first generation."""

    manifest: JSONObject | None = None
    files: Mapping[PurePosixPath, RecordedFile] = field(default_factory=lambda: MappingProxyType({}))


def _state_error(*, code: str, path: PurePosixPath, message: str, target_id: str) -> APIGenerationError:
    return APIGenerationError((
        Diagnostic(
            code=code,
            severity="error",
            stage="ownership",
            message=message,
            artifact_path=path.as_posix(),
            target_id=target_id,
        ),
    ))


def _is_object(value: object) -> TypeIs[JSONObject]:
    return isinstance(value, dict)


def _is_hash(value: object) -> TypeIs[str]:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_HEX_LENGTH
        and all(char in "0123456789abcdef" for char in value)
    )


def _is_contained(path: str) -> bool:
    posix, windows = PurePosixPath(path), PureWindowsPath(path)
    return bool(posix.parts) and posix.as_posix() == path and not windows.anchor and ".." not in windows.parts


def _read_json(path: Path, relative: PurePosixPath, target_id: str) -> JSONObject:
    try:
        value = json.loads(path.read_bytes())
    except ValueError:
        raise _state_error(
            code="E_STATE_CORRUPT", path=relative, message="The management file is not JSON", target_id=target_id
        ) from None
    match value:
        case {"schema_version": 1 as version} if type(version) is int:
            return value
        case {"schema_version": int() as version} if type(version) is int:
            raise _state_error(
                code="E_STATE_VERSION",
                path=relative,
                message="The management file has an unknown version",
                target_id=target_id,
            )
    raise _state_error(
        code="E_STATE_CORRUPT",
        path=relative,
        message="The management file has no schema version",
        target_id=target_id,
    )


def _recorded(entry: JSONValue, target_id: str) -> RecordedFile:
    match entry:
        case {
            "path": str() as path,
            "kind": str() as kind,
            "ownership": "owned",
            "sha256": digest,
            "size": int() as size,
            "group": str() | None as group,
        } if (
            len(entry) == len(_FILE_KEYS)
            and _is_contained(path)
            and _is_hash(digest)
            and not isinstance(size, bool)
            and size >= 0
        ):
            return RecordedFile(path=PurePosixPath(path), kind=kind, ownership="owned", sha256=digest, group=group)
        case {
            "path": str() as path,
            "kind": str() as kind,
            "ownership": "create_only",
            "sha256": None,
            "size": None,
            "group": str() | None as group,
        } if len(entry) == len(_FILE_KEYS) and _is_contained(path):
            return RecordedFile(path=PurePosixPath(path), kind=kind, ownership="create_only", sha256=None, group=group)
    raise _state_error(
        code="E_STATE_CORRUPT",
        path=PurePosixPath(MANIFEST_NAME),
        message="A manifest file record is invalid",
        target_id=target_id,
    )


def _check_manifest(manifest: JSONObject, kind: TargetKind, package: str, target_id: str) -> list[JSONValue]:
    name = PurePosixPath(MANIFEST_NAME)
    if frozenset(manifest) != _MANIFEST_KEYS:
        raise _state_error(
            code="E_STATE_CORRUPT", path=name, message="The manifest has unknown or missing keys", target_id=target_id
        )
    for key, keys in _RECORD_KEYS.items():
        if not _is_object(record := manifest[key]) or frozenset(record) != keys:
            raise _state_error(
                code="E_STATE_CORRUPT", path=name, message=f"The manifest {key} record is invalid", target_id=target_id
            )
    match manifest["files"], manifest["diagnostics"], manifest["bindings"], manifest["target_data"]:
        case list() as files, list(), list(), dict() as data if frozenset(data) == frozenset((kind,)):
            pass
        case _:
            raise _state_error(
                code="E_STATE_CORRUPT",
                path=name,
                message="The manifest has an invalid list or target data",
                target_id=target_id,
            )
    match manifest["target"]:
        case {"kind": str() as recorded_kind, "package": str() as recorded_package} if (
            recorded_kind,
            recorded_package,
        ) != (kind, package):
            raise _state_error(
                code="E_OUTPUT_CONFLICT",
                path=name,
                message="The manifest belongs to another target",
                target_id=target_id,
            )
        case {"id": identity, "kind": recorded_kind, "package": recorded_package, "root_uri": "."} if (
            identity,
            recorded_kind,
            recorded_package,
        ) == (target_id, kind, package):
            return files
    raise _state_error(
        code="E_STATE_CORRUPT", path=name, message="The manifest target record is invalid", target_id=target_id
    )


def read_target_state(root: Path, kind: TargetKind, package: str) -> TargetState:
    """Read and check a target root's previous manifest and model inventory."""
    target_id = target_identity(kind, package)
    manifest_path, inventory_path = root / MANIFEST_NAME, root.joinpath(*INVENTORY_PATH.parts)
    match manifest_path.is_file(), inventory_path.is_file():
        case False, False:
            return TargetState()
        case True, False:
            raise _state_error(
                code="E_STATE_MISSING",
                path=INVENTORY_PATH,
                message="The model inventory is missing",
                target_id=target_id,
            )
        case False, True:
            raise _state_error(
                code="E_STATE_MISSING",
                path=PurePosixPath(MANIFEST_NAME),
                message="The manifest is missing",
                target_id=target_id,
            )
        case _:
            pass
    manifest = _read_json(manifest_path, PurePosixPath(MANIFEST_NAME), target_id)
    inventory = _read_json(inventory_path, INVENTORY_PATH, target_id)
    files = _check_manifest(manifest, kind, package, target_id)
    if frozenset(inventory) != _INVENTORY_KEYS or inventory["model"] != manifest["model"]:
        raise _state_error(
            code="E_STATE_CORRUPT",
            path=INVENTORY_PATH,
            message="The model inventory disagrees with the manifest",
            target_id=target_id,
        )
    recorded: dict[PurePosixPath, RecordedFile] = {}
    for entry in files:
        if (record := _recorded(entry, target_id)).path in recorded:
            raise _state_error(
                code="E_STATE_CORRUPT",
                path=PurePosixPath(MANIFEST_NAME),
                message="The manifest repeats a file",
                target_id=target_id,
            )
        recorded[record.path] = record
    return TargetState(manifest=manifest, files=recorded)


@dataclass(frozen=True, slots=True, kw_only=True)
class PlannedFile:
    """One target file a renderer produced, with the ownership the target plans for it."""

    path: PurePosixPath
    kind: str
    ownership: Ownership
    content: bytes
    group: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class FilePlan:
    """What publication does with one target path, and the bytes the path holds afterwards."""

    path: PurePosixPath
    kind: str
    action: ArtifactAction
    ownership: Ownership | None
    content: bytes | None
    group: str | None


def plan_files(root: Path, state: TargetState, planned: Sequence[PlannedFile], target_id: str) -> tuple[FilePlan, ...]:
    """Compare planned files with the previous manifest and the actual files, refusing unowned changes."""
    problems: list[Diagnostic] = []
    plans: list[FilePlan] = []

    def refuse(code: str, path: PurePosixPath, message: str) -> None:
        problems.append(
            Diagnostic(
                code=code,
                severity="error",
                stage="ownership",
                message=message,
                artifact_path=path.as_posix(),
                target_id=target_id,
            )
        )

    def owned(path: PurePosixPath, previous: RecordedFile | None) -> tuple[bool, bytes | None]:
        location = root.joinpath(*path.parts)
        current = location.read_bytes() if location.is_file() else None
        if previous is None or previous.ownership != "owned":
            return True, current
        if current is None:
            refuse("E_OUTPUT_MODIFIED", path, "A file the target owns is missing")
        elif sha256(current) != previous.sha256:
            refuse("E_OUTPUT_MODIFIED", path, "A file the target owns was modified")
        else:
            return True, current
        return False, None

    for item in planned:
        valid, current = owned(item.path, previous := state.files.get(item.path))
        match item.ownership, current:
            case _ if not valid:
                continue
            case "owned", bytes() if previous is None or previous.ownership != "owned":
                refuse("E_OUTPUT_CONFLICT", item.path, "An unmanaged file occupies a path the target owns")
                continue
            case "owned", _:
                action: ArtifactAction = "unchanged" if current == item.content else "write"
                content = item.content
            case _, None:
                action, content = "create_only", item.content
            case _:
                action, content = "unchanged", current
        plans.append(
            FilePlan(
                path=item.path,
                kind=item.kind,
                action=action,
                ownership=item.ownership,
                content=content,
                group=item.group,
            )
        )
    kept = {item.path for item in planned}
    for path, previous in state.files.items():
        if path not in kept and previous.ownership == "owned" and owned(path, previous)[0]:
            plans.append(
                FilePlan(
                    path=path, kind=previous.kind, action="delete", ownership=None, content=None, group=previous.group
                )
            )
    if problems:
        raise APIGenerationError(tuple(problems))
    return tuple(plans)


def manifest_files(plans: Sequence[FilePlan]) -> list[JSONValue]:
    """Record the final file inventory: owned files with their hashes, create-only files without."""
    return [
        {
            "path": plan.path.as_posix(),
            "kind": plan.kind,
            "ownership": plan.ownership,
            "sha256": sha256(plan.content) if plan.ownership == "owned" and plan.content is not None else None,
            "size": len(plan.content) if plan.ownership == "owned" and plan.content is not None else None,
            "group": plan.group,
        }
        for plan in plans
        if plan.ownership is not None
    ]
