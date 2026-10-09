"""GraphQL schema parser implementation.

Parses GraphQL schema files to generate Python data models including
objects, interfaces, enums, scalars, inputs, and union types.
"""

from __future__ import annotations

from functools import cached_property
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    cast,
)

from typing_extensions import Unpack

from datamodel_code_generator import (
    InputFileType,
    InvalidFileFormatError,
    LiteralType,
    snooper_to_methods,
)
from datamodel_code_generator._format_types import DatetimeClassType
from datamodel_code_generator.model.base import _find_base_classes, get_inherited_fields
from datamodel_code_generator.model.enum import SPECIALIZED_ENUM_TYPE_MATCH, Enum, EnumMemberValue
from datamodel_code_generator.parser.base import (
    DataType,
    Parser,
    _copy_data_model_field,
)
from datamodel_code_generator.reference import ModelType, Reference
from datamodel_code_generator.types import Types

try:
    import graphql
except ImportError as exc:  # pragma: no cover
    msg = "Please run `$pip install 'datamodel-code-generator[graphql]`' to generate data-model from a GraphQL schema."
    raise Exception(msg) from exc  # noqa: TRY002


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from urllib.parse import ParseResult

    from datamodel_code_generator._types import GraphQLParserConfigDict
    from datamodel_code_generator.config import GraphQLParserConfig
    from datamodel_code_generator.model import DataModel, DataModelFieldBase
    from datamodel_code_generator.parser.schema_version import JsonSchemaFeatures

# graphql-core >=3.2.7 removed TypeResolvers in favor of TypeFields.kind.
# Normalize to a single callable for resolving type kinds.
try:  # graphql-core < 3.2.7
    graphql_resolver_kind = graphql.type.introspection.TypeResolvers().kind  # ty: ignore[unresolved-attribute]
except AttributeError:
    graphql_resolver_kind = graphql.type.introspection.TypeFields.kind


def _input_field_default(field: graphql.GraphQLInputField) -> Any:
    """Return the coerced schema default of an input field, or ``Undefined`` when it has none.

    graphql-core 3.3 keeps SDL defaults as literals in ``default`` and leaves ``default_value`` unset.
    """
    if (default := getattr(field, "default", None)) is None:
        return field.default_value
    return graphql.coerce_input_literal(default.literal, field.type)  # ty: ignore[unresolved-attribute]


def build_graphql_schema(schema_str: str, *, source: str | None = None) -> graphql.GraphQLSchema:
    """Build a graphql schema from a string."""
    try:
        schema = graphql.build_schema(schema_str)
    except graphql.GraphQLSyntaxError as exc:
        raise InvalidFileFormatError(exc, InputFileType.GraphQL, source=source) from exc
    return graphql.lexicographic_sort_schema(schema)


