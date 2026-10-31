"""Shared helpers for schema converter parsers."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from datamodel_code_generator._free_names import first_free
from datamodel_code_generator._name_words import pascal_words

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

JsonSchemaT = TypeVar("JsonSchemaT", bound=dict[str, Any])
DefinitionKeyT = TypeVar("DefinitionKeyT")


def _copy_schema(schema: JsonSchemaT) -> JsonSchemaT:
    return copy.deepcopy(schema)


def _unique_name(name: str, used_names: set[str]) -> str:
    return first_free(name, used_names.__contains__, "joined-from-2")[0]


def _namespace_name(namespace: str | None, to_class_title: Callable[[str], str]) -> str:
    if not namespace:
        return "NoNamespace"
    return pascal_words(namespace, title=to_class_title) or "Namespace"


def _name_definitions(
    qualified_names: Mapping[DefinitionKeyT, tuple[str, str | None, str]],
    to_class_title: Callable[[str], str],
    *,
    lone: Literal["titled", "as-written"],
) -> dict[DefinitionKeyT, str]:
    """Return the definition name of each key of a converted schema, given its kind, namespace and local name.

    Keys are named in the order of their local names, and the keys of one local name in the order given. A local name
    that one key declares is its name, titled or as written (``lone``); otherwise a name is the namespace's name when
    the keys' namespaces differ, the titled local name, and the titled kind when the namespace and local name are
    declared with several kinds (a converter whose names have no kind passes ``""``). A name taken before takes the
    first free number (``joined-from-2``).
    """
    keys_by_local: dict[str, list[DefinitionKeyT]] = {}
    for key, (_, _, local) in qualified_names.items():
        keys_by_local.setdefault(local, []).append(key)

    names: dict[DefinitionKeyT, str] = {}
    used_names: set[str] = set()
    for local, keys in sorted(keys_by_local.items()):
        namespaces = {qualified_names[key][1] for key in keys}
        for key in keys:
            kind, namespace, _ = qualified_names[key]
            if len(keys) == 1:
                name = to_class_title(local) if lone == "titled" else local
            else:
                namespace_name = _namespace_name(namespace, to_class_title) if len(namespaces) > 1 else ""
                same_qname_count = sum(qualified_names[other][1:] == (namespace, local) for other in keys)
                kind_name = to_class_title(kind) if same_qname_count > 1 else ""
                name = f"{namespace_name}{to_class_title(local)}{kind_name}"
            names[key] = candidate = _unique_name(name, used_names)
            used_names.add(candidate)
    return names
