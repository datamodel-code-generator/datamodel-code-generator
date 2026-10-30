"""The union tags of the JSON Schema parser (L1): the value that tags each branch of a union, and the branch names.

Each function takes the parser through ``UnionTagsHost``, which ``JsonSchemaParser`` satisfies structurally. The
methods tests call stay on the parser as delegators to these functions (``parser/jsonschema.py``), and these functions
call them through the parser.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any, Union

from typing_extensions import Protocol, final

from datamodel_code_generator._records import record
from datamodel_code_generator.model._decisions.tag_value import (
    FieldTag,
    distinct_tags,
    field_tag,
    tagged_union_discriminator,
    union_tag_values,
)
from datamodel_code_generator.model.base import sanitize_module_name
from datamodel_code_generator.parser._schema_object import Discriminator, JsonSchemaObject

if TYPE_CHECKING:
    from collections.abc import Container, Iterable, Iterator, Mapping, Sequence

    from datamodel_code_generator.parser._schema_graph import _TargetNode
    from datamodel_code_generator.parser._schema_object import JsonSchemaLiteral
    from datamodel_code_generator.reference import ModelResolver, SchemaLocation
    from datamodel_code_generator.types import DataType

__all__ = [
    "TaggedUnionValue",
    "UnionTagsHost",
    "all_of_adds_properties",
    "get_referenced_single_literal_value",
    "get_single_literal_value",
    "get_tagged_union_field_values",
    "get_union_variant_class_name",
    "get_union_variant_literal_values",
    "get_union_variant_tag_keys",
    "infer_union_variant_literal_names",
    "infer_union_variant_names",
    "is_null_only_schema",
    "is_single_literal_schema",
    "iter_union_variant_literal_field_names",
    "parse_combined_schema_items",
    "set_tagged_union_discriminator",
]

TaggedUnionValue = Union[int, str]  # noqa: UP007
_MIN_UNION_VARIANT_LITERAL_VALUES = 2


class UnionTagsHost(Protocol):
    """The options, run state and hooks of the parser that the union tags read and write."""

    @property
    def model_resolver(self) -> ModelResolver:
        """The resolver of references and class names."""

    @property
    def use_tag_as_union_variant_name(self) -> bool:
        """Whether union branches are named after their tags (``--use-tag-as-union-variant-name``)."""

    @property
    def _tag_key_variants(self) -> dict[SchemaLocation | None, tuple[str, str]]:
        """The key and the name of each branch named after its key, by the location of the branch schema."""

    def _ref_path(self, ref: str) -> str:
        """Return the reference key a reference names."""

    def _resolve_external_ref_mapping(self, ref: str) -> tuple[str, str] | None:
        """Return the package and fragment ``--external-ref-mapping`` maps a reference to, if any."""

    def _key_node(self, resolved_ref: str) -> _TargetNode:
        """Return the schema graph node at a reference key, or the key when the graph does not locate its value."""

    def _load_ref_schema_object(self, ref: str) -> JsonSchemaObject:
        """Load the schema object a reference names."""

    def _is_local_ref(self, ref: str) -> bool:
        """Return whether a reference is a fragment of the document parsed now."""

    def _get_single_literal_value(
        self,
        obj: JsonSchemaObject,
        seen_refs: set[_TargetNode] | None = None,
    ) -> JsonSchemaLiteral | None:
        """Return the one value a property schema admits as a tag (``get_single_literal_value``)."""

    def _get_union_variant_literal_values(
        self,
        combined_schemas: Sequence[JsonSchemaObject],
        field_name: str,
        tag_keys: Mapping[int, str] | None = None,
    ) -> dict[int, JsonSchemaLiteral] | None:
        """Return each property branch's distinct literal for a field (``get_union_variant_literal_values``)."""

    def _iter_union_variant_literal_field_names(
        self,
        obj: JsonSchemaObject,
        combined_schemas: Sequence[JsonSchemaObject],
    ) -> Iterator[str]:
        """Yield the fields that may tag the branches (``iter_union_variant_literal_field_names``)."""

    def parse_item(
        self,
        name: str,
        item: JsonSchemaObject,
        path: list[str],
        singular_name: bool = False,  # noqa: FBT001, FBT002
        parent: JsonSchemaObject | None = None,
    ) -> DataType:
        """Parse a single JSON Schema item into a data type."""


def _get_discriminator_property_name(obj: JsonSchemaObject) -> str | None:
    """Return the discriminator property name from either JSON Schema or OpenAPI shape."""
    discriminator = obj.discriminator
    if isinstance(discriminator, Discriminator):
        return discriminator.propertyName
    if isinstance(discriminator, str):
        return discriminator
    return None


