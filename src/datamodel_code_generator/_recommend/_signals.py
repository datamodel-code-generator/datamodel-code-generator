"""Schema signals for option recommendations: one walk over the loaded schema document."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, get_args

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence
    from collections.abc import Set as AbstractSet

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
    "unique_items",
    "pattern_properties",
    "unmodeled_pattern_properties",
    "multiple_of",
    "nullable_keywords",
    "boolean_exclusive_bounds",
    "coercible_scalars",
    "formats",
    "integer_range_formats",
    "decimal_formats",
]
FactName: TypeAlias = Literal[
    "schema",
    "shared_inline_name_examples",
    "inline_title_examples",
    "duplicate_title_examples",
    "key_style",
    "root_title",
    "scopes",
    "format_examples",
]
SchemaKind: TypeAlias = Literal["openapi", "jsonschema", "asyncapi"]
SIGNAL_NAMES: frozenset[str] = frozenset(get_args(SignalName))

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
_COERCIBLE_TYPES = frozenset({"integer", "number", "boolean"})
_INTEGER_RANGE_FORMATS = ("int32", "int64")
_NORMALIZE = re.compile(r"[^a-z0-9]")
_EXAMPLE_LIMIT = 4
_TAG_REFERENCE_DEPTH = 5


@dataclass(frozen=True, slots=True)
class Signals:
    """Counts and example texts measured on one schema document, and the normalized names of its schemas."""

    counts: Mapping[str, int]
    facts: Mapping[str, str]
    component_names: frozenset[str] = frozenset()


def normalize_name(name: str) -> str:
    """Return a name reduced to lowercase letters and digits, as class names compare after case conversion."""
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


def _unmodeled_pattern_properties(node: Mapping[str, Any]) -> bool:
    """Return whether a typed map cannot express a schema's patternProperties exactly, while schema validators can.

    A map of pattern-checked keys is exact for one pattern on an object that declares no properties and allows no other
    keys. Next to declared properties, with several patterns, or with other keys allowed, it rejects valid objects or
    leaves values unchecked; next to an additionalProperties schema, schema validators were measured no better.
    """
    if not isinstance(patterns := node.get("patternProperties"), dict) or not patterns:
        return False
    if isinstance(properties := node.get("properties"), dict) and properties:
        return True
    if isinstance(additional := node.get("additionalProperties"), dict) and additional:
        return False
    return len(patterns) > 1 or additional is not False


def _types(node: Mapping[str, Any]) -> frozenset[str]:
    """Return the JSON types a schema declares, from a type name or a list of them."""
    declared = node.get("type")
    return frozenset(
        name for name in (declared if isinstance(declared, list) else (declared,)) if isinstance(name, str)
    )


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


def _items(value: object) -> Iterable[tuple[str, Any]]:
    return value.items() if isinstance(value, dict) else ()


def _unwrap(node: object) -> object:
    """Return the schema of an AsyncAPI multi-format schema object, or the schema itself."""
    if isinstance(node, dict) and ("schemaFormat" in node or node.keys() == {"schema"}):
        return node.get("schema")
    return node


def _uses_definitions(kind: SchemaKind) -> bool:
    """Return whether named schemas live in definitions/$defs, as in JSON Schema and Swagger 2.0 read as JSON Schema."""
    return kind == "jsonschema"


def _components(document: Mapping[str, Any], kind: SchemaKind) -> dict[str, Any]:
    """Return the named schemas of a document: $defs and definitions, or components/schemas."""
    if _uses_definitions(kind):
        return {key: value for keyword in ("$defs", "definitions") for key, value in _items(document.get(keyword))}
    components = document.get("components")
    return {
        key: _unwrap(value)
        for key, value in _items(components.get("schemas") if isinstance(components, dict) else None)
    }


def _component_refs(kind: SchemaKind, components: Iterable[str]) -> dict[str, str]:
    prefixes = ("#/$defs/", "#/definitions/") if _uses_definitions(kind) else ("#/components/schemas/",)
    return {f"{prefix}{key}": key for key in components for prefix in prefixes}


def _media_schemas(key: str, container: object) -> Iterator[tuple[str, object]]:
    """Yield the media type schemas of a request body or response."""
    content = container.get("content") if isinstance(container, dict) else None
    for media_type, media in _items(content):
        yield f"{key} {media_type}", media.get("schema") if isinstance(media, dict) else None


def _path_selected(path: str, include_paths: Sequence[str]) -> bool:
    slashed = path if path.startswith("/") else f"/{path}"
    return not include_paths or any(
        fnmatch(slashed, pattern if pattern.startswith("/") else f"/{pattern}") for pattern in include_paths
    )


def _operation_schemas(
    document: Mapping[str, Any], scopes: AbstractSet[str], include_paths: Sequence[str]
) -> Iterator[tuple[str, object]]:
    """Yield the request, response, parameter, and request body schemas the OpenAPI scopes generate."""
    api = "api" in scopes
    for section in ("paths", "webhooks"):
        items = document.get(section) if api or section in scopes else None
        for path, item in _items(items):
            if section == "webhooks" or _path_selected(path, include_paths):
                for method, operation in _items(item):
                    yield from _operation_parts(f"{path} {method}", operation)
    components = document.get("components")
    bodies = (
        components.get("requestBodies") if isinstance(components, dict) and (api or "requestbodies" in scopes) else None
    )
    for name, body in _items(bodies):
        yield from _media_schemas(name, body)


def _operation_parts(key: str, operation: object) -> Iterator[tuple[str, object]]:
    """Yield the request body, response, and parameter schemas of one operation.

    Generation parses every parameter's schema of the operations it walks, so inline enums and objects of parameters
    are models whether or not the parameters scope also emits the operation's parameters model.
    """
    if not isinstance(operation, dict) or key.rpartition(" ")[2] not in _HTTP_METHODS:
        return
    yield from _media_schemas(f"{key} request", operation.get("requestBody"))
    for status, response in _items(operation.get("responses")):
        yield from _media_schemas(f"{key} {status}", response)
    fields = {
        name: parameter.get("schema")
        for parameter in operation.get("parameters") or ()
        if isinstance(parameter, dict) and isinstance(name := parameter.get("name"), str)
    }
    yield f"{key} parameters", {"type": "object", "properties": fields} if fields else None


def _message_schemas(document: Mapping[str, Any]) -> Iterator[tuple[str, object]]:
    """Yield the payload and header schemas of AsyncAPI messages in components and channels."""
    components = document.get("components")
    components = components if isinstance(components, dict) else {}
    messages = list(_items(components.get("messages")))
    for name, channel in [*_items(document.get("channels")), *_items(components.get("channels"))]:
        messages.extend(
            (f"{name} {operation}", _operation_message(channel, operation)) for operation in ("publish", "subscribe")
        )
        messages.extend((f"{name} {key}", message) for key, message in _items(_get(channel, "messages")))
    for name, message in messages:
        variants = _get(message, "oneOf")
        for index, variant in enumerate(variants if isinstance(variants, list) else [message]):
            for part in ("payload", "headers"):
                yield f"{name} {index} {part}", _unwrap(_get(variant, part))


def _get(node: object, key: str) -> object:
    return node.get(key) if isinstance(node, dict) else None


def _operation_message(channel: object, operation: str) -> object:
    return _get(_get(channel, operation), "message")


def _count_operations(document: Mapping[str, Any], kind: SchemaKind) -> int:
    """Count OpenAPI path operations, or AsyncAPI channel and top-level operations."""
    if kind == "openapi":
        return sum(method in _HTTP_METHODS for _, item in _items(document.get("paths")) for method, _ in _items(item))
    return len(dict(_items(document.get("operations")))) + sum(
        isinstance(_get(channel, operation), dict)
        for _, channel in _items(document.get("channels"))
        for operation in ("publish", "subscribe")
    )


def _schema_label(document: Mapping[str, Any], kind: SchemaKind) -> str:
    if "swagger" in document:
        return f"Swagger {document['swagger']}"
    if kind == "jsonschema":
        schema_uri = document.get("$schema")
        return f"JSON Schema ({schema_uri})" if isinstance(schema_uri, str) else "JSON Schema"
    return f"{'OpenAPI' if kind == 'openapi' else 'AsyncAPI'} {document.get(kind)}"


@dataclass(frozen=True, slots=True)
class _Dialect:
    """How generation reads the schemas of one document: the formats it maps, and the version-dependent keywords.

    ``ref_siblings`` is whether keywords beside ``$ref`` apply, ``nullable_keyword`` whether ``nullable`` is the
    document's keyword for null (OpenAPI 3.0 and AsyncAPI 2).
    """

    data_formats: Mapping[str, Mapping[str, Any]]
    ref_siblings: bool
    nullable_keyword: bool


def _dialect(document: Mapping[str, Any], kind: SchemaKind) -> _Dialect:
    """Return how generation reads a document, from its declared or detected version, as the parsers decide it."""
    from datamodel_code_generator.enums import JsonSchemaVersion  # noqa: PLC0415
    from datamodel_code_generator.parser.schema_version import (  # noqa: PLC0415
        OpenAPISchemaFeatures,
        detect_asyncapi_version,
        detect_jsonschema_version,
        detect_openapi_version,
        get_data_formats,
    )

    data_formats = get_data_formats(is_openapi=True)
    if kind == "jsonschema":
        version = detect_jsonschema_version(dict(document))
        ref_siblings = version not in {JsonSchemaVersion.Draft4, JsonSchemaVersion.Draft6, JsonSchemaVersion.Draft7}
        return _Dialect(data_formats, ref_siblings=ref_siblings, nullable_keyword=False)
    features = (
        OpenAPISchemaFeatures.from_openapi_version(detect_openapi_version(dict(document)))
        if kind == "openapi"
        else OpenAPISchemaFeatures.from_asyncapi_version(detect_asyncapi_version(dict(document)))
    )
    return _Dialect(data_formats, features.ref_sibling_keywords, features.nullable_keyword)


class _Walker:
    """Collect the counts of one document in a single pass over its named schemas."""

    def __init__(self, document: Mapping[str, Any], refs: Mapping[str, str], dialect: _Dialect) -> None:
        self.document = document
        self.refs = refs
        self.dialect = dialect
        self.counts: Counter[str] = Counter()
        self.formats: Counter[str] = Counter()
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

    def count_validation_keywords(self, node: Mapping[str, Any]) -> None:
        """Count the uniqueItems, patternProperties and multipleOf keywords of a schema."""
        counts = self.counts
        counts["unique_items"] += node.get("uniqueItems") is True
        counts["pattern_properties"] += isinstance(patterns := node.get("patternProperties"), dict) and bool(patterns)
        counts["unmodeled_pattern_properties"] += _unmodeled_pattern_properties(node)
        divisor = node.get("multipleOf")
        counts["multiple_of"] += isinstance(divisor, int | float) and not isinstance(divisor, bool)

    def count_keywords(self, node: Mapping[str, Any]) -> None:
        """Count a one-value enum without const, the validation keywords of a schema, and a format generation maps.

        A format counts when generation maps it to a type for one of the types the schema declares.
        """
        counts = self.counts
        values = node.get("enum")
        counts["single_value_enums"] += isinstance(values, list) and len(values) == 1 and "const" not in node
        self.count_validation_keywords(node)
        counts["nullable_keywords"] += self.dialect.nullable_keyword and node.get("nullable") is True
        counts["boolean_exclusive_bounds"] += (
            node.get("exclusiveMinimum") is True or node.get("exclusiveMaximum") is True
        )
        types = _types(node)
        counts["coercible_scalars"] += not types.isdisjoint(_COERCIBLE_TYPES)
        if isinstance(format_ := node.get("format"), str) and any(
            format_ in self.dialect.data_formats.get(name, ()) for name in types
        ):
            self.formats[format_] += 1

    def count_reference(self, node: Mapping[str, Any], component: str) -> bool:
        """Record a reference and count the validation keywords beside it where they apply; return whether it is one."""
        if not self.record_reference(node, component):
            return False
        if self.dialect.ref_siblings:
            self.count_validation_keywords(node)
        return True

    def record_reference(self, node: Mapping[str, Any], component: str) -> bool:
        """Record a reference to a named schema; return whether the node is a reference."""
        if not isinstance(ref := node.get("$ref"), str):
            return False
        if (target := self.refs.get(ref)) is not None:
            self.references[component].add(target)
        return True

    def walk_named(self, key: str, node: object) -> None:
        """Walk one named schema; the schema itself is the first parent model.

        Subschemas reached without a property (array items, union branches, allOf parts) are named after the
        schema itself, so they get an empty name and do not count as names shared with other parents.
        """
        if isinstance(node, dict) and not self.count_reference(node, key):
            self.count_keywords(node)
            self.walk_children(node, parent=key, name="", depth=0, component=key)

    def record_name(self, name: str, parent: str, parents: defaultdict[str, set[str]]) -> None:
        """Record the parent of an inline model named after a property."""
        if name:
            self.name_parents[name].add(parent)
            parents[name].add(parent)

    def walk(self, node: object, *, parent: str, name: str, depth: int, component: str) -> None:
        """Walk one inline schema reached under a parent model through a property or keyword."""
        if not isinstance(node, dict) or _is_null(node) or self.count_reference(node, component):
            return
        self.count_keywords(node)
        if _is_enum(node):
            self.counts["inline_enums"] += 1
            self.record_name(name, parent, self.enum_name_parents)
            self.enum_shapes[self.shape(node)] += 1
        if _is_object(node):
            self.counts["inline_objects"] += 1
            self.record_name(name, parent, self.object_name_parents)
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
                self.walk(
                    member, parent=parent, name=normalize_name(str(member_name)), depth=depth, component=component
                )
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
    keys = {normalize_name(key) for key in components}
    groups = Counter(normalize_name(title) for title in titles.values())
    shared = {title: count for title, count in groups.items() if count > 1}
    counts["titled_components"] = len(titles)
    counts["component_title_duplicate_groups"] = len(shared)
    counts["component_titles_in_duplicate_groups"] = sum(shared.values())
    counts["component_titles_equal_own_key"] = sum(
        normalize_name(title) == normalize_name(key) for key, title in titles.items()
    )
    counts["component_titles_equal_other_key"] = sum(
        (normalized := normalize_name(title)) != normalize_name(key) and normalized in keys
        for key, title in titles.items()
    )
    return Counter({
        title: shared[normalize_name(title)] for title in titles.values() if normalize_name(title) in shared
    })


def _count_names(counts: Counter[str], walker: _Walker, components: Iterable[str]) -> Counter[str]:
    """Count inline model names shared by parents or taken by components; return the shared names."""
    keys = {normalize_name(key) for key in components}
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


def _walk_components(walker: _Walker, components: Mapping[str, Any], roots: Iterable[str] | None) -> dict[str, Any]:
    """Walk the named schemas generation emits: all of them, or those the roots reference when roots are given."""
    pending = (
        sorted(components)
        if roots is None
        else sorted({target for root in roots for target in walker.references[root]})
    )
    walked: dict[str, Any] = {}
    while pending:
        if (key := pending.pop()) not in walked:
            walked[key] = components[key]
            walker.walk_named(key, components[key])
            pending.extend(sorted(walker.references[key] - walked.keys()))
    return dict(sorted(walked.items()))


def measure_signals(
    document: Mapping[str, Any],
    kind: SchemaKind,
    *,
    openapi_scopes: AbstractSet[str] = frozenset({"schemas"}),
    include_paths: Sequence[str] = (),
) -> Signals:
    """Measure the naming-relevant signals of one OpenAPI, AsyncAPI, or JSON Schema document.

    For OpenAPI, the scopes select what is measured as generation would emit it: component schemas with the schemas
    scope (otherwise only those the operations reference), and request, response, and parameter schemas with the
    path scopes.
    """
    all_components = _components(document, kind)
    walker = _Walker(document, _component_refs(kind, all_components), _dialect(document, kind))
    counts = walker.counts
    extra: list[tuple[str, object]] = []
    if kind == "openapi":
        extra = list(_operation_schemas(document, openapi_scopes, include_paths))
    elif kind == "asyncapi":
        extra = list(_message_schemas(document))
    for key, node in extra:
        walker.walk_named(key, node)
    whole = kind != "openapi" or bool({"schemas", "api"} & openapi_scopes)
    components = _walk_components(walker, all_components, None if whole else [key for key, _ in extra])
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
        walker.count_keywords(root)
        walker.walk_children(root, parent="#", name="", depth=0, component="")
        counts["root_is_object"] = _is_object(root) or root.get("type") == "object"
        root_title = title.strip() if isinstance(title := root.get("title"), str) else ""
        counts["root_title_words"] = len(root_title.split())
    counts["operations"] = _count_operations(document, kind)
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
    counts["formats"] = walker.formats.total()
    counts["integer_range_formats"] = sum(walker.formats[name] for name in _INTEGER_RANGE_FORMATS)
    counts["decimal_formats"] = walker.formats["decimal"]
    counts["estimated_models"] = (
        counts["components"] + counts["inline_objects"] + counts["inline_enums"] + counts["inline_union_variants"]
    )
    styles = Counter(_key_style(key) for key in components).most_common(1)
    return Signals(
        counts={name: int(counts[name]) for name in sorted(SIGNAL_NAMES)},
        facts={
            "schema": _schema_label(document, kind),
            "scopes": ", ".join(sorted(openapi_scopes)) if kind == "openapi" else "",
            "shared_inline_name_examples": _example_text(shared_names),
            "inline_title_examples": _example_text(walker.inline_titles),
            "duplicate_title_examples": _example_text(shared_titles),
            "key_style": "".join(f"{style} ({count} of {len(components)})" for style, count in styles),
            "root_title": root_title,
            "format_examples": _example_text(walker.formats),
        },
        component_names=frozenset(normalize_name(key) for key in all_components),
    )
