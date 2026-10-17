"""Schema signals for option recommendations: one walk over the loaded schema document."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, get_args

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

SignalName: TypeAlias = Literal[
    "components",
    "operations",
    "inline_objects",
    "inline_enums",
    "component_enums",
    "all_enums",
    "max_inline_depth",
    "shared_inline_names",
    "shared_inline_object_names",
    "shared_inline_enum_names",
    "inline_names_equal_component",
    "duplicate_inline_objects",
    "duplicate_inline_shapes",
    "duplicate_inline_enums",
    "duplicate_inline_enum_shapes",
    "parent_copy_union_branches",
    "titled_components",
    "component_title_duplicate_groups",
    "component_titles_in_duplicate_groups",
    "component_titles_equal_own_key",
    "component_titles_equal_other_key",
    "inline_titled_objects",
    "inline_title_max_repeat",
    "unions",
    "discriminated_unions",
    "inline_union_variants",
    "inline_object_union_variants",
    "inline_scalar_union_variants",
    "inline_titled_union_variants",
    "inline_tagged_union_branches",
    "tagged_behind_ref",
    "single_key_union_branches",
    "single_key_required_open",
    "single_value_enums",
    "single_value_enum_union_branches",
    "dotted_keys",
    "dotted_key_namespaces",
    "dotted_namespace_reference_cycles",
    "root_is_object",
    "root_title_words",
    "estimated_models",
]
FactName: TypeAlias = Literal[
    "schema",
    "shared_inline_name_examples",
    "inline_title_examples",
    "duplicate_title_examples",
    "key_style",
    "root_title",
]
SchemaKind: TypeAlias = Literal["openapi", "jsonschema", "asyncapi"]
SIGNAL_NAMES: frozenset[str] = frozenset(get_args(SignalName))
FACT_NAMES: frozenset[str] = frozenset(get_args(FactName))

_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "patch", "head", "options", "trace"})
_SCHEMA_MAP_KEYWORDS = ("properties", "patternProperties", "dependentSchemas")
_SUBSCHEMA_KEYWORDS = (
    "allOf",
    "anyOf",
    "oneOf",
    "prefixItems",
    "items",
    "additionalItems",
    "additionalProperties",
    "unevaluatedItems",
    "unevaluatedProperties",
    "contains",
    "propertyNames",
    "not",
    "if",
    "then",
    "else",
)
_SHAPE_IGNORED_KEYWORDS = frozenset({"description", "title", "example", "examples", "$comment"})
_CONSTRAINT_ONLY_KEYWORDS = frozenset({"type", "properties", "required", "description", "title", "$comment"})
_NORMALIZE = re.compile(r"[^a-z0-9]")
_EXAMPLE_LIMIT = 4
_TAG_REFERENCE_DEPTH = 5


@dataclass(frozen=True, slots=True)
class Signals:
    """Counts and example texts measured on one schema document."""

    counts: Mapping[str, int]
    facts: Mapping[str, str]


def _normalize(name: str) -> str:
    return _NORMALIZE.sub("", name.lower())


def _module(key: str) -> str:
    return key.rpartition(".")[0]


def _is_object(node: Mapping[str, Any]) -> bool:
    return isinstance(properties := node.get("properties"), dict) and bool(properties)


def _is_enum(node: Mapping[str, Any]) -> bool:
    return isinstance(values := node.get("enum"), list) and any(value is not None for value in values)


def _is_null(node: Mapping[str, Any]) -> bool:
    return node.get("type") in ("null", ["null"]) or node.get("enum") == [None]


def _single_literal(node: object) -> tuple[str, object] | None:
    """Return the form and value of a schema that allows exactly one value."""
    if not isinstance(node, dict):
        return None
    if "const" in node:
        return "const", node["const"]
    if isinstance(values := node.get("enum"), list) and len(values) == 1:
        return "enum", values[0]
    return None


def _subschemas(node: Mapping[str, Any]) -> Iterator[object]:
    """Yield the subschemas of a schema object that are not keyed by a name."""
    for keyword in _SUBSCHEMA_KEYWORDS:
        value = node.get(keyword)
        yield from value if isinstance(value, list) else (value,)


def _key_style(key: str) -> str:
    if "." in key:
        return "dotted"
    if "-" in key:
        return "kebab-case"
    if "_" in key:
        return "snake_case"
    if key[:1].isupper():
        return "PascalCase"
    return "camelCase" if any(char.isupper() for char in key) else "lowercase"


def _example_text(counter: Counter[str]) -> str:
    return ", ".join(f"{name} x{count}" for name, count in counter.most_common(_EXAMPLE_LIMIT))


def _resolve_local(document: Mapping[str, Any], ref: object) -> object:
    """Return the schema a local JSON Pointer reference points at, or None."""
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    node: object = document
    for part in ref[2:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _components(document: Mapping[str, Any], kind: SchemaKind) -> dict[str, Any]:
    """Return the named schemas of a document: $defs and definitions, or components/schemas."""
    if kind == "jsonschema":
        return {
            key: value
            for keyword in ("$defs", "definitions")
            if isinstance(section := document.get(keyword), dict)
            for key, value in section.items()
        }
    section = components.get("schemas") if isinstance(components := document.get("components"), dict) else None
    return section if isinstance(section, dict) else {}


def _component_refs(kind: SchemaKind, components: Iterable[str]) -> dict[str, str]:
    prefixes = ("#/$defs/", "#/definitions/") if kind == "jsonschema" else ("#/components/schemas/",)
    return {f"{prefix}{key}": key for key in components for prefix in prefixes}


def _schema_label(document: Mapping[str, Any], kind: SchemaKind) -> str:
    if kind == "jsonschema":
        schema_uri = document.get("$schema")
        return f"JSON Schema ({schema_uri})" if isinstance(schema_uri, str) else "JSON Schema"
    return f"{'OpenAPI' if kind == 'openapi' else 'AsyncAPI'} {document.get(kind)}"


class _Walker:
    """Collect the counts of one document in a single pass over its named schemas."""

    def __init__(self, document: Mapping[str, Any], refs: Mapping[str, str]) -> None:
        self.document = document
        self.refs = refs
        self.counts: Counter[str] = Counter()
        self.name_parents: defaultdict[str, set[str]] = defaultdict(set)
        self.object_name_parents: defaultdict[str, set[str]] = defaultdict(set)
        self.enum_name_parents: defaultdict[str, set[str]] = defaultdict(set)
        self.inline_titles: Counter[str] = Counter()
        self.shape_ids: dict[object, int] = {}
        self.object_shapes: Counter[int] = Counter()
        self.enum_shapes: Counter[int] = Counter()
        self.references: defaultdict[str, set[str]] = defaultdict(set)
        self.max_depth = 0

    def shape(self, node: object) -> int:
        """Return an interned id of a schema's structure, ignoring documentation keywords."""
        key: object = ("v", type(node).__name__, repr(node))
        if isinstance(node, dict):
            key = tuple(
                (name, self.shape(value))
                for name, value in sorted(node.items())
                if name not in _SHAPE_IGNORED_KEYWORDS and not name.startswith("x-")
            )
        elif isinstance(node, list):
            key = ("l", *(self.shape(value) for value in node))
        return self.shape_ids.setdefault(key, len(self.shape_ids))

    def count_single_value_enum(self, node: Mapping[str, Any]) -> None:
        """Count a schema whose enum has exactly one value and that declares no const."""
        values = node.get("enum")
        self.counts["single_value_enums"] += isinstance(values, list) and len(values) == 1 and "const" not in node

    def walk_named(self, key: str, node: object) -> None:
        """Walk one named schema; the schema itself is the first parent model."""
        if isinstance(node, dict):
            self.count_single_value_enum(node)
            self.walk_children(node, parent=key, name=_normalize(key), depth=0, component=key)

    def walk(self, node: object, *, parent: str, name: str, depth: int, component: str) -> None:
        """Walk one inline schema reached under a parent model through a property or keyword."""
        if not isinstance(node, dict) or _is_null(node):
            return
        if isinstance(ref := node.get("$ref"), str):
            if (target := self.refs.get(ref)) is not None:
                self.references[component].add(target)
            return
        self.count_single_value_enum(node)
        if _is_enum(node):
            self.counts["inline_enums"] += 1
            self.name_parents[name].add(parent)
            self.enum_name_parents[name].add(parent)
            self.enum_shapes[self.shape(node)] += 1
        if _is_object(node):
            self.counts["inline_objects"] += 1
            self.name_parents[name].add(parent)
            self.object_name_parents[name].add(parent)
            self.object_shapes[self.shape(node)] += 1
            if isinstance(title := node.get("title"), str) and title.strip():
                self.counts["inline_titled_objects"] += 1
                self.inline_titles[title.strip()] += 1
            depth += 1
            self.max_depth = max(self.max_depth, depth)
            parent = f"{parent}/{name}"
        self.walk_children(node, parent=parent, name=name, depth=depth, component=component)

    def walk_children(self, node: Mapping[str, Any], *, parent: str, name: str, depth: int, component: str) -> None:
        """Walk the subschemas of one schema object."""
        for keyword in _SCHEMA_MAP_KEYWORDS:
            members = node.get(keyword)
            for member_name, member in members.items() if isinstance(members, dict) else ():
                self.walk(member, parent=parent, name=_normalize(str(member_name)), depth=depth, component=component)
        for keyword in ("oneOf", "anyOf"):
            if isinstance(branches := node.get(keyword), list):
                self.count_variants(node, [branch for branch in branches if isinstance(branch, dict)])
        for member in _subschemas(node):
            self.walk(member, parent=parent, name=name, depth=depth, component=component)

    def count_variants(self, node: Mapping[str, Any], branches: list[dict[str, Any]]) -> None:
        """Count one oneOf/anyOf: its inline variants, and the literal tags and keys that could name them."""
        real = [branch for branch in branches if not _is_null(branch)]
        if len(real) > 1:
            self.counts["unions"] += 1
            self.counts["discriminated_unions"] += "discriminator" in node
            for branch in real:
                if "$ref" in branch:
                    continue
                self.counts["inline_union_variants"] += 1
                self.counts["parent_copy_union_branches"] += self.copies_parent(node, branch)
                self.counts["inline_titled_union_variants"] += isinstance(branch.get("title"), str)
                is_object = _is_object(branch) or isinstance(branch.get("allOf"), list)
                self.counts["inline_object_union_variants" if is_object else "inline_scalar_union_variants"] += 1
        self.count_tags([branch for branch in branches if "$ref" not in branch])

    def count_tags(self, branches: list[dict[str, Any]]) -> None:
        """Count inline branches named by a distinct literal tag or a single required key, as generation names them."""
        values: defaultdict[str, list[str]] = defaultdict(list)
        for branch in branches:
            for name, (_, value) in self.tags(branch).items():
                values[name].append(json.dumps(value, sort_keys=True))
        distinct = {name for name, items in values.items() if len(set(items)) == len(items)}
        for branch in branches:
            if not isinstance(properties := branch.get("properties"), dict):
                self.counts["tagged_behind_ref"] += self.tagged_behind_ref(branch)
                continue
            if forms := {form for name, (form, _) in self.tags(branch).items() if name in distinct}:
                self.counts["inline_tagged_union_branches"] += 1
                self.counts["single_value_enum_union_branches"] += forms == {"enum"}
            else:
                self.counts["tagged_behind_ref"] += self.tagged_behind_ref(branch)
            if len(properties) == 1 and "oneOf" not in branch and "anyOf" not in branch:
                self.counts["single_key_union_branches"] += 1
                ((key, value),) = properties.items()
                self.counts["single_key_required_open"] += (
                    key in (branch.get("required") or ()) and _single_literal(value) is None
                )

    @staticmethod
    def copies_parent(node: Mapping[str, Any], branch: Mapping[str, Any]) -> bool:
        """Return whether a branch only constrains properties of the object that holds the union.

        Generation copies the holder's properties into a model for each such branch.
        """
        parent_properties = node.get("properties")
        properties = branch.get("properties", {})
        return (
            isinstance(parent_properties, dict)
            and bool(parent_properties)
            and branch.keys() <= _CONSTRAINT_ONLY_KEYWORDS
            and ("required" in branch or "properties" in branch)
            and isinstance(properties, dict)
            and all(value is True or value == {} or name in parent_properties for name, value in properties.items())
        )

    @staticmethod
    def tags(branch: Mapping[str, Any]) -> dict[str, tuple[str, object]]:
        """Return the literal tag properties declared directly on one schema."""
        properties = branch.get("properties")
        return {
            name: literal
            for name, value in (properties.items() if isinstance(properties, dict) else ())
            if (literal := _single_literal(value)) is not None
        }

    def tagged_behind_ref(self, branch: Mapping[str, Any], depth: int = 0) -> bool:
        """Return whether a schema the branch references or merges through allOf declares a literal tag."""
        if depth > _TAG_REFERENCE_DEPTH:
            return False
        groups = [branch.get("allOf")]
        if isinstance(target := _resolve_local(self.document, branch.get("$ref")), dict):
            if self.tags(target):
                return True
            groups.append(target.get("allOf"))
        return any(
            isinstance(member, dict) and (bool(self.tags(member)) or self.tagged_behind_ref(member, depth + 1))
            for group in groups
            if isinstance(group, list)
            for member in group
        )