@final
@record
class _SchemaTagFacts:
    """The facts of a property schema that ``field_tag`` reads: its ``const``, its ``enum`` and its ``$ref``."""

    parser: UnionTagsHost
    obj: JsonSchemaObject
    seen_refs: set[_TargetNode] | None

    def has_const(self) -> bool:
        """Return whether the schema has a ``const``, null included."""
        return "const" in self.obj.extras

    def const(self) -> Any:
        """Return the schema's ``const``."""
        return self.obj.extras["const"]

    def literals(self) -> tuple[()]:  # noqa: PLR6301
        """Return no ``Literal`` values: a schema has no ``Literal`` type."""
        return ()

    def enum_member(self) -> tuple[int, Any]:
        """Return how many values the schema's ``enum`` holds, and the first one."""
        return len(self.obj.enum), self.obj.enum[0] if self.obj.enum else None

    def referenced_tag(self) -> FieldTag | None:
        """Return the tag of the schema the ``$ref`` names."""
        value = get_referenced_single_literal_value(self.parser, self.obj, self.seen_refs)
        return None if value is None else FieldTag(value, "referenced-schema")


def _get_union_variant_name(name: str, literal: JsonSchemaLiteral) -> str | None:
    module_name, separator, class_name = name.rpartition(".")
    if isinstance(literal, str):
        literal_text = literal
    elif isinstance(literal, bool):
        literal_text = f"bool_{str(literal).lower()}"
    else:
        literal_text = f"int_{literal}"
    literal_name = sanitize_module_name(literal_text, treat_dot_as_module=False)
    if not literal_name:
        return None
    variant_name = f"{class_name or name}_{literal_name}"
    return f"{module_name}{separator}{variant_name}" if module_name else variant_name


def get_single_literal_value(
    parser: UnionTagsHost,
    obj: JsonSchemaObject,
    seen_refs: set[_TargetNode] | None = None,
) -> JsonSchemaLiteral | None:
    """Return the one value a property schema admits as a tag (``field_tag`` at ``schema``)."""
    tag = field_tag("schema", _SchemaTagFacts(parser, obj, seen_refs))
    return None if tag is None else tag.value


def get_referenced_single_literal_value(
    parser: UnionTagsHost, obj: JsonSchemaObject, seen_refs: set[_TargetNode] | None
) -> JsonSchemaLiteral | None:
    """Return the one value the schema a ``$ref`` names admits as a tag, reading each schema node once."""
    if not obj.ref:
        return None
    seen_refs = seen_refs or set()
    resolved_ref = parser._ref_path(obj.ref)  # noqa: SLF001
    if parser._resolve_external_ref_mapping(obj.ref) or (node := parser._key_node(resolved_ref)) in seen_refs:  # noqa: SLF001
        return None
    seen_refs.add(node)
    return parser._get_single_literal_value(parser._load_ref_schema_object(obj.ref), seen_refs)  # noqa: SLF001


def get_union_variant_literal_values(
    parser: UnionTagsHost,
    combined_schemas: Sequence[JsonSchemaObject],
    field_name: str,
    tag_keys: Mapping[int, str] | None = None,
) -> dict[int, JsonSchemaLiteral] | None:
    """Return each property branch's distinct literal for a field; key branches count only for their own key.

    Without `tag_keys`, at least two branches must carry a literal; with them, one is enough, and branches that are
    unions themselves are skipped.
    """
    values: dict[int, JsonSchemaLiteral] = {}
    for index, item in enumerate(combined_schemas):
        if not item.properties or (
            tag_keys is not None and (item.oneOf or item.anyOf or tag_keys.get(index, field_name) != field_name)
        ):
            continue
        field = item.properties.get(field_name)
        if not isinstance(field, JsonSchemaObject):
            return None
        value = parser._get_single_literal_value(field)  # noqa: SLF001
        if value is None:
            return None
        values[index] = value

    if len(values) < (_MIN_UNION_VARIANT_LITERAL_VALUES if tag_keys is None else 1):
        return None
    return values if distinct_tags(values.values(), "type-and-value") else None


def iter_union_variant_literal_field_names(
    parser: UnionTagsHost,  # noqa: ARG001
    obj: JsonSchemaObject,
    combined_schemas: Sequence[JsonSchemaObject],
) -> Iterator[str]:
    seen: set[str] = set()
    if discriminator_property_name := _get_discriminator_property_name(obj):
        seen.add(discriminator_property_name)
        yield discriminator_property_name

    for item in combined_schemas:
        if not item.properties:
            continue
        for field_name in item.properties:
            if field_name in seen:
                continue
            seen.add(field_name)
            yield field_name


