"""Name model types and support code in one generated target module, as model modules import each other.

A target module imports a generated model the way a model module imports another generated module: the model's module,
then the model as its attribute, or the model itself under `--use-exact-imports`. A model type is spelled from the hint
the binding seam captured from the model generator's own rendering, and support annotations around it are composed
with the model generator's configured type class, so its union and collection options apply to them too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, TypeAlias

from datamodel_code_generator._openapi_codec_plan import artifact_module
from datamodel_code_generator._target_contract import (
    BuiltinType,
    GeneratedSymbolType,
    ImportedType,
    ModelHint,
    NoneType,
    UnionType,
)
from datamodel_code_generator.imports import Import

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from datamodel_code_generator._target_contract import GeneratedTypeContractBatch, SymbolId, TypeView
    from datamodel_code_generator.types import DataType

__all__ = ("TargetModule", "TypeComposer", "TypeNames")

_BUILTINS: Final = (
    "bool",
    "bytes",
    "complex",
    "dict",
    "float",
    "frozenset",
    "int",
    "list",
    "object",
    "set",
    "str",
    "tuple",
)
_LOCAL: Final = (None, "")
_PART: Final = "\x01"
_Identity: TypeAlias = tuple[str | None, str]


class TypeComposer:
    """Compose spelled types with the model generator's configured type class, as its type hints compose them.

    Each shape of composite renders once, over placeholder parts, and its text is filled with the parts' spellings. A
    part that spells None, or no type, renders as it is, since the type class reads such text itself.
    """

    def __init__(self, hint_type: type[DataType]) -> None:
        """Keep the type class and the renderings of each composite shape."""
        self.hint_type = hint_type
        self.templates: dict[tuple[object, ...], tuple[list[str], tuple[Import, ...]]] = {}
        self.composed: dict[tuple[object, ...], tuple[str, tuple[Import, ...]]] = {}

    def render(
        self,
        style: tuple[tuple[str, object], ...],
        base: str,
        members: tuple[str, ...],
        key: str | None,
        flags: tuple[tuple[str, object], ...],
    ) -> tuple[str, tuple[Import, ...]]:
        leaf = self.hint_type.model_construct
        values: dict[str, Any] = {
            **dict(style),
            "type": base,
            "data_types": [leaf(type=member) for member in members],
            "dict_key": None if key is None else leaf(type=key),
            **dict(flags),
        }
        node: DataType = leaf(**values)
        return node.type_hint, tuple(node.imports)

    def compose(
        self,
        style: tuple[tuple[str, object], ...] = (),
        *,
        base: str = "",
        members: tuple[str, ...] = (),
        key: str | None = None,
        **flags: object,
    ) -> tuple[str, tuple[Import, ...]]:
        """Return a composite of spelled parts and the names its text writes as they are.

        `style` holds the union and collection options of the type the composite stands for, `base` the type a
        container or optional wraps, `members` a union's or a tuple's types, and `key` a mapping's key type.
        """
        frozen = tuple(flags.items())
        if (found := self.composed.get(cache := (style, base, members, key, frozen))) is not None:
            return found
        values = (*((base,) if base else ()), *members, *(() if key is None else (key,)))
        if len(set(members)) != len(members) or any("None" in text or not text for text in values):
            found = self.composed[cache] = self.render(style, base, members, key, frozen)
            return found
        shape = (style, bool(base), len(members), key is not None, frozen)
        if (template := self.templates.get(shape)) is None:
            slots = [f"{_PART}{index}{_PART}" for index in range(len(values))]
            text, imports = self.render(
                style,
                slots[0] if base else "",
                tuple(slots[bool(base) : len(slots) - (key is not None)]),
                slots[-1] if key is not None else None,
                frozen,
            )
            template = self.templates[shape] = text.split(_PART), imports
        pieces, imports = template
        text = "".join(values[int(piece)] if index % 2 else piece for index, piece in enumerate(pieces))
        found = self.composed[cache] = text, imports
        return found


class TypeNames:
    """Where a target package's modules import each generated model from, and how they compose model types.

    `exact` imports each model by name, as `--use-exact-imports` does for models, and `overrides` moves imported names
    to other modules, as `--import-overrides` does.
    """

    def __init__(
        self, batch: GeneratedTypeContractBatch, *, exact: bool = False, overrides: Mapping[str, str] | None = None
    ) -> None:
        """Index each emitted symbol's module and name, and keep the batch's type class and fixed names."""
        self.symbols: dict[SymbolId, tuple[str, str]] = {
            symbol.id: (artifact_module(symbol.artifact), symbol.name)
            for symbol in batch.symbols
            if symbol.artifact is not None
        }
        self.exact = exact
        self.overrides = overrides or {}
        assert batch.hint_type is not None
        self.composer = TypeComposer(batch.hint_type)
        self.fixed = tuple(dict.fromkeys(self.identity(item) for item in batch.hint_imports))

    def identity(self, import_: Import) -> _Identity:
        """Return the module and name an import binds, after the import overrides."""
        return self.resolve(import_.from_, import_.import_)

    def resolve(self, module: str | None, name: str) -> _Identity:
        """Return the module and name a name imported from a module binds, after the import overrides."""
        override = self.overrides.get(name) if module != "__future__" else None
        return override or module, name


class TargetModule:
    """One generated target module: its name scope, imports, and the spellings of model and support types.

    Every name is bound to one identity: a fixed name a model type spells keeps its name, and any other import takes
    the model generator's `Name_1` suffix when its name is already taken. A qualified module names each type by its
    full path and records no imports, as digests and messages read it.
    """

    def __init__(
        self, names: TypeNames, reserved: Iterable[str] = (), *, level: int = 0, qualified: bool = False
    ) -> None:
        """Reserve the names the module defines, the builtins, and the fixed names that model types spell."""
        self.names = names
        self.level = level
        self.qualified = qualified
        self.taken: dict[str, object] = dict.fromkeys((*reserved, *_BUILTINS), _LOCAL)
        for identity in names.fixed:
            if identity[0] is not None:
                self.taken.setdefault(identity[1], identity)
        self.bound: dict[_Identity, str] = {}
        self.spelled: dict[object, str] = {}

    def _claim(self, identity: _Identity, preferred: str) -> str:
        if (bound := self.bound.get(identity)) is not None:
            return bound
        name, count = preferred, 0
        while self.taken.get(name, identity) != identity:
            count += 1
            name = f"{preferred}_{count}"
        self.taken[name] = identity
        self.bound[identity] = name
        return name

    def name(self, module: str, name: str) -> str:
        """Return the local name of a name imported from an absolute module, or from a relative one.

        A qualified module spells only model types, and names no support code.
        """
        if (found := self.spelled.get((module, name))) is None:
            identity = (module, name) if module.startswith(".") else self.names.resolve(module, name)
            found = self.spelled[module, name] = self._claim(identity, name)
        return found

    def local(self, module: str, name: str) -> str:
        """Return the local name of a name imported from a module of the generated package."""
        return self.name("." * self.level + module, name)

    def root(self, name: str) -> str:
        """Return the local name of a module at the package root."""
        return self.name("." * self.level, name)

    def module(self, path: str) -> str:
        """Return the local name of an absolute module, imported as a whole."""
        return path if self.qualified else self._claim((None, path), path.replace(".", "_"))

    def imported(self, import_: Import) -> str:
        """Return the local name of a library name a model type spells, imported as the models import it."""
        module, name = identity = self.names.identity(import_)
        return (
            self.module(name)
            if module is None
            else f"{module}.{name}"
            if self.qualified
            else self._claim(identity, name)
        )

    def symbol(self, symbol: SymbolId) -> str:
        """Return the spelling of a generated model: an attribute of its module, or its own name with exact imports.

        A module below the model package is imported from its parent, as a model module imports another one.
        """
        module, name = self.names.symbols[symbol]
        if self.qualified:
            return f"{module}.{name}"
        if self.names.exact:
            return self._claim((module, name), name)
        parent, _, leaf = module.rpartition(".")
        return f"{self._claim((parent, leaf), leaf) if parent else self.module(module)}.{name}"

    def hint(self, value: TypeView | ModelHint, *, static: bool = True) -> str:
        """Return the spelling of a model type in this module, statically or as the annotation that validates it.

        A union a target composed of model types, which has no hint, is composed as the model generator writes unions.
        """
        match value:
            case GeneratedSymbolType():
                return self.symbol(value.symbol)
            case BuiltinType():
                return value.name
            case NoneType():
                return "None"
            case ImportedType():
                return ".".join((self.imported(value.import_), *value.qualified_suffix))
            case ModelHint():
                hint: ModelHint | None = value
            case UnionType() if value.hint is None:
                return self.union(*(self.hint(member, static=static) for member in value.members))
            case _:
                hint = value.hint
        assert hint is not None, "a type view without a hint is a union a target composed"
        text = hint.static if static else hint.annotation
        if (found := self.spelled.get(text)) is None:
            if not self.qualified:
                for item in text.imports:
                    self.imported(item)
            found = self.spelled[text] = "".join(
                part
                if isinstance(part, str)
                else self.imported(part)
                if isinstance(part, Import)
                else self.symbol(part)
                for part in text.parts
            )
        return found

    def _composed(self, kind: str, *parts: str) -> str:
        """Return a composite of spelled types this module writes, importing the names its text writes once."""
        if (found := self.spelled.get(key := (kind, *parts))) is None:
            composer = self.names.composer
            match kind:
                case "union":
                    text, imports = composer.compose(members=parts, preserve_union_member_order=True)
                case "optional":
                    text, imports = composer.compose(base=parts[0], is_optional=True)
                case _:
                    text, imports = composer.compose(base=parts[0], is_sequence=True)
            if not self.qualified:
                for item in imports:
                    self.imported(item)
            found = self.spelled[key] = text
        return found

    def union(self, *parts: str) -> str:
        """Return the union of spelled types, each once in the order given, as the model generator writes unions."""
        members = tuple(dict.fromkeys(parts))
        return "".join(members) if len(members) < 2 else self._composed("union", *members)  # noqa: PLR2004

    def optional(self, part: str) -> str:
        """Return a spelled type or None, as the model generator writes an optional type."""
        return self._composed("optional", part)

    def sequence(self, part: str) -> str:
        """Return a read-only sequence of a spelled type, as the model generator writes one."""
        return self._composed("sequence", part)

    def imports(self) -> str:
        """Return the module's import statements: whole modules in path order, then names by module."""
        modules: list[str] = []
        grouped: dict[str, list[str]] = {}
        for (module, name), alias in sorted(self.bound.items(), key=lambda item: (item[0][0] or "", item[0][1])):
            if module is None:
                modules.append(f"import {name}" if alias == name else f"import {name} as {alias}")
            else:
                grouped.setdefault(module, []).append(name if alias == name else f"{name} as {alias}")
        return "\n".join((*modules, *(f"from {module} import {', '.join(names)}" for module, names in grouped.items())))
