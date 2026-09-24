"""Generator-side codec declarations: builtin compatibility, model exports, and adapter registrations.

The new target entry points will expose these records through `datamodel_code_generator.api_types`.
"""

from __future__ import annotations

import keyword
from dataclasses import dataclass
from typing import Final, Literal, TypeAlias

from datamodel_code_generator._runtime.model_codecs.capabilities import (
    ClientMediaCodecCapabilities,
    CodecCapabilities,
    ParameterCodecCapabilities,
    SchemaCodecCapabilities,
    ServerMediaCodecCapabilities,
)
from datamodel_code_generator._runtime.model_codecs.context import (  # noqa: TC001 - Public annotations support get_type_hints().
    Direction,
)
from datamodel_code_generator._runtime.model_codecs.parameters import (  # noqa: TC001 - Public annotations support get_type_hints().
    ParameterLocation,
)
from datamodel_code_generator.enums import DataModelType  # noqa: TC001 - Public annotations support get_type_hints().

AdapterKind: TypeAlias = Literal["model", "parameter", "media", "schema"]
TypeUseRefRole: TypeAlias = Literal[
    "parameter",
    "request_body",
    "response_body",
    "response_header",
    "request_encoding_header",
    "response_encoding_header",
]
RegistrationCapabilities: TypeAlias = (
    CodecCapabilities
    | ParameterCodecCapabilities
    | ClientMediaCodecCapabilities
    | ServerMediaCodecCapabilities
    | SchemaCodecCapabilities
)

_KIND_CAPABILITIES: Final[dict[AdapterKind, tuple[type, ...]]] = {
    "model": (CodecCapabilities,),
    "parameter": (ParameterCodecCapabilities,),
    "media": (ClientMediaCodecCapabilities, ServerMediaCodecCapabilities),
    "schema": (SchemaCodecCapabilities,),
}
_REQUIRED_FIELDS: Final[dict[TypeUseRefRole, frozenset[str]]] = {
    "parameter": frozenset({"location", "name"}),
    "request_body": frozenset({"media_type"}),
    "response_body": frozenset({"status", "media_type"}),
    "response_header": frozenset({"status", "name"}),
    "request_encoding_header": frozenset({"name", "media_type", "encoding_property"}),
    "response_encoding_header": frozenset({"status", "name", "media_type", "encoding_property"}),
}
_OPTIONAL_FIELDS: Final[dict[TypeUseRefRole, frozenset[str]]] = {"parameter": frozenset({"media_type"})}


def _dotted(value: str) -> bool:
    return bool(value) and all(part.isidentifier() and not keyword.iskeyword(part) for part in value.split("."))


def _requirements(dependencies: tuple[str, ...], python_requires: str) -> None:
    if not python_requires or not all(dependencies) or len(set(dependencies)) != len(dependencies):
        msg = "dependencies must be distinct nonempty requirements and python_requires a nonempty specifier"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationRef:
    """Select a root operation use site by RFC 6901 pointer, in the root document unless another is named."""

    pointer: str
    document: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaRef:
    """Select a processed schema occurrence by RFC 6901 pointer, in the root document unless another is named."""

    pointer: str
    document: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaDirectionalUse:
    """Apply a registration to every use of one schema occurrence in one direction."""

    schema: SchemaRef
    direction: Direction


@dataclass(frozen=True, slots=True, kw_only=True)
class TypeUseRef:
    """Select one exact type use by its root operation, role, and the selectors that role requires."""

    operation: OperationRef | str
    role: TypeUseRefRole
    location: ParameterLocation | None = None
    name: str | None = None
    status: str | None = None
    media_type: str | None = None
    encoding_property: str | None = None

    def __post_init__(self) -> None:
        """Require exactly the selectors the role uses."""
        present = frozenset(
            name
            for name in ("location", "name", "status", "media_type", "encoding_property")
            if getattr(self, name) is not None
        )
        required = _REQUIRED_FIELDS[self.role]
        if not required <= present or present - required - _OPTIONAL_FIELDS.get(self.role, frozenset()):
            msg = f"A {self.role} use requires exactly {', '.join(sorted(required))}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class BuiltinCodecCompatibility:
    """Assert that custom models of one backend keep the builtin-v1 codec semantics."""

    name: str
    backend: DataModelType
    schemas: tuple[SchemaRef, ...] = ()
    contract: Literal["builtin-v1"] = "builtin-v1"
    dependencies: tuple[str, ...] = ()
    python_requires: str = ">=3.10"

    def __post_init__(self) -> None:
        """Require a name, the builtin-v1 contract, and valid requirements."""
        if not self.name or self.contract != "builtin-v1":
            msg = "A builtin codec compatibility declaration needs a name and the builtin-v1 contract"
            raise ValueError(msg)
        _requirements(self.dependencies, self.python_requires)


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelExportBinding:
    """Import the model of one schema variant from an explicit module of the model package."""

    schema: SchemaRef
    module: str
    symbol: str
    direction: Direction | Literal["neutral"] = "neutral"

    def __post_init__(self) -> None:
        """Require an absolute dotted module and one identifier."""
        if not _dotted(self.module) or not _dotted(self.symbol) or "." in self.symbol:
            msg = "An export binding needs an absolute dotted module and one identifier"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class CodecAdapterRegistration:
    """Register one adapter factory with its dependencies, capabilities, and applicable uses."""

    kind: AdapterKind
    name: str
    import_ref: str
    dependencies: tuple[str, ...]
    python_requires: str
    capabilities: RegistrationCapabilities
    uses: tuple[TypeUseRef | SchemaDirectionalUse, ...]
    api_version: Literal[1] = 1

    def __post_init__(self) -> None:
        """Require a name, a zero-argument factory reference, matching capabilities, and at least one use."""
        module, _, symbol = self.import_ref.partition(":")
        if not self.name or not _dotted(module) or not _dotted(symbol) or "." in symbol:
            msg = "An adapter registration needs a name and an 'absolute.module:Symbol' factory reference"
            raise ValueError(msg)
        if not isinstance(self.capabilities, _KIND_CAPABILITIES.get(self.kind, ())) or self.api_version != 1:
            msg = f"A {self.kind} adapter registration needs {self.kind} capabilities and API version 1"
            raise ValueError(msg)
        if not self.uses or len(set(self.uses)) != len(self.uses):
            msg = "An adapter registration needs distinct applicable uses"
            raise ValueError(msg)
        _requirements(self.dependencies, self.python_requires)


@dataclass(frozen=True, slots=True, kw_only=True)
class CodecDeclarations:
    """Collect one target's codec declarations for planning."""

    compatibility: tuple[BuiltinCodecCompatibility, ...] = ()
    exports: tuple[ModelExportBinding, ...] = ()
    adapters: tuple[CodecAdapterRegistration, ...] = ()
