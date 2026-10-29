"""Hashability: whether the instances of a model hash by value, as set items and as dict keys.

A model's chain is the model and the generated models it inherits from, in the order a walk of the reference graph
over its bases meets them (`ReferenceGraph.preorder`); the first class of the chain that sets ``frozen`` decides
whether its instances are frozen. ``keeps_builtin_hash`` decides whether a set item model keeps the hash of its
backend class, which hashes frozen instances by their values, or hashes by identity (``__hash__ = object.__hash__``);
``set_items_may_keep_builtin_hash`` decides whether a parse whose backend allows it lets any set item model keep it.
``dict_key_takes_root_type`` decides whether a dict key that names a root model takes the model's root type, as the
model's instances cannot be dict keys. ``value_hashes`` and ``field_value_hashes`` decide whether the values Pydantic
validates for a field hash as builtin values.

Every input is an immutable fact; the adapters read them from the live objects: ``DataModel._hash_class_facts`` (with
the Pydantic v2 ``BaseModel`` row for ``frozen`` and the builtin class), the backend capability
``DataModel._FIELD_VALUE_HASHES`` (the Pydantic v2 row reads its exact field class),
``model/base.py:set_item_models_keeping_builtin_hash`` for
``parser/_passes/render_prep.py:mark_set_item_models_hashable`` and ``DataModel._keys_take_root_type`` for
``parser/_passes/modules.py:type_dict_keys_by_root_types``.
"""

from __future__ import annotations

from types import NoneType
from typing import TYPE_CHECKING, final

from datamodel_code_generator._records import record

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from datamodel_code_generator._python_type_annotation import PythonTypeExpr

__all__ = [
    "HashClassFacts",
    "ValueHashFacts",
    "dict_key_takes_root_type",
    "field_value_hashes",
    "frozen_by_config",
    "keeps_builtin_hash",
    "set_items_may_keep_builtin_hash",
    "value_hashes",
]

_BUILTIN_VALUE_KINDS: frozenset[type] = frozenset({str, int, float, bool, NoneType})
"""The kinds of literal and default values whose hash is builtin."""
_BUILTIN_HASH_SCALARS: frozenset[str] = frozenset({"str", "int", "float", "bool", "None"})
"""The builtin scalar types whose validated values have builtin hashes; bytes, date and UUID can keep an unhashable
subclass instance."""


@final
@record
class HashClassFacts:
    """The facts of one class of a model's chain that its hashability reads."""

    frozen: bool | None = None
    """The ``frozen`` its own configuration sets, or None where it sets none."""
    builtin_hash: bool = False
    """The class is the backend's own class, whose frozen instances hash by their values."""
    custom_base_class: bool = False
    user_class_code: bool = False
    """The class has methods, decorators, validators or class body lines from its template data."""
    custom_template_dir: bool = False
    """The class renders from a template directory other than the built-in one."""


@final
@record(eq=False)
class ValueHashFacts:
    """The facts of one data type of a field that tell whether the values validated for it hash as builtin values."""

    container: bool
    """The type is a list, sequence, dict, mapping or set."""
    opaque: bool
    """The type names a model, holds member types, is a custom type or a function, or takes keyword arguments: model
    instances, frozen ones included, can keep unhashable user subclasses."""
    python_type: PythonTypeExpr | None
    literals: tuple[object, ...]
    builtin_name: str | None
    """The type's name where it imports nothing."""


def frozen_by_config(chain: Iterable[HashClassFacts]) -> bool:
    """Return whether the first class of ``chain`` that sets ``frozen`` freezes the instances."""
    return next((facts.frozen for facts in chain if facts.frozen is not None), None) is True


def set_items_may_keep_builtin_hash(*, user_imports: bool) -> bool:
    """Return whether a set item model may keep its builtin hash in a parse whose backend allows it.

    User imports may shadow the builtin annotations that ``value_hashes`` recognizes.
    """
    return not user_imports


def keeps_builtin_hash(*, chain: Sequence[HashClassFacts], field_values: Iterable[bool]) -> bool:
    """Return whether a set item model keeps the builtin hash of its class instead of hashing by identity.

    Its chain freezes it, every class of the chain is the backend's own class without a custom base class, class code
    or custom templates, and the value of each field that is no ClassVar hashes as a builtin value (``field_values``,
    read in order until one does not).
    """
    return (
        frozen_by_config(chain)
        and all(
            facts.builtin_hash and not (facts.custom_base_class or facts.user_class_code or facts.custom_template_dir)
            for facts in chain
        )
        and all(field_values)
    )


def dict_key_takes_root_type(
    *, chain: Sequence[HashClassFacts], generated_checks: bool, custom_root_template: bool
) -> bool:
    """Return whether a dict key that names a root model takes the model's root type.

    The model's instances cannot be dict keys unless its chain freezes it, and the key takes the root type only where
    the model checks nothing beyond it: no class code of its own (``chain[0]``), no class body lines or runtime object
    rules of the generator (``generated_checks``) and no custom root template.
    """
    return not (frozen_by_config(chain) or chain[0].user_class_code or generated_checks or custom_root_template)


def field_value_hashes(
    *, default_factory: bool, default: object, defaulted: bool, values: Iterable[ValueHashFacts]
) -> bool:
    """Return whether the values validated for a field hash as builtin values.

    A default factory or a default of another kind can give a value that Pydantic does not validate.
    """
    return (
        not default_factory
        and (not defaulted or type(default) in _BUILTIN_VALUE_KINDS)
        and all(map(value_hashes, values))
    )


def value_hashes(facts: ValueHashFacts) -> bool:
    """Return whether the values validated for a data type hash as builtin values."""
    if facts.container or facts.opaque:
        return False
    if facts.python_type is not None:
        return _python_type_hashes(facts.python_type)
    if facts.literals:
        return all(type(value) in _BUILTIN_VALUE_KINDS for value in facts.literals)
    return facts.builtin_name in _BUILTIN_HASH_SCALARS


def _python_type_hashes(expression: PythonTypeExpr) -> bool:
    """Return whether the values validated for a bound Python type are builtin scalars or tuples and frozensets of them.

    Pydantic 2.0 can keep unhashable subclasses in a union of several types, so a union holds one type and None.
    """
    from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
        PythonTypeBoundName,
        PythonTypeEllipsis,
        PythonTypeName,
        PythonTypeSubscript,
        PythonTypeUnion,
    )

    pending = [expression]
    while pending:
        expression = pending.pop()
        if isinstance(expression, PythonTypeUnion):
            if len(expression.items) != 2 or PythonTypeName("None") not in expression.items:  # noqa: PLR2004
                return False
            pending.extend(expression.items)
            continue
        arguments = None
        if isinstance(expression, PythonTypeSubscript):
            arguments = expression.arguments
            expression = expression.base
        module, name = "builtins", expression.value if isinstance(expression, PythonTypeName) else ""
        if isinstance(expression, PythonTypeBoundName):
            module, name = expression.import_from, expression.import_name
        if arguments is None:
            if module != "builtins" or name not in _BUILTIN_HASH_SCALARS:
                return False
            continue
        match module, name:
            case ("builtins", "tuple") | ("typing", "Tuple"):
                match arguments:
                    case (_, PythonTypeEllipsis()):
                        arguments = arguments[:1]
            case ("builtins", "frozenset") | ("typing", "FrozenSet") if len(arguments) == 1:
                pass
            case _:
                return False
        pending.extend(arguments)
    return True