def is_null_only_schema(
    parser: UnionTagsHost, obj: JsonSchemaObject, seen_refs: set[_TargetNode] | None = None
) -> bool:
    """Return whether a schema, or the schema its `$ref` chain reaches, admits only null by type, const, or enum."""
    if obj.type in ("null", ["null"]) or obj.enum == [None] or ("const" in obj.extras and obj.extras["const"] is None):
        return True
    if not obj.ref:
        return False
    seen_refs = seen_refs or set()
    resolved_ref = parser._ref_path(obj.ref)  # noqa: SLF001
    if parser._resolve_external_ref_mapping(obj.ref) or (node := parser._key_node(resolved_ref)) in seen_refs:  # noqa: SLF001
        return False
    seen_refs.add(node)
    return is_null_only_schema(parser, parser._load_ref_schema_object(obj.ref), seen_refs)  # noqa: SLF001


def is_single_literal_schema(parser: UnionTagsHost, obj: JsonSchemaObject | bool | None) -> bool:  # noqa: FBT001
    """Return whether a property schema admits a single literal value."""
    return isinstance(obj, JsonSchemaObject) and parser._get_single_literal_value(obj) is not None  # noqa: SLF001


def all_of_adds_properties(parser: UnionTagsHost, items: Iterable[JsonSchemaObject | bool]) -> bool:
    """Return whether allOf members add properties inline, through nested allOf/oneOf/anyOf, or through a `$ref`."""
    return any(
        isinstance(item, JsonSchemaObject)
        and bool(
            item.ref
            or item.properties
            or any(all_of_adds_properties(parser, members) for members in (item.allOf, item.oneOf, item.anyOf))
        )
        for item in items
    )


def get_union_variant_class_name(parser: UnionTagsHost, variant_name: str) -> str:
    """Return the class name a variant name becomes before the model resolver makes it unique."""
    return parser.model_resolver.get_class_name(variant_name, unique=False).name


def get_union_variant_tag_keys(
    parser: UnionTagsHost,
    obj: JsonSchemaObject,
    combined_schemas: Sequence[JsonSchemaObject],
    literal_fields: Container[str],
) -> dict[int, str]:
    """Return the key of each key branch by branch index.

    A key branch has no `oneOf`/`anyOf` and no `allOf` of its own that adds properties (the union schema's `allOf`
    is copied into every branch first), one own property (one the union schema does not declare) that is required
    and not a single literal, and no single literal for a field the union declares without one. A union of one
    branch beside null has no key branch.
    """
    tag_keys: dict[int, str] = {}
    non_null_branches = (item for item in combined_schemas if not is_null_only_schema(parser, item))
    if (
        len(combined_schemas) > 1
        and next(non_null_branches, None) is not None
        and next(non_null_branches, None) is None
    ):
        return tag_keys
    shared = obj.properties or {}
    open_fields = [field_name for field_name in shared if field_name not in literal_fields]
    for index, item in enumerate(combined_schemas):
        if (
            not item.properties
            or item.oneOf
            or item.anyOf
            or all_of_adds_properties(parser, item.allOf[len(obj.allOf) :])
        ):
            continue
        own_properties = [(key, value) for key, value in item.properties.items() if key not in shared]
        if len(own_properties) != 1:
            continue
        key, value = own_properties[0]
        if (
            key in item.required
            and not is_single_literal_schema(parser, value)
            and not any(is_single_literal_schema(parser, item.properties.get(field)) for field in open_fields)
        ):
            tag_keys[index] = key
    return tag_keys


def infer_union_variant_literal_names(  # noqa: PLR0913, PLR0917
    parser: UnionTagsHost,
    name: str,
    obj: JsonSchemaObject,
    combined_schemas: Sequence[JsonSchemaObject],
    tag_keys: Mapping[int, str] | None = None,
    skipped_fields: Container[str] = (),
) -> list[str | None] | None:
    """Name the branches after the first field not in `skipped_fields` whose literals give distinct names."""
    for field_name in parser._iter_union_variant_literal_field_names(obj, combined_schemas):  # noqa: SLF001
        if field_name in skipped_fields:
            continue
        values = parser._get_union_variant_literal_values(combined_schemas, field_name, tag_keys)  # noqa: SLF001
        if values is None:
            continue
        variant_names: list[str | None] = [None] * len(combined_schemas)
        for index, literal in values.items():
            variant_names[index] = _get_union_variant_name(name, literal)
        generated_names = [variant_name for variant_name in variant_names if variant_name]
        if parser.use_tag_as_union_variant_name:
            generated_names = [get_union_variant_class_name(parser, variant_name) for variant_name in generated_names]
        if len(set(generated_names)) != len(generated_names):
            continue
        return variant_names
    return None