def _reachable(start: str, edges: Mapping[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        for target in edges.get(stack.pop(), ()):
            if target not in seen:
                seen.add(target)
                stack.append(target)
    return seen


def _reference_cycles(references: Mapping[str, set[str]]) -> int:
    """Count groups of two or more dotted-key namespaces whose schemas reference each other in a cycle."""
    edges: defaultdict[str, set[str]] = defaultdict(set)
    for source, targets in references.items():
        edges[_module(source)].update(_module(target) for target in targets)
    reach = {module: _reachable(module, edges) for module in edges}
    groups = {
        frozenset({module, *(other for other in reach[module] if module in reach.get(other, ()))}) for module in reach
    }
    return sum(len(group) > 1 for group in groups)


def _count_titles(counts: Counter[str], titles: Mapping[str, str], components: Iterable[str]) -> Counter[str]:
    """Count component titles that collide or repeat keys; return the shared titles with their group sizes."""
    keys = {_normalize(key) for key in components}
    groups = Counter(_normalize(title) for title in titles.values())
    shared = {title: count for title, count in groups.items() if count > 1}
    counts["titled_components"] = len(titles)
    counts["component_title_duplicate_groups"] = len(shared)
    counts["component_titles_in_duplicate_groups"] = sum(shared.values())
    counts["component_titles_equal_own_key"] = sum(
        _normalize(title) == _normalize(key) for key, title in titles.items()
    )
    counts["component_titles_equal_other_key"] = sum(
        (normalized := _normalize(title)) != _normalize(key) and normalized in keys for key, title in titles.items()
    )
    return Counter({title: shared[_normalize(title)] for title in titles.values() if _normalize(title) in shared})


def _count_names(counts: Counter[str], walker: _Walker, components: Iterable[str]) -> Counter[str]:
    """Count inline model names shared by parents or taken by components; return the shared names."""
    keys = {_normalize(key) for key in components}
    shared = Counter({name: len(parents) for name, parents in walker.name_parents.items() if len(parents) > 1})
    counts["shared_inline_names"] = len(shared)
    counts["shared_inline_object_names"] = sum(len(parents) > 1 for parents in walker.object_name_parents.values())
    counts["shared_inline_enum_names"] = sum(len(parents) > 1 for parents in walker.enum_name_parents.values())
    counts["inline_names_equal_component"] = sum(name in keys for name in walker.name_parents)
    for repeats, shapes, groups in (
        ("duplicate_inline_objects", walker.object_shapes, "duplicate_inline_shapes"),
        ("duplicate_inline_enums", walker.enum_shapes, "duplicate_inline_enum_shapes"),
    ):
        repeated = [count for count in shapes.values() if count > 1]
        counts[repeats] = sum(repeated)
        counts[groups] = len(repeated)
    return shared


def measure_signals(document: Mapping[str, Any], kind: SchemaKind) -> Signals:
    """Measure the naming-relevant signals of one OpenAPI, AsyncAPI, or JSON Schema document."""
    components = _components(document, kind)
    walker = _Walker(document, _component_refs(kind, components))
    counts = walker.counts
    for key, node in components.items():
        walker.walk_named(key, node)
    named = {key: node for key, node in components.items() if isinstance(node, dict)}
    counts["component_enums"] = sum(_is_enum(node) for node in named.values())
    titles = {
        key: title.strip()
        for key, node in named.items()
        if isinstance(title := node.get("title"), str) and title.strip()
    }
    root_title = ""
    if kind == "jsonschema":
        root = {key: value for key, value in document.items() if key not in {"definitions", "$defs"}}
        walker.walk_children(root, parent="#", name="model", depth=0, component="")
        counts["root_is_object"] = _is_object(root) or root.get("type") == "object"
        root_title = title.strip() if isinstance(title := root.get("title"), str) else ""
        counts["root_title_words"] = len(root_title.split())
    paths = document.get("paths") if kind == "openapi" else None
    counts["operations"] = sum(
        method in _HTTP_METHODS
        for item in (paths.values() if isinstance(paths, dict) else ())
        if isinstance(item, dict)
        for method in item
    )
    counts["components"] = len(components)
    counts["all_enums"] = counts["inline_enums"] + counts["component_enums"]
    counts["max_inline_depth"] = walker.max_depth
    shared_names = _count_names(counts, walker, components)
    shared_titles = _count_titles(counts, titles, components)
    counts["inline_title_max_repeat"] = max(walker.inline_titles.values(), default=0)
    dotted = [key for key in components if "." in key]
    counts["dotted_keys"] = len(dotted)
    counts["dotted_key_namespaces"] = len({_module(key) for key in dotted})
    counts["dotted_namespace_reference_cycles"] = _reference_cycles(walker.references) if dotted else 0
    counts["estimated_models"] = (
        counts["components"] + counts["inline_objects"] + counts["inline_enums"] + counts["inline_union_variants"]
    )
    styles = Counter(_key_style(key) for key in components).most_common(1)
    return Signals(
        counts={name: int(counts[name]) for name in sorted(SIGNAL_NAMES)},
        facts={
            "schema": _schema_label(document, kind),
            "shared_inline_name_examples": _example_text(shared_names),
            "inline_title_examples": _example_text(walker.inline_titles),
            "duplicate_title_examples": _example_text(shared_titles),
            "key_style": "".join(f"{style} ({count} of {len(components)})" for style, count in styles),
            "root_title": root_title,
        },
    )
