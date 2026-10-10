"""Name generated API identifiers with the model generator's own configured naming rules.

Two kinds of names exist, as for models. A Python-only name (a method, handler, resource, module, or class) follows its
kind's convention whatever `--snake-case-field` says, the way model class names ignore it: functions, attributes and
modules are snake_case, classes UpperCamel. A name that stands for a wire name (a parameter or a body field) is named
as a model field named after that wire name is: `--snake-case-field`, `--aliases`, the special-field prefix options and
the field name delimiter apply to it.

A derived name never fails: in a scope that already holds it, it takes the model's duplicate suffix. An explicit name
(a configured name or an `--aliases` entry) is a user choice and is validated, never renamed.
"""

from __future__ import annotations

import keyword
import re
import unicodedata
from copy import copy
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._target_contract import LiteralScalar

if TYPE_CHECKING:
    from collections.abc import Iterable

    from datamodel_code_generator._target_contract import OperationContract
    from datamodel_code_generator.reference import FieldNameResolver, ModelResolver

__all__ = ("WINDOWS_DEVICES", "NameScope", "TargetNames", "explicit_name", "operation_basis")

_PLACEHOLDER: Final = re.compile(r"\{([^{}]*)\}")
_WORDS: Final = re.compile(r"[^\W_]+")
WINDOWS_DEVICES: Final = frozenset({
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
})


def explicit_name(value: object) -> bool:
    """Return whether an explicit name is a Python identifier: no keyword, no leading `__`, and in NFKC form."""
    return (
        isinstance(value, str)
        and value.isidentifier()
        and not keyword.iskeyword(value)
        and not value.startswith("__")
        and unicodedata.normalize("NFKC", value) == value
    )


def operation_basis(operation: OperationContract) -> str:
    """Return the text an operation's derived name comes from: its operationId, or its method and path.

    The path gives its literal words, and `by_` before the words of each placeholder; a path with neither is `root`.
    """
    operation_id = next((value for key, value in operation.facts if key == "operationId"), None)
    if (
        operation.explicit_operation_id
        and isinstance(operation_id, LiteralScalar)
        and isinstance(text := operation_id.value, str)
        and text
    ):
        return text
    parts: list[str] = []
    for index, piece in enumerate(_PLACEHOLDER.split(operation.path)):
        if words := _WORDS.findall(piece):
            parts.append(f"by_{'_'.join(words)}" if index % 2 else "_".join(words))
    return "_".join((operation.method.lower(), *(parts or ["root"])))


class NameScope:
    """The names one namespace already holds: its support names first, then each name claimed in order.

    A folded scope compares names case-insensitively, because its names become paths on case-insensitive file systems.
    """

    __slots__ = ("_folded", "_taken")

    def __init__(self, reserved: Iterable[str] = (), *, folded: bool = False) -> None:
        """Hold the support names the namespace defines itself."""
        self._folded = folded
        self._taken = {self._key(name) for name in reserved}

    def _key(self, name: str) -> str:
        return name.casefold() if self._folded else name

    def __contains__(self, name: object) -> bool:
        """Return whether the scope already holds a name."""
        return isinstance(name, str) and self._key(name) in self._taken

    def take(self, name: str) -> bool:
        """Hold an explicit name, returning False when the scope already holds it."""
        if (key := self._key(name)) in self._taken:
            return False
        self._taken.add(key)
        return True

    def claim(self, name: str, suffix: str | None = None, *, camel: bool = False) -> str:
        """Hold a derived name, suffixed as the models suffix a duplicate field, or with `camel` a duplicate class.

        A field takes `name_1`, `name_2`; a class `Name1`, `Name2`, or `NameSuffix`, `NameSuffix1` with a suffix.
        """
        delimiter = "" if camel else "_"
        candidate, count = name, 0
        while candidate in self:
            count += 1
            if camel and suffix:
                candidate = f"{name}{suffix}{count - 1 if count > 1 else ''}"
            else:
                candidate = f"{name}{delimiter}{count}"
        self._taken.add(self._key(candidate))
        return candidate


class TargetNames:
    """The model generator's configured class-name resolver, applied to the names of generated API code.

    `suffix` is the model's duplicate class name suffix, which `--duplicate-name-suffix` sets.
    """

    __slots__ = ("_function", "_resolver", "suffix")

    def __init__(self, resolver: FieldNameResolver, suffix: str | None = None) -> None:
        """Keep the resolver, and a copy of it that snake-cases every name as `--snake-case-field` would."""
        function = copy(resolver)
        function.snake_case_field = True
        function.original_delimiter = None
        self._resolver = resolver
        self._function = function
        self.suffix = suffix or None

    @classmethod
    def of(cls, model_resolver: ModelResolver) -> TargetNames:
        """Capture the naming rules of a parser's model resolver."""
        from datamodel_code_generator.reference import ModelType  # noqa: PLC0415

        suffixes = model_resolver.duplicate_name_suffix_map
        suffix = suffixes.get("model", suffixes.get("default")) or model_resolver.duplicate_name_suffix
        return cls(model_resolver.field_name_resolvers[ModelType.CLASS], suffix)

    def function(self, text: str, scope: NameScope | None = None) -> str:
        """Return the snake_case name of a method, handler, resource, or module, unique in its scope."""
        name = self._function.get_valid_name(text)
        return name if scope is None else scope.claim(name)

    def pascal(self, text: str, scope: NameScope | None = None) -> str:
        """Return the UpperCamel class name the models would give, unique in its scope by the model's class rule."""
        name = self._resolver.get_valid_name(text, ignore_snake_case_field=True, upper_camel=True)
        return name if scope is None else scope.claim(name, self.suffix, camel=True)

    def alias(self, wire_name: str) -> str | None:
        """Return the flat `--aliases` entry for a wire name, which names its argument as given."""
        return alias if isinstance(alias := self._resolver.aliases.get(wire_name), str) else None

    def argument(self, wire_name: str, scope: NameScope | None = None) -> str:
        """Return the name a model field named after a wire name takes, unique in its scope."""
        name = self._resolver.get_valid_field_name_and_alias(wire_name)[0]
        return name if scope is None else scope.claim(name)