@snooper_to_methods()
class GraphQLParser(Parser["GraphQLParserConfig", "JsonSchemaFeatures"]):
    """Parser for GraphQL schema files."""

    # raw graphql schema as `graphql-core` object
    raw_obj: graphql.GraphQLSchema

    @cached_property
    def schema_features(self) -> JsonSchemaFeatures:
        """Get schema features for GraphQL (uses default JSON Schema features)."""
        from datamodel_code_generator.enums import JsonSchemaVersion  # noqa: PLC0415
        from datamodel_code_generator.parser.schema_version import JsonSchemaFeatures  # noqa: PLC0415

        return JsonSchemaFeatures.from_version(JsonSchemaVersion.Draft202012)

    # all processed graphql objects
    # mapper from an object name (unique) to an object
    all_graphql_objects: dict[str, graphql.GraphQLNamedType]
    # a reference for each object
    # mapper from an object name to his reference
    references: dict[str, Reference]
    # mapper from graphql type to all objects with this type
    # `graphql.type.introspection.TypeKind` -- an enum with all supported types
    # `graphql.GraphQLNamedType` -- base type for each graphql object
    # see `graphql-core` for more details
    support_graphql_types: dict[graphql.type.introspection.TypeKind, list[graphql.GraphQLNamedType]]
    _typename_collisions: list[graphql.GraphQLObjectType | graphql.GraphQLInterfaceType] | None
    # graphql types order for render
    # may be as a parameter in the future
    parse_order: list[graphql.type.introspection.TypeKind] = [  # noqa: RUF012
        graphql.type.introspection.TypeKind.SCALAR,
        graphql.type.introspection.TypeKind.ENUM,
        graphql.type.introspection.TypeKind.INTERFACE,
        graphql.type.introspection.TypeKind.OBJECT,
        graphql.type.introspection.TypeKind.INPUT_OBJECT,
        graphql.type.introspection.TypeKind.UNION,
    ]

    _config_class_name: ClassVar[str] = "GraphQLParserConfig"

    def __init__(
        self,
        source: str | Path | ParseResult,
        *,
        config: GraphQLParserConfig | None = None,
        **options: Unpack[GraphQLParserConfigDict],
    ) -> None:
        """Initialize the GraphQL parser with configuration options."""
        if config is None and options.get("target_datetime_class") is None:
            options["target_datetime_class"] = DatetimeClassType.Datetime
        super().__init__(source=source, config=config, **options)

        self.references: dict[str, Reference] = {}
        self.all_graphql_objects: dict[str, graphql.GraphQLNamedType] = {}
        self.data_model_scalar_type = self.config.data_model_scalar_type
        self.data_model_union_type = self.config.data_model_union_type
        self.use_standard_collections = self.config.use_standard_collections
        self.use_union_operator = self.config.use_union_operator

    def _resolve_types(self, paths: list[str], schema: graphql.GraphQLSchema) -> None:
        root_types = {schema.query_type, schema.mutation_type, schema.subscription_type}
        for type_ in schema.type_map.values():
            if isinstance(type_, graphql.GraphQLUnionType):
                root_types.difference_update(type_.types)
        for type_name, type_ in schema.type_map.items():
            if type_name.startswith("__"):
                continue

            if type_ in root_types:
                continue

            resolved_type = graphql_resolver_kind(type_, None)

            if resolved_type in self.support_graphql_types:  # pragma: no cover
                self.all_graphql_objects[type_.name] = type_
                graphql_model_type = "enum" if resolved_type == graphql.TypeKind.ENUM else "model"
                affixed_name = self.model_resolver.get_affixed_name(type_.name, model_type=graphql_model_type)
                self.references[type_.name] = Reference(
                    path=f"{paths!s}/{resolved_type.value}/{type_.name}",
                    name=affixed_name,
                    original_name=type_.name,
                )

                self.support_graphql_types[resolved_type].append(type_)

    def _typename_field(self, name: str, excludes: set[str]) -> DataModelFieldBase:
        field_name = "typename__"
        if field_name in excludes:
            field_name = self.model_resolver.get_valid_field_name(
                field_name, excludes=excludes, model_type=self.field_name_model_type
            )
        return self.data_model_field_type(
            name=field_name,
            data_type=DataType(
                literals=[name],
                use_union_operator=self.use_union_operator,
                use_standard_collections=self.use_standard_collections,
            ),
            default=name,
            use_annotated=self.use_annotated,
            required=False,
            alias="__typename",
            serialization_alias=self.get_serialization_alias("__typename", field_name, name),
            use_one_literal_as_default=True,
            use_default_kwarg=self.use_default_kwarg,
            has_default=True,
            use_serialization_alias=self.use_serialization_alias,
            **self._data_model_field_common_kwargs(),
        )

    def _get_default(  # noqa: PLR6301
        self,
        field: graphql.GraphQLField | graphql.GraphQLInputField,
        final_data_type: DataType,  # noqa: ARG002
        *,
        required: bool,  # noqa: ARG002
    ) -> Any:
        if (
            isinstance(field, graphql.GraphQLInputField)
            and (default := _input_field_default(field)) is not graphql.pyutils.Undefined
        ):
            return default
        return None

    def _has_schema_default(  # noqa: PLR6301
        self, field: graphql.GraphQLField | graphql.GraphQLInputField
    ) -> bool:
        """Return whether a GraphQL input field defines a schema default."""
        return (
            isinstance(field, graphql.GraphQLInputField)
            and _input_field_default(field) is not graphql.pyutils.Undefined
        )

    def parse_scalar(self, scalar_graphql_object: graphql.GraphQLScalarType) -> None:
        """Parse a GraphQL scalar type and add it to results."""
        self.generation_store.register_model(
            self.data_model_scalar_type(
                reference=self.references[scalar_graphql_object.name],
                fields=[],
                custom_template_dir=self.custom_template_dir,
                extra_template_data=self.extra_template_data,
                description=scalar_graphql_object.description,
            )
        )

    def should_parse_enum_as_literal(self, obj: graphql.GraphQLEnumType) -> bool:
        """Determine if an enum should be parsed as a literal type."""
        if self.enum_field_as_literal == LiteralType.All:
            return True
        if self.enum_field_as_literal == LiteralType.One:
            return len(obj.values) == 1
        return False

    def parse_enum(self, enum_object: graphql.GraphQLEnumType) -> None:
        """Parse a GraphQL enum type and add it to results."""
        if self.ignore_enum_constraints:
            return self.parse_enum_as_str_type(enum_object)
        if self.should_parse_enum_as_literal(enum_object):
            return self.parse_enum_as_literal(enum_object)
        return self.parse_enum_as_enum_class(enum_object)

    def parse_enum_as_str_type(self, enum_object: graphql.GraphQLEnumType) -> None:
        """Parse enum as a str type alias when ignoring enum constraints."""
        data_type = self.data_type_manager.get_data_type(Types.string)
        data_model_type = self._create_data_model(
            model_type=self.data_model_root_type,
            reference=self.references[enum_object.name],
            fields=[
                self.data_model_field_type(
                    required=True,
                    data_type=data_type,
                    **self._data_model_field_common_kwargs(),
                )
            ],
            custom_base_class=self._resolve_base_class(enum_object.name),
            custom_template_dir=self.custom_template_dir,
            extra_template_data=self.extra_template_data,
            path=self.current_source_path,
            description=enum_object.description,
        )
        self.generation_store.register_model(data_model_type)

    def parse_enum_as_literal(self, enum_object: graphql.GraphQLEnumType) -> None:
        """Parse enum values as a Literal type."""
        data_type = self.data_type(literals=list(enum_object.values.keys()))
        data_model_type = self._create_data_model(
            model_type=self.data_model_root_type,
            reference=self.references[enum_object.name],
            fields=[
                self.data_model_field_type(
                    required=True,
                    data_type=data_type,
                    **self._data_model_field_common_kwargs(),
                )
            ],
            custom_base_class=self._resolve_base_class(enum_object.name),
            custom_template_dir=self.custom_template_dir,
            extra_template_data=self.extra_template_data,
            path=self.current_source_path,
            description=enum_object.description,
        )
        self.generation_store.register_model(data_model_type)

    def parse_enum_as_enum_class(self, enum_object: graphql.GraphQLEnumType) -> None:
        """Parse enum values as an Enum class."""
        enum_fields: list[DataModelFieldBase] = []
        exclude_field_names: set[str] = set()

        for value_name, value in enum_object.values.items():
            default = EnumMemberValue(value_name) if isinstance(value_name, str) else value_name

            field_name = self.model_resolver.get_valid_field_name(
                value_name, excludes=exclude_field_names, model_type=ModelType.ENUM
            )
            exclude_field_names.add(field_name)

            enum_fields.append(
                self.data_model_field_type(
                    name=field_name,
                    data_type=self.data_type_manager.get_data_type(
                        Types.string,
                    ),
                    default=default,
                    required=True,
                    strip_default_none=self.strip_default_none,
                    has_default=True,
                    use_field_description=value.description is not None,
                    original_name=None,
                    **self._data_model_field_common_kwargs(),
                )
            )

        enum_cls: type[Enum] = Enum
        if (
            self.target_python_version.has_strenum
            and self.use_specialized_enum
            and (specialized_type := SPECIALIZED_ENUM_TYPE_MATCH.get(Types.string))
        ):
            # If specialized enum is available in the target Python version, use it
            enum_cls = specialized_type

        enum: Enum = enum_cls(
            reference=self.references[enum_object.name],
            fields=enum_fields,
            path=self.current_source_path,
            description=enum_object.description,
            type_=Types.string if self.use_subclass_enum else None,
            custom_template_dir=self.custom_template_dir,
        )
        self.generation_store.register_model(enum)

    def parse_field(
        self,
        field_name: str,
        alias: str | list[str] | None,
        field: graphql.GraphQLField | graphql.GraphQLInputField,
        original_field_name: str,
        class_name: str | None = None,
    ) -> DataModelFieldBase:
        """Parse a GraphQL field and return a data model field."""
        final_data_type = DataType(
            is_optional=True,
            use_union_operator=self.use_union_operator,
            use_standard_collections=self.use_standard_collections,
        )
        data_type = final_data_type
        obj = field.type

        while graphql.is_list_type(obj) or graphql.is_non_null_type(obj):
            if graphql.is_list_type(obj):
                data_type.is_list = True

                new_data_type = DataType(
                    is_optional=True,
                    use_union_operator=self.use_union_operator,
                    use_standard_collections=self.use_standard_collections,
                )
                data_type.data_types = [new_data_type]

                data_type = new_data_type
            elif graphql.is_non_null_type(obj):  # pragma: no cover
                data_type.is_optional = False

            obj = graphql.assert_wrapping_type(obj)
            obj = obj.of_type

        obj = graphql.assert_named_type(obj)
        if obj.name in self.references:
            self.generation_store.replace_data_type_ref(data_type, self.references[obj.name])
        else:
            # Operation roots are intentionally not emitted as models.
            any_data_type = self.data_type_manager.get_data_type(Types.any)
            data_type.type = any_data_type.type
            data_type.import_ = any_data_type.import_

        has_schema_default = self._has_schema_default(field)
        required = (
            (not self.force_optional_for_required_fields)
            and (not final_data_type.is_optional)
            and not has_schema_default
        )
        nullable = False if has_schema_default and not final_data_type.is_optional else None

        default = self._get_default(field, final_data_type, required=required)
        effective_default, effective_has_default, use_default_with_required = self._effective_default_state(
            original_field_name,
            default,
            has_default=has_schema_default,
            required=required,
            class_name=class_name,
        )

        extras = {} if self.default_field_extras is None else self.default_field_extras.copy()

        if field.description is not None:  # pragma: no cover
            extras["description"] = field.description

        single_alias, validation_aliases = self._split_field_alias(alias)
        return self.data_model_field_type(
            name=field_name,
            default=effective_default,
            data_type=final_data_type,
            required=required,
            nullable=nullable,
            extras=extras,
            alias=single_alias,
            validation_aliases=validation_aliases,
            serialization_alias=self.get_serialization_alias(original_field_name, field_name, class_name),
            strip_default_none=self.strip_default_none,
            use_annotated=self.use_annotated,
            use_serialize_as_any=self.use_serialize_as_any,
            use_field_description=self.use_field_description,
            use_field_description_example=self.use_field_description_example,
            use_inline_field_description=self.use_inline_field_description,
            use_default_kwarg=self.use_default_kwarg,
            original_name=field_name,
            has_default=effective_has_default,
            use_serialization_alias=self.use_serialization_alias,
            use_default_with_required=use_default_with_required,
            **self._data_model_field_common_kwargs(),
        )

    def parse_object_like(
        self,
        obj: graphql.GraphQLInterfaceType | graphql.GraphQLObjectType | graphql.GraphQLInputObjectType,
    ) -> None:
        """Parse a GraphQL object-like type and add it to results."""
        fields = []
        exclude_field_names: set[str] = set()

        for original_field_name, field in obj.fields.items():
            field_name_, alias = self.model_resolver.get_valid_field_name_and_alias(
                original_field_name,
                excludes=exclude_field_names,
                model_type=self.field_name_model_type,
                class_name=obj.name,
            )
            exclude_field_names.add(field_name_)

            data_model_field_type = self.parse_field(
                field_name_, alias, field, original_field_name, class_name=obj.name
            )
            fields.append(data_model_field_type)

        if not self.config.graphql_no_typename:
            fields.append(self._typename_field(obj.name, exclude_field_names))

        base_classes = []
        if hasattr(obj, "interfaces"):
            base_classes = [self.references[i.name] for i in obj.interfaces]  # ty: ignore[not-iterable]

        if (
            not self.config.graphql_no_typename
            and fields[-1].name != "typename__"
            and isinstance(obj, graphql.GraphQLObjectType | graphql.GraphQLInterfaceType)
        ):
            if self._typename_collisions is None:
                self._typename_collisions = []
            self._typename_collisions.append(obj)

        data_model_type = self._create_data_model(
            reference=self.references[obj.name],
            fields=fields,
            base_classes=base_classes,
            custom_base_class=self._resolve_base_class(obj.name),
            custom_template_dir=self.custom_template_dir,
            extra_template_data=self.extra_template_data,
            path=self.current_source_path,
            description=obj.description,
            keyword_only=self.keyword_only,
            treat_dot_as_module=self.treat_dot_as_module,
            dataclass_arguments=self.dataclass_arguments,
        )
        self.generation_store.register_model(data_model_type)

    def parse_interface(self, interface_graphql_object: graphql.GraphQLInterfaceType) -> None:
        """Parse a GraphQL interface type and add it to results."""
        self.parse_object_like(interface_graphql_object)

    def parse_object(self, graphql_object: graphql.GraphQLObjectType) -> None:
        """Parse a GraphQL object type and add it to results."""
        self.parse_object_like(graphql_object)

    def parse_input_object(self, input_graphql_object: graphql.GraphQLInputObjectType) -> None:
        """Parse a GraphQL input object type and add it to results."""
        self.parse_object_like(input_graphql_object)

    def parse_union(self, union_object: graphql.GraphQLUnionType) -> None:
        """Parse a GraphQL union type and add it to results."""
        fields = [
            self.data_model_field_type(
                name=self.references[type_.name].name,
                data_type=DataType(),
                **self._data_model_field_common_kwargs(),
            )
            for type_ in union_object.types
        ]
        data_model_type = self.data_model_union_type(
            reference=self.references[union_object.name],
            fields=fields,
            custom_base_class=self._resolve_base_class(union_object.name),
            custom_template_dir=self.custom_template_dir,
            extra_template_data=self.extra_template_data,
            path=self.current_source_path,
            description=union_object.description,
        )
        self.generation_store.register_model(data_model_type)

    def parse_raw(self) -> None:
        """Parse the raw GraphQL schema and generate all data models."""
        self.all_graphql_objects = {}
        self.references: dict[str, Reference] = {}
        self._typename_collisions = None

        self.support_graphql_types = {
            graphql.type.introspection.TypeKind.SCALAR: [],
            graphql.type.introspection.TypeKind.ENUM: [],
            graphql.type.introspection.TypeKind.UNION: [],
            graphql.type.introspection.TypeKind.INTERFACE: [],
            graphql.type.introspection.TypeKind.OBJECT: [],
            graphql.type.introspection.TypeKind.INPUT_OBJECT: [],
        }

        # may be as a parameter in the future (??)
        mapper_from_graphql_type_to_parser_method = {
            graphql.type.introspection.TypeKind.SCALAR: self.parse_scalar,
            graphql.type.introspection.TypeKind.ENUM: self.parse_enum,
            graphql.type.introspection.TypeKind.INTERFACE: self.parse_interface,
            graphql.type.introspection.TypeKind.OBJECT: self.parse_object,
            graphql.type.introspection.TypeKind.INPUT_OBJECT: self.parse_input_object,
            graphql.type.introspection.TypeKind.UNION: self.parse_union,
        }

        source_paths: list[str] = []

        def iter_source_texts() -> Iterator[str]:
            for source in self.iter_source:
                display_path = self._source_path_for_diagnostics(source.path)
                if display_path != "<input>":
                    source_paths.append(display_path)
                yield source.text

        schema: graphql.GraphQLSchema = build_graphql_schema(
            "\n".join(iter_source_texts()),
            source=", ".join(source_paths) or "<input>",
        )
        self.raw_obj = schema

        self._resolve_types([], schema)

        for next_type in self.parse_order:
            for obj in self.support_graphql_types[next_type]:
                parser_ = mapper_from_graphql_type_to_parser_method[next_type]
                parser_(obj)  # ty: ignore[invalid-argument-type]

        if not (collisions := self._typename_collisions):
            return
        self._typename_collisions = None
        self._resolve_typename_collisions(schema, collisions)

    def _resolve_typename_collisions(
        self,
        schema: graphql.GraphQLSchema,
        collisions: list[graphql.GraphQLObjectType | graphql.GraphQLInterfaceType],
    ) -> None:
        """Override inherited synthetic slots without renaming unrelated models."""
        resolved: set[graphql.GraphQLObjectType | graphql.GraphQLInterfaceType] = set()

        def resolve(obj: graphql.GraphQLObjectType | graphql.GraphQLInterfaceType) -> None:
            if obj in resolved or obj.name not in self.references:
                return
            resolved.add(obj)
            for interface in obj.interfaces:
                resolve(interface)
            source = cast("DataModel", self.references[obj.name].source)
            bases = _find_base_classes(source)
            inherited_typename_names = {
                field.name for base in bases for field in base.fields if field.alias == "__typename"
            }
            fields = {field.name: field for field in source.fields}
            inherited_fields = None
            if self.data_model_type.REQUIRES_UNIQUE_FIELD_ALIASES and (
                conflicts := [
                    field
                    for field in source.fields
                    if field.name in inherited_typename_names and (field.alias is None or field.alias == field.name)
                ]
            ):
                inherited_fields = get_inherited_fields(bases)
                excludes = {
                    cast("str", field.name)
                    for field in (*source.fields, *inherited_fields.values())
                    if field.alias != "__typename"
                }
                for field in conflicts:
                    wire_name = cast("str", field.name)
                    fields.pop(wire_name)
                    inherited = next(
                        (
                            candidate
                            for candidate in inherited_fields.values()
                            if (candidate.alias or candidate.name) == wire_name
                        ),
                        None,
                    )
                    field.name = (
                        inherited.name
                        if inherited
                        else self.model_resolver.get_valid_field_name(
                            wire_name, excludes=excludes, model_type=self.field_name_model_type
                        )
                    )
                    field.alias = wire_name
                    field.serialization_alias = self.get_serialization_alias(
                        wire_name, cast("str", field.name), obj.name
                    )
                    excludes.add(cast("str", field.name))
                    fields[field.name] = field
            if not inherited_typename_names.difference(fields):
                return
            if inherited_fields is None:
                inherited_fields = get_inherited_fields(bases)
            for inherited in inherited_fields.values():
                if (
                    inherited.alias != "__typename"
                    and inherited.name in inherited_typename_names
                    and (inherited.name not in fields or fields[inherited.name].alias == "__typename")
                ):
                    field = _copy_data_model_field(inherited)
                    self.generation_store.insert_field(source, -1, field)
                    fields[field.name] = field
            inherited_typename = next(
                (
                    field
                    for field in inherited_fields.values()
                    if field.alias == "__typename" and field.name not in fields
                ),
                None,
            )
            typename_field = source.fields[-1]
            typename_field.name = (
                inherited_typename.name
                if inherited_typename
                else self.model_resolver.get_valid_field_name(
                    "typename__",
                    excludes={
                        cast("str", field.name)
                        for field in (*source.fields, *inherited_fields.values())
                        if field.alias != "__typename"
                    },
                    model_type=self.field_name_model_type,
                )
            )
            typename_field.serialization_alias = self.get_serialization_alias(
                "__typename", cast("str", typename_field.name), obj.name
            )

        visited: set[graphql.GraphQLObjectType | graphql.GraphQLInterfaceType] = set()
        pending = list(collisions)
        while pending:
            obj = pending.pop()
            if obj in visited:
                continue
            visited.add(obj)
            if isinstance(obj, graphql.GraphQLInterfaceType):
                implementations = schema.get_implementations(obj)
                pending.extend(implementations.objects)
                pending.extend(implementations.interfaces)
            resolve(obj)
