"""Attempt-local correspondence between actual source nodes and validated schemas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

from datamodel_code_generator._generation_contract import BindingCaptureError, SourceLocation
from datamodel_code_generator.parser.jsonschema import JsonSchemaObject
from datamodel_code_generator.parser.openapi_contract_store import BindingLedger, capture_errors

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from datamodel_code_generator._generation_contract import SourceDocumentId
    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator.enums import AllOfMergeMode

SchemaRelation: TypeAlias = Literal[
    "validated_child",
    "ref_sibling",
    "allof_merge",
    "inherited_constraint",
    "inherited_materialization",
    "ref_union_materialization",
    "deferred_shape",
    "conditional_merge",
    "combined_materialization",
    "allof_root_materialization",
    "synthetic_value",
]


@dataclass(frozen=True, slots=True)
class SchemaOrigin:
    """Borrow an actual source occurrence until final facts have been projected."""

    location: SourceLocation
    raw: dict[str, YamlValue] | bool
    relation: SchemaRelation


@dataclass(frozen=True, slots=True)
class SchemaOriginEdge:
    """Retain an actual producer's input and returned nodes until attempt freezing."""

    source: JsonSchemaObject
    target: JsonSchemaObject
    relation: SchemaRelation
    merge_mode: AllOfMergeMode | None


@dataclass(frozen=True, slots=True)
class SyntheticSchemaProjection:
    """Retain the actual item-stream producer without inventing source child pointers."""

    source: SourceLocation
    target: JsonSchemaObject
    item_reference: JsonSchemaObject
    kind: Literal["item_stream_array"] = "item_stream_array"


def _child_location(parent: SourceLocation, *tokens: str) -> SourceLocation:
    """Append raw tokens using plain RFC6901 escaping, without interpreting names."""
    suffix = "/".join(token.replace("~", "~0").replace("/", "~1") for token in tokens)
    return SourceLocation(parent.document, f"{parent.pointer}/{suffix}", "schema")


def _item_schema_children(
    *,
    items: list[JsonSchemaObject | bool] | JsonSchemaObject | bool | None,
) -> Iterator[tuple[tuple[str, ...], JsonSchemaObject | bool]]:
    """Preserve the ordinary validator's single-schema versus tuple distinction."""
    match items:
        case list():
            for ordinal, child in enumerate(items):
                yield ("items", str(ordinal)), child
        case JsonSchemaObject() | bool() as child:
            yield ("items",), child
        case None:
            return


def _schema_children(obj: JsonSchemaObject) -> Iterator[tuple[tuple[str, ...], JsonSchemaObject | bool]]:
    """Read only schema-valued data attributes, preserving raw keys and ordinals."""
    for keyword, mapping in (("properties", obj.properties), ("patternProperties", obj.patternProperties)):
        if mapping is not None:
            for key, child in mapping.items():
                yield (keyword, key), child
    for keyword, sequence in (
        ("allOf", obj.allOf),
        ("anyOf", obj.anyOf),
        ("oneOf", obj.oneOf),
        ("prefixItems", obj.prefixItems),
    ):
        if sequence is not None:
            for ordinal, child in enumerate(sequence):
                yield (keyword, str(ordinal)), child
    yield from _item_schema_children(items=obj.items)
    for keyword, optional_child in (
        ("additionalItems", obj.additionalItems),
        ("additionalProperties", obj.additionalProperties),
        ("unevaluatedProperties", obj.unevaluatedProperties),
        ("unevaluatedItems", obj.unevaluatedItems),
        ("propertyNames", obj.propertyNames),
    ):
        if optional_child is not None:
            yield (keyword,), optional_child