def infer_union_variant_names(
    parser: UnionTagsHost,
    name: str,
    obj: JsonSchemaObject,
    combined_schemas: Sequence[JsonSchemaObject],
    variant_keys: dict[int, str],
) -> list[str | None] | None:
    """Name the branches from literal fields, or with tag names only for a union literal fields leave unnamed.

    Tag names come from a literal that one branch alone may carry, skipping fields the union schema fixes to a
    literal, then from the keys of key branches; a key name whose class name equals that of a literal name or
    another key name is dropped. With tags, names must also differ as class names. The key of each branch named
    after its key goes into `variant_keys` by branch index.
    """
    variant_names = infer_union_variant_literal_names(parser, name, obj, combined_schemas)
    if not parser.use_tag_as_union_variant_name or (variant_names and any(variant_names)):
        return variant_names
    literal_fields = {
        field_name for field_name, field in (obj.properties or {}).items() if is_single_literal_schema(parser, field)
    }
    tag_keys = get_union_variant_tag_keys(parser, obj, combined_schemas, literal_fields)
    tag_names = infer_union_variant_literal_names(parser, name, obj, combined_schemas, tag_keys, literal_fields)
    tag_names = tag_names or [None] * len(combined_schemas)
    key_names = {index: key_name for index, key in tag_keys.items() if (key_name := _get_union_variant_name(name, key))}
    key_class_names = {index: get_union_variant_class_name(parser, key_name) for index, key_name in key_names.items()}
    literal_class_names = {get_union_variant_class_name(parser, tag_name) for tag_name in tag_names if tag_name}
    counts = Counter(key_class_names.values())
    for index, class_name in key_class_names.items():
        if counts[class_name] == 1 and class_name not in literal_class_names:
            tag_names[index] = key_names[index]
            variant_keys[index] = tag_keys[index]
    return tag_names


def get_tagged_union_field_values(
    parser: UnionTagsHost,
    obj: JsonSchemaObject,
    combined_schemas: Sequence[JsonSchemaObject],
) -> tuple[str, dict[int, TaggedUnionValue]] | None:
    """Return the shared required literal field and the msgspec tags of the branches (``union_tag_values``)."""
    discriminator_schemas = [
        parser._load_ref_schema_object(item.ref) if item.ref and parser._is_local_ref(item.ref) else item  # noqa: SLF001
        for item in combined_schemas
    ]
    for field_name in parser._iter_union_variant_literal_field_names(obj, discriminator_schemas):  # noqa: SLF001
        if (
            literal_values := parser._get_union_variant_literal_values(discriminator_schemas, field_name)  # noqa: SLF001
        ) is not None and (
            tag_values := union_tag_values(
                literal_values,
                branch_count=len(discriminator_schemas),
                required={index for index in literal_values if field_name in discriminator_schemas[index].required},
            )
        ) is not None:
            return field_name, tag_values
    return None


def set_tagged_union_discriminator(
    parser: UnionTagsHost,
    obj: JsonSchemaObject,
    combined_schemas: Sequence[JsonSchemaObject],
) -> None:
    """Set a discriminator extra when the output requires a tagged union."""
    if not (tag_data := get_tagged_union_field_values(parser, obj, combined_schemas)):
        return
    obj.extras["discriminator"] = tagged_union_discriminator(
        obj.extras.get("discriminator"), literal_property=tag_data[0]
    )


def parse_combined_schema_items(  # noqa: PLR0913, PLR0917
    parser: UnionTagsHost,
    name: str,
    obj: JsonSchemaObject,
    path: list[str],
    combined_schemas: Sequence[JsonSchemaObject],
    variant_names: Sequence[str | None] | None,
    variant_keys: Mapping[int, str],
) -> list[DataType]:
    """Parse the branches of a union, recording the key and name of each branch named after its key by its node."""
    if variant_names:
        for index, key in variant_keys.items():
            parser._tag_key_variants[combined_schemas[index].schema_location] = (key, variant_names[index] or name)  # noqa: SLF001
    return [
        parser.parse_item(
            (variant_names[index] or name) if variant_names else name,
            item,
            [*path, str(index)],
            singular_name=False,
            parent=obj,
        )
        for index, item in enumerate(combined_schemas)
    ]