class ValidatedSchemaOriginIndex:
    """Own borrowed source and validated identities without revalidating schemas."""

    def __init__(self, ledger: BindingLedger) -> None:
        """Keep origin allocation confined to a capture attempt."""
        self.binding_ledger = ledger
        self._raw_locations: dict[int, list[SourceLocation]] = {}
        self._raw_anchors: dict[int, dict[str, YamlValue] | list[YamlValue]] = {}
        self._validated_anchors: dict[int, JsonSchemaObject] = {}
        self._origins: dict[int, list[SchemaOrigin]] = {}
        self._required_owners: dict[int, list[JsonSchemaObject]] = {}
        self._required_lists: dict[int, list[str]] = {}
        self._paired: set[tuple[int, SourceLocation, SchemaRelation]] = set()
        self.edges: list[SchemaOriginEdge] = []
        self.projections: list[SyntheticSchemaProjection] = []
        self._incoming: dict[int, list[SchemaOriginEdge]] = {}

    @capture_errors
    def borrow_document(self, document: SourceDocumentId, raw: dict[str, YamlValue]) -> None:
        """Index original document occurrences once, without copying raw values."""
        active: set[int] = set()
        pending: list[tuple[YamlValue, str, bool]] = [(raw, "", False)]
        while pending:
            value, pointer, leaving = pending.pop()
            if leaving:
                active.remove(id(value))
                continue
            if not isinstance(value, (dict, list)):
                continue
            node = id(value)
            location = SourceLocation(document, pointer, "schema")
            locations = self._raw_locations.setdefault(node, [])
            if location in locations:
                continue
            locations.append(location)
            self._raw_anchors[node] = value
            if node in active:
                continue
            active.add(node)
            pending.append((value, pointer, True))
            children = (
                reversed(value.items())
                if isinstance(value, dict)
                else ((len(value) - ordinal - 1, child) for ordinal, child in enumerate(reversed(value)))
            )
            pending.extend(
                (child, f"{pointer}/{str(key).replace('~', '~0').replace('/', '~1')}", False) for key, child in children
            )

    def raw_locations(self, *, raw: YamlValue) -> tuple[SourceLocation, ...]:
        """Keep repeated mapping occurrences distinct; booleans need an owning frame."""
        return tuple(self._raw_locations.get(id(raw), ()))

    def origins(self, obj: JsonSchemaObject) -> tuple[SchemaOrigin, ...]:
        """Return only recorded producer relations for this exact validated node."""
        pending = [obj]
        seen_nodes: set[int] = set()
        seen_origins: set[tuple[SourceLocation, SchemaRelation]] = set()
        result: list[SchemaOrigin] = []
        while pending:
            node = pending.pop()
            if id(node) in seen_nodes:
                continue
            seen_nodes.add(id(node))
            for origin in self._origins.get(id(node), ()):
                key = origin.location, origin.relation
                if key not in seen_origins:
                    seen_origins.add(key)
                    result.append(origin)
            pending.extend(edge.source for edge in reversed(self._incoming.get(id(node), ())))
        return tuple(result)

    @capture_errors
    def pair(
        self,
        *,
        raw: dict[str, YamlValue] | bool,
        obj: JsonSchemaObject,
        location: SourceLocation,
        relation: SchemaRelation = "validated_child",
    ) -> None:
        """Pair actual validator input/output, following only corresponding children."""
        key = id(obj), location, relation
        if key in self._paired:
            return
        self._paired.add(key)
        self._validated_anchors[id(obj)] = obj
        self._register_required(obj)
        self._origins.setdefault(id(obj), []).append(SchemaOrigin(location, raw, relation))
        if isinstance(raw, bool):
            return
        for tokens, child in _schema_children(obj):
            original: YamlValue = raw
            for token in tokens:
                match original:
                    case dict():
                        original = original.get(token)
                    case list():
                        original = original[int(token)]
                    case _:
                        msg = "A validated schema child has no matching source occurrence"
                        raise BindingCaptureError(msg)
            if isinstance(child, bool):
                continue
            if not isinstance(original, (dict, bool)):
                # x-propertyNames is the engine's documented declaration alias.
                if tokens == ("propertyNames",) and isinstance(alias := raw.get("x-propertyNames"), (dict, bool)):
                    self.pair(
                        raw=alias, obj=child, location=_child_location(location, "x-propertyNames"), relation=relation
                    )
                    continue
                msg = "A validated schema node has no matching raw schema"
                raise BindingCaptureError(msg)
            self.pair(raw=original, obj=child, location=_child_location(location, *tokens), relation=relation)

    @capture_errors
    def pair_item_projection(
        self, *, raw: dict[str, YamlValue] | bool, obj: JsonSchemaObject, location: SourceLocation
    ) -> None:
        """Bind an observed synthetic array and its item reference to the real item declaration."""
        if obj.type != "array" or not isinstance(item := obj.items, JsonSchemaObject) or item.ref is None:
            msg = "An item-stream projection did not produce the expected array reference"
            raise BindingCaptureError(msg)
        key: tuple[int, SourceLocation, SchemaRelation] = id(obj), location, "synthetic_value"
        if key in self._paired:
            return
        self._paired.add(key)
        self._validated_anchors[id(obj)] = obj
        self._origins.setdefault(id(obj), []).append(SchemaOrigin(location, raw, "synthetic_value"))
        self.pair(raw=raw, obj=item, location=location, relation="synthetic_value")
        self.projections.append(SyntheticSchemaProjection(location, obj, item))

    @capture_errors
    def pair_merged_properties(self, origin: SchemaOrigin, target: JsonSchemaObject) -> None:
        """Project raw property occurrences admitted by an observed schema merge.

        This observes property keys only; it does not evaluate constraints or
        assign source ordinals to transformed composition sequences.
        """
        key = id(target), origin.location, origin.relation
        if key in self._paired:
            return
        self._paired.add(key)
        self._validated_anchors[id(target)] = target
        self._register_required(target)
        self._origins.setdefault(id(target), []).append(origin)
        if not isinstance(origin.raw, dict) or not isinstance(properties := origin.raw.get("properties"), dict):
            return
        for name, child in (target.properties or {}).items():
            if isinstance(child, JsonSchemaObject) and isinstance(raw := properties.get(name), (dict, bool)):
                self.pair_merged_properties(
                    SchemaOrigin(_child_location(origin.location, "properties", name), raw, origin.relation), child
                )

    @capture_errors
    def pair_keyword(
        self,
        parent: JsonSchemaObject,
        keyword: Literal["if", "then", "else", "not"],
        *,
        result: JsonSchemaObject | bool | None,
    ) -> None:
        """Connect the one actual conditional-validation return to its raw keyword."""
        if not isinstance(result, JsonSchemaObject):
            return
        for origin in self.origins(parent):
            if isinstance(origin.raw, dict) and isinstance(raw := origin.raw.get(keyword), (dict, bool)):
                self.pair(raw=raw, obj=result, location=_child_location(origin.location, keyword))

    @capture_errors
    def pair_true_branch(self, parent: JsonSchemaObject, keyword: str, ordinal: int, target: JsonSchemaObject) -> None:
        """Attach a verified true occurrence without inventing child declarations."""
        for origin in self.origins(parent):
            if not isinstance(origin.raw, dict) or not isinstance(sequence := origin.raw.get(keyword), list):
                continue
            if ordinal < len(sequence) and sequence[ordinal] is True:
                self.pair(
                    raw=True,
                    obj=target,
                    location=_child_location(origin.location, keyword, str(ordinal)),
                    relation="combined_materialization",
                )

    @capture_errors
    def derive(
        self,
        source: JsonSchemaObject,
        target: JsonSchemaObject,
        relation: SchemaRelation,
        *,
        merge_mode: AllOfMergeMode | None = None,
    ) -> None:
        """Connect shape-preserving producer results using actual property identities.

        Union/tuple compaction is deliberately handled by its owning producer;
        sequence positions are not inferred by this property correspondence.
        """
        if source is target:
            return
        edge = SchemaOriginEdge(source, target, relation, merge_mode)
        self.edges.append(edge)
        self._incoming.setdefault(id(target), []).append(edge)
        self._validated_anchors[id(source)] = source
        self._validated_anchors[id(target)] = target
        self._register_required(target)
        if source.properties is None or target.properties is None:
            return
        for name, original in source.properties.items():
            replacement = target.properties.get(name)
            if isinstance(original, JsonSchemaObject) and isinstance(replacement, JsonSchemaObject):
                self.derive(original, replacement, relation, merge_mode=merge_mode)

    @capture_errors
    def derive_preserved_shape(
        self, source: JsonSchemaObject, target: JsonSchemaObject, relation: SchemaRelation
    ) -> None:
        """Follow validated children of a producer that preserves a single input shape."""
        if source is target:
            return
        self.derive(source, target, relation)
        completed = dict(_schema_children(target))
        for tokens, original in _schema_children(source):
            if tokens[0] == "properties":
                # The general property producer has already established these edges.
                continue
            replacement = completed.get(tokens)
            if isinstance(original, JsonSchemaObject) and isinstance(replacement, JsonSchemaObject):
                self.derive_preserved_shape(original, replacement, relation)

    @capture_errors
    def pair_root_materialization(
        self,
        raw: dict[str, object],
        target: JsonSchemaObject,
        producers: dict[int, tuple[JsonSchemaObject | bool, ...]],
    ) -> None:
        """Compose actual raw helper results with their final validated children."""
        pending = [(raw, target)]
        while pending:
            current, completed = pending.pop()
            sources = producers.get(id(current), ())
            for source in sources:
                if isinstance(source, JsonSchemaObject):
                    self.derive(source, completed, "allof_root_materialization")
            if len(sources) == 1 and isinstance(source := sources[0], JsonSchemaObject):
                self.derive_preserved_shape(source, completed, "allof_root_materialization")
            for tokens, child in _schema_children(completed):
                if isinstance(child, bool):
                    continue
                original: object = current
                for token in tokens:
                    match original:
                        case dict():
                            original = cast("Mapping[str, object]", original).get(token)
                        case list():
                            original = cast("list[object]", original)[int(token)]
                        case _:
                            msg = "A root materialization child has no actual raw producer"
                            raise BindingCaptureError(msg)
                if isinstance(original, dict):
                    pending.append((cast("dict[str, object]", original), child))

    @capture_errors
    def derive_combined_common(
        self, parent: JsonSchemaObject, target: JsonSchemaObject, keyword: str, *, branch: JsonSchemaObject | bool
    ) -> None:
        """Retain the unfiltered common composition copied by the actual combined producer.

        The active keyword is excluded from the common parent. Other keyword
        sequences are concatenated by that producer before any branch filtering;
        their original ordinals remain local to their respective source nodes.
        """
        for attribute, parent_nodes, specific, completed in (
            ("allOf", parent.allOf, branch.allOf if isinstance(branch, JsonSchemaObject) else [], target.allOf),
            ("anyOf", parent.anyOf, branch.anyOf if isinstance(branch, JsonSchemaObject) else [], target.anyOf),
            ("oneOf", parent.oneOf, branch.oneOf if isinstance(branch, JsonSchemaObject) else [], target.oneOf),
        ):
            common = [] if attribute == keyword else parent_nodes
            if len(common) + len(specific) != len(completed):
                msg = "A combined common composition does not match its actual materialization"
                raise BindingCaptureError(msg)
            for offset, sources in ((0, common), (len(common), specific)):
                for ordinal, source in enumerate(sources):
                    result = completed[offset + ordinal]
                    if isinstance(source, JsonSchemaObject) and isinstance(result, JsonSchemaObject):
                        self.derive(source, result, "combined_materialization")

    def _register_required(self, obj: JsonSchemaObject) -> None:
        if obj.required:
            self._required_lists[id(obj.required)] = obj.required
            owners = self._required_owners.setdefault(id(obj.required), [])
            if not any(owner is obj for owner in owners):
                owners.append(obj)

    @capture_errors
    def register_required_composition(self, names: list[str], obj: JsonSchemaObject) -> None:
        """Retain the actual allOf aggregate and its inline required-list producers."""
        self._required_lists[id(names)] = names
        owners = self._required_owners.setdefault(id(names), [])
        for inline in obj.allOf:
            if (
                isinstance(inline, JsonSchemaObject)
                and not inline.ref
                and inline.required
                and not any(owner is inline for owner in owners)
            ):
                owners.append(inline)

    def required_locations(self, names: list[str], name: str) -> tuple[SourceLocation, ...]:
        """Use the actual required-list identity, never a fabricated property key."""
        locations: dict[SourceLocation, None] = {}
        for owner in self._required_owners.get(id(names), ()):
            for origin in self.origins(owner):
                if not isinstance(origin.raw, dict) or not isinstance(required := origin.raw.get("required"), list):
                    continue
                for ordinal, raw_name in enumerate(required):
                    if raw_name == name:
                        locations[_child_location(origin.location, "required", str(ordinal))] = None
        return tuple(locations)

    def keyword_locations(self, obj: JsonSchemaObject, keyword: str) -> tuple[SourceLocation, ...]:
        """Retain only keyword occurrences actually present in borrowed declarations."""
        return tuple(
            dict.fromkeys(
                _child_location(origin.location, keyword)
                for origin in self.origins(obj)
                if isinstance(origin.raw, dict) and keyword in origin.raw
            )
        )

    def property_origins(self, obj: JsonSchemaObject, wire_name: str) -> tuple[SchemaOrigin, ...]:
        """Connect an actual field to source properties by its unchanged wire name."""
        child = (obj.properties or {}).get(wire_name)
        if isinstance(child, JsonSchemaObject) and (origins := self.origins(child)):
            return origins
        result: list[SchemaOrigin] = []
        for origin in self.origins(obj):
            if not isinstance(origin.raw, dict) or not isinstance(properties := origin.raw.get("properties"), dict):
                continue
            if isinstance(raw := properties.get(wire_name), (dict, bool)):
                result.append(
                    SchemaOrigin(_child_location(origin.location, "properties", wire_name), raw, origin.relation)
                )
        return tuple(result)

    def close(self) -> None:
        """Drop source/validated anchors at the same attempt disposal boundary."""
        self._raw_locations.clear()
        self._raw_anchors.clear()
        self._validated_anchors.clear()
        self._origins.clear()
        self._paired.clear()
        self._required_owners.clear()
        self._required_lists.clear()
        self.edges.clear()
        self.projections.clear()
        self._incoming.clear()
