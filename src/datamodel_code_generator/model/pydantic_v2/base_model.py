"""Pydantic v2 BaseModel implementation.

Provides Constraints, DataModelField, and BaseModel for Pydantic v2
with support for Field() constraints and ConfigDict.
"""

from __future__ import annotations

import json
import re
from abc import abstractmethod
from collections import defaultdict
from dataclasses import replace
from itertools import chain
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Optional, cast
from warnings import warn

from pydantic import Field, ValidationError, field_validator, model_validator
from typing_extensions import override

from datamodel_code_generator import _constraint_applicability as _applicability
from datamodel_code_generator._alias_generators import generate_alias as _generate_alias
from datamodel_code_generator._container_kinds import CONTAINERS, UNHASHABLE_CONTAINERS
from datamodel_code_generator._document_reader import as_mapping
from datamodel_code_generator._free_names import first_free
from datamodel_code_generator._graph import reach, reaching
from datamodel_code_generator._graph_links import clear_parents, drop_tree
from datamodel_code_generator._message_text import for_count
from datamodel_code_generator._model_keys import FIELDLESS_SITE_KEY as FIELDLESS_SITE_KEY
from datamodel_code_generator._model_keys import (
    is_fieldless_site,
)
from datamodel_code_generator._python_syntax import call_text, holds_lone_surrogate
from datamodel_code_generator._reference_graph import EdgeKind
from datamodel_code_generator._regex_syntax import (
    pattern_engine,
    selects_python_engine,
)
from datamodel_code_generator._requirements import CONFIG_KEY_REQUIREMENTS, REQUIREMENTS, form_path
from datamodel_code_generator._shared_types import Error
from datamodel_code_generator.enums import AliasGenerator, DataModelType
from datamodel_code_generator.imports import (
    IMPORT_ANNOTATED,
    IMPORT_ANY,
    IMPORT_DICT,
    IMPORT_UNION,
    Import,
    _holds,
    _import_bindings,
    _import_key,
)
from datamodel_code_generator.model import _rebuild_model_with_datamodel_namespace, get_data_model_types
from datamodel_code_generator.model._decisions.hashability import (
    HashClassFacts,
    ValueHashFacts,
    field_value_hashes,
)
from datamodel_code_generator.model._decisions.union_assembly import join_union
from datamodel_code_generator.model._render_plan import (
    RENDER_READY,
    CallPlan,
    FieldRenderPlan,
    Kwarg,
    Omitted,
    Positional,
    RuleId,
    Why,
    frozen_render_plan,
)
from datamodel_code_generator.model.base import (
    _COMPILED_PATTERN_KEY,
    ALL_MODEL,
    UNDEFINED,
    BaseClassDataType,
    DataModel,
    DataModelFieldBase,
    _base_walk,
    _builtin_renderer,
    _default_state,
    _find_base_classes,
    _get_template_with_custom_dir,
    _has_opaque_custom_base,
    _model_written,
    _ModelKeys,
    _uses_original_template_loader,
    _written_name,
    overrides_builtin_templates,
)
from datamodel_code_generator.model.field_name import PYDANTIC_BASE_MODEL_ATTRIBUTES, PydanticFieldNameResolver
from datamodel_code_generator.model.imports import IMPORT_CLASSVAR
from datamodel_code_generator.model.pydantic_base import (
    PYTHON_ENGINE_BUILDS_KEY,
    BaseModelBase,
    _constraint_arguments,
    _FieldArguments,
)
from datamodel_code_generator.model.pydantic_base import Constraints as _Constraints
from datamodel_code_generator.model.pydantic_base import (
    DataModelField as _PydanticBaseDataModelField,
)
from datamodel_code_generator.model.pydantic_v2._config import (
    ConfigAttribute,
    build_base_config_parameters,
    config_attributes,
)
from datamodel_code_generator.model.pydantic_v2._output_context import (
    ANNOTATED_CONSTRAINTS_CONTEXT as _ANNOTATED_CONSTRAINTS_CONTEXT,
)
from datamodel_code_generator.model.pydantic_v2.imports import (
    IMPORT_ALIAS_CHOICES,
    IMPORT_ALIAS_GENERATOR_TO_CAMEL,
    IMPORT_ALIAS_GENERATOR_TO_PASCAL,
    IMPORT_ALIAS_GENERATOR_TO_SNAKE,
    IMPORT_BASE_MODEL,
    IMPORT_CONFIG_DICT,
    IMPORT_CONSTR,
    IMPORT_FIELD,
    IMPORT_FIELD_VALIDATOR,
    IMPORT_MISSING,
    IMPORT_MODEL_VALIDATOR,
    IMPORT_PYDANTIC_MISSING,
    IMPORT_STRING_CONSTRAINTS,
    IMPORT_TYPE_ADAPTER,
    IMPORT_VALIDATION_INFO,
    IMPORT_VALIDATOR_FUNCTION_WRAP_HANDLER,
)
from datamodel_code_generator.model.pydantic_v2.types import (
    AnnotatedConstraintDataType,
    DataTypeManager,
    EncodedTextDataType,
    PydanticV2DataType,
    constrained_base,
)
from datamodel_code_generator.model.pydantic_v2.version import _includes_dict_key_reference_classes, model_target_meets
from datamodel_code_generator.model.runtime_validation import (
    IndependentDeclaredPatternPropertiesRule,
    IndependentModelPatternPropertiesRule,
    SchemaRuntimeValidation,
    _counted_engine,
    _counts_every_pattern,
    _is_internal_schema_runtime_validation,
    alias_keeps_string_pattern,
    alias_pattern_counter,
    conditional_value_uses_json_equality,
    unique_items_path_uses_regex,
)
from datamodel_code_generator.python_literal import (
    PythonRuntimeExpression,
    _normalize_string,
    _normalized_safe_python_name,
    _text_constructor_expression,
    message_repr,
    represent_untrusted_python_value,
    stable_repr,
)
from datamodel_code_generator.reference import FieldNameResolver, ModelType, keep_node_records
from datamodel_code_generator.types import NONE, _is_value_literal, chain_as_tuple

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence

    from jinja2 import Template
    from typing_extensions import TypedDict, Unpack

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator._regex_syntax import PatternEngine
    from datamodel_code_generator._requirements import PydanticConstruct
    from datamodel_code_generator.enums import TargetPydanticVersion
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model._decisions.assignment import WrittenAssignment
    from datamodel_code_generator.model.pydantic_v2._schema_runtime_validation import (
        SchemaRuntimeValidationModulePlan,
    )
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType
    from datamodel_code_generator.types import DataTypeManager as DataTypeManagerBase


_CONSTRAINED_STRING_IMPORTS = frozenset({IMPORT_CONSTR, IMPORT_STRING_CONSTRAINTS, IMPORT_FIELD})
_IMPORT_JSON_VALUE = Import.from_full_path(form_path("pydantic.json_value"))


def _constraint_base(data_type: DataType, *, annotated_string: bool = False, encoded: bool = False) -> str | None:
    """Return the name of the scalar a constrained scalar constrains, or None for any other type.

    With ``annotated_string``, an annotated string constrains ``str``; with ``encoded``, a value decoded from base64
    text with constraints constrains the type its decoding writes.
    """
    if annotated_string and getattr(data_type, "annotated_string", False):
        return "str"
    if encoded and (base := getattr(data_type, "encoded_base", None)):
        return base
    return constrained_base(data_type)


class _RawRepr:
    """Wrapper to prevent repr() from adding quotes around a value."""

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value

    @override
    def __repr__(self) -> str:
        return self.value


def _supports_pydantic_typed_extra_dict_key(data_type: DataType) -> bool:  # noqa: PLR0911
    """Return whether Pydantic preserves the JSON object key as a string."""
    if data_type.reference or data_type.data_types:
        return False
    if data_type.python_type or data_type.enum_member_literals:
        return False
    if data_type.is_optional:
        return False
    if data_type._is_container:  # noqa: SLF001
        return False

    match data_type.literals:
        case [] if (
            (import_ := data_type.import_)
            and import_ in _CONSTRAINED_STRING_IMPORTS
            and data_type.type == import_.import_
            and (import_ != IMPORT_FIELD or data_type.base_type_hint == "str")
            and data_type.is_func
            and data_type.kwargs
            and data_type.alias is None
            and data_type.discriminator is None
        ):
            return True
        case literals if (
            data_type.type is None
            and data_type.import_ is None
            and not data_type.is_func
            and data_type.kwargs is None
            and data_type.alias is None
            and data_type.discriminator is None
            and all(isinstance(value, str) for value in literals)
        ):
            return True
    return False


def _get_plain_pattern_root_types() -> tuple[
    type[DataModel], type[DataModel], type[DataModelFieldBase], type[DataTypeManagerBase]
]:
    """Identify the uncustomized model types eligible for inert root annotations."""
    from .root_model import RootModel  # noqa: PLC0415

    return BaseModel, RootModel, DataModelField, DataTypeManager


def _supports_plain_pattern_root_annotations(
    model_type: type[DataModel],
    root_type: type[DataModel],
    field_type: type[DataModelFieldBase],
    manager_type: type[DataTypeManagerBase],
    pattern_types: Iterable[DataType],
) -> bool:
    """Decide whether this Pydantic implementation can ignore inert root annotations."""
    if (get_types := model_type.PLAIN_PATTERN_ROOT_TYPES) is None:
        return False
    expected_model, expected_root, expected_field, expected_manager = get_types()
    if (
        model_type is not expected_model
        or root_type is not expected_root
        or field_type is not expected_field
        or manager_type is not expected_manager
    ):
        return False
    return all(
        type(model := data_type.reference.source) is expected_model
        and all(type(field) is expected_field for field in cast("DataModel", model).fields)
        for data_type in pattern_types
        if data_type.reference is not None
    )


def _get_schema_runtime_validation_root_model() -> type[DataModel]:
    """Return the executable root model owned by the Pydantic v2 output."""
    from datamodel_code_generator.model.pydantic_v2.root_model import RootModel  # noqa: PLC0415

    return RootModel


class Constraints(_Constraints):
    """Pydantic v2 field constraints with pattern support."""

    # To override existing pattern alias
    regex: Optional[str] = Field(None, alias="regex")
    pattern: Optional[str] = Field(None, alias="pattern")

    @model_validator(mode="before")
    def validate_min_max_items(cls, values: Any) -> dict[str, Any]:  # noqa: N805
        """Validate and convert minItems/maxItems to minLength/maxLength."""
        if not isinstance(values, dict):
            return values
        min_items = values.pop("minItems", None)
        if min_items is not None:
            values["minLength"] = min_items
        max_items = values.pop("maxItems", None)
        if max_items is not None:
            values["maxLength"] = max_items
        return values


DataModelFieldV1 = _PydanticBaseDataModelField  # deprecated re-export, pydantic-v1 output removed in #3031

_ALIAS_GENERATOR_TEMPLATE_DATA_KEY = "alias_generator"
_ALIAS_GENERATOR_INTERNAL_KEY = "_alias_generator"
_NO_ALIAS_INTERNAL_KEY = "_no_alias"
_PIN_ALIASES_INTERNAL_KEY = "_pin_aliases_with_alias_generator"
_MISSING_SENTINEL = "MISSING"
_CONFIG_ITEMS_TEMPLATE_DATA_KEY = "config_items"
_RENDERED_NONE = "None"
_IMPORT_RE_COMPILE = Import.from_full_path("re.compile")
_NEUTRALIZE_ROOT_MODEL_EXTRA_CONFIG_TEMPLATE_DATA_KEY = "neutralize_root_model_extra_config"
_MIN_QUOTED_STRING_LENGTH = 2
_LEGACY_CONFIG_LITERAL_STRINGS: frozenset[str] = frozenset({"False", "None", "True"})
_COMPILED_PATTERN = Why(RuleId("D2.pydantic.compiled-pattern"), keyword="pattern")
_CONST = Why(RuleId("D5.const.literal"), keyword="const")
_UNIQUE_ITEMS = Why(RuleId("D2.pydantic.unique-items"), keyword="uniqueItems")
_UNIQUE_ITEMS_ARGUMENT = "unique_items"
"""The constraint argument ``Field()`` of Pydantic v2 cannot express."""
_FROZEN = Why(RuleId("D11.pydantic.frozen"), keyword="readOnly", option="--use-frozen-field")
_UNION_MODE = Why(RuleId("D6.union-mode"), keyword="union_mode")
_UNION_WRAPPING_CONSTRAINTS: frozenset[str] = _applicability.NUMBER_FIELD_KEYWORDS | _applicability.LENGTH_META_KEYWORDS
"""The constraint arguments Pydantic applies before ``union_mode``: on a union, each wraps it in a validator, which
``union_mode`` cannot take."""
_ALIAS_GENERATOR = Why(RuleId("D9.alias.generator"), keyword="alias")
_ALIAS_GENERATOR_PIN = Why(RuleId("D9.alias.generator-pinned"), keyword="alias")
_VALIDATION_ALIASES = Why(RuleId("D9.alias.validation-choices"), keyword="validation_aliases")
_SERIALIZATION_ALIAS = Why(RuleId("D9.alias.serialization"), keyword="serialization_alias")
_SERIALIZATION_ALIAS_FROM_ALIAS = Why(
    RuleId("D9.alias.serialization"), keyword="alias", option="--use-serialization-alias"
)
_JSON_SCHEMA_EXTRA = Why(RuleId("D11.pydantic.json-schema-extra"))
_NULL_DEFAULT = Why(RuleId("D4.default.pydantic-null-type"), keyword="default")
_LEGACY_PYDANTIC_EXTRA_TEMPLATE_PATTERN = re.compile(
    r"{%-?\s*(?:if|elif)\s+(?:not\s+)?field\.use_pydantic_extra_annotation_assignment\b"
)
_LEGACY_PYDANTIC_EXTRA_POST_CLASS_PATTERN = re.compile(
    r"(?m)^(?P<class_name>[^.\s]+)[ \t]*\.[ \t]*__annotations__[ \t]*"
    r"\[[ \t]*(?P<quote>['\"])__pydantic_extra__(?P=quote)[ \t]*\][ \t]*=[^\r\n]*\r?\n"
    r"(?:[ \t]*\r?\n)*"
    r"(?P=class_name)[ \t]*\.[ \t]*model_rebuild[ \t]*"
    r"\([ \t]*force[ \t]*=[ \t]*True[ \t]*\)[ \t]*(?:#[^\r\n]*)?(?:\r?\n)?"
)
_ALIAS_GENERATOR_IMPORTS: dict[str, Import] = {
    AliasGenerator.ToCamel.value: IMPORT_ALIAS_GENERATOR_TO_CAMEL,
    AliasGenerator.ToPascal.value: IMPORT_ALIAS_GENERATOR_TO_PASCAL,
    AliasGenerator.ToSnake.value: IMPORT_ALIAS_GENERATOR_TO_SNAKE,
}


def _uses_legacy_pydantic_extra_template(template_file_path: str) -> bool:
    """Return whether a custom template uses the pre-0.68.1 typed-extra property."""
    template_source = Path(template_file_path).read_text(encoding="utf-8")
    return bool(_LEGACY_PYDANTIC_EXTRA_TEMPLATE_PATTERN.search(template_source))


def _strip_legacy_pydantic_extra_post_class_assignment(rendered: str, class_name: str) -> str | None:
    """Remove the unsupported post-class typed-extra assignment for the rendered model."""
    if (
        assignment := next(
            (
                match
                for match in _LEGACY_PYDANTIC_EXTRA_POST_CLASS_PATTERN.finditer(rendered)
                if match["class_name"] == class_name
            ),
            None,
        )
    ) is None:
        return None
    return f"{rendered[: assignment.start()]}{rendered[assignment.end() :]}"


class _LegacyPydanticExtraTemplate:
    """Adapt pre-0.68.1 custom templates without affecting normal render paths."""

    __slots__ = ("_template",)

    def __init__(self, template: Template) -> None:
        self._template = template

    def __getattr__(self, name: str) -> Any:
        return getattr(self._template, name)

    def _warn_template_update(self, status: str) -> None:
        warn(
            f"Legacy custom template {stable_repr(self._template.filename)} {status} for Pydantic typed-extra "
            "compatibility. Update the template to declare __pydantic_extra__ in the class body and remove the "
            "post-class annotation assignment and model_rebuild(force=True).",
            stacklevel=3,
        )

    def render(self, *args: Any, **kwargs: Any) -> str:
        """Render typed extras in the class body and remove the unsupported legacy tail."""
        if (
            field := next(
                (
                    field
                    for field in kwargs.get("fields", ())
                    if getattr(field, "use_pydantic_extra_annotations_dict", False)
                ),
                None,
            )
        ) is None:
            return self._template.render(*args, **kwargs)

        annotation_line = f"    '__pydantic_extra__': {field.pydantic_extra_type_hint},"
        kwargs["class_body_lines"] = [
            "__annotations__ = {",
            annotation_line,
            "}",
            *(kwargs.get("class_body_lines") or ()),
        ]
        rendered = self._template.render(*args, **kwargs)
        if annotation_line not in rendered:
            self._warn_template_update("could not be fully rewritten automatically")
            return rendered
        if (adapted := _strip_legacy_pydantic_extra_post_class_assignment(rendered, kwargs["class_name"])) is None:
            self._warn_template_update("could not be fully rewritten automatically")
            return rendered
        self._warn_template_update("was rewritten automatically")
        return adapted


@_uses_original_template_loader
def _adapt_legacy_pydantic_extra_template(template: Template) -> Template:
    """Wrap only custom templates that use the removed typed-extra property."""
    match template.filename:
        case str() as filename if _uses_legacy_pydantic_extra_template(filename):
            return cast("Template", _LegacyPydanticExtraTemplate(template))
    return template


def _alias_generator_name(value: Any) -> str | None:
    generator_name: str | None = None
    match value:
        case AliasGenerator():
            generator_name = value.value
        case str():
            normalized_value = _normalize_string(value)
            if normalized_value in _ALIAS_GENERATOR_IMPORTS:
                generator_name = normalized_value
    return generator_name


def _config_dict_items(config: Any) -> list[tuple[str, Any]]:
    if config is None:
        return []

    if isinstance(config, dict):
        return list(config.items())

    dump = getattr(config, "model_dump", None) or getattr(config, "dict", None)
    if not dump:
        return []
    values = dump(exclude_unset=True)
    return list(as_mapping(values).items())


def _decode_legacy_quoted_config_string(value: str) -> str:
    """Decode the simple quoted strings emitted by older template-data files.

    This is deliberately not a Python-literal parser.  Existing configuration
    fixtures use ``'allow'`` and ``"python-re"`` spellings; accepting only
    an unescaped matching quote preserves those values while strings with escape
    syntax remain ordinary data and are safely serialized below.
    """
    if (
        len(value) >= _MIN_QUOTED_STRING_LENGTH
        and value[0] == value[-1]
        and value[0] in {"'", '"'}
        and "\\" not in value[1:-1]
    ):
        return value[1:-1]
    return value


def _safe_config_dict_items(config: Any, target: PythonVersion) -> list[tuple[str, str]]:
    """Return Python-source-safe ConfigDict arguments for built-in templates, each key a name of the target Python."""
    safe_items: list[tuple[str, str]] = []
    for field_name, value in _config_dict_items(config):
        if (normalized_field_name := _normalized_safe_python_name(field_name, target)) is None:
            continue
        normalized_value = _normalize_string(value) if isinstance(value, str) else value
        if normalized_field_name == _ALIAS_GENERATOR_TEMPLATE_DATA_KEY and (
            name := _alias_generator_name(normalized_value)
        ):
            safe_items.append((normalized_field_name, name))
            continue
        if isinstance(normalized_value, str) and normalized_value in _LEGACY_CONFIG_LITERAL_STRINGS:
            safe_items.append((normalized_field_name, normalized_value))
            continue
        if isinstance(normalized_value, str):
            normalized_value = _decode_legacy_quoted_config_string(normalized_value)
        rendered_value = represent_untrusted_python_value(normalized_value)
        if normalized_field_name == "regex_engine" and isinstance(normalized_value, str):
            # Keep the existing double-quoted spelling for generated regex
            # engine settings while escaping every string character safely.
            rendered_value = json.dumps(normalized_value, ensure_ascii=False)
        safe_items.append((normalized_field_name, rendered_value))
    return safe_items


def _template_config_items(extra_template_data: Mapping[str, Any], target: PythonVersion) -> list[tuple[str, str]]:
    """Return the safe items of the ``config`` template data, which the built-in templates write for the target."""
    return _safe_config_dict_items(config, target) if (config := extra_template_data.get("config")) else []


def _config_constructs(config_items: Iterable[tuple[str, str]]) -> Iterator[tuple[PydanticConstruct, str | None]]:
    """Yield the row of every key of a rendered config, as a construct of the model that renders it."""
    for key, _ in config_items:
        if (row := CONFIG_KEY_REQUIREMENTS.get(key)) is not None:
            yield cast("PydanticConstruct", row.construct), None


def _import_rendered_alias_generator(model: DataModel, config_items: Iterable[tuple[str, str]]) -> None:
    """Import the known alias generator that the model's rendered config items name.

    A BaseModel, RootModels included, also keeps importing the run's ``--alias-generator`` where it does not render
    it (``legacy-run-alias-generator-import``): class naming avoids every imported name of a module, so dropping that
    import would rename classes, until the module import ledger binds names without writing imports.
    """
    if (generator := _alias_generator_name(dict(config_items).get(_ALIAS_GENERATOR_TEMPLATE_DATA_KEY))) is None:
        return
    if (import_ := _ALIAS_GENERATOR_IMPORTS[generator]) not in (additional_imports := model._additional_imports):  # noqa: SLF001
        additional_imports.append(import_)


_PYDANTIC_V2_DEFAULT_FIELD_KEYS: frozenset[str] = frozenset({
    "default",
    "default_factory",
    "alias",
    "alias_priority",
    "validation_alias",
    "serialization_alias",
    "title",
    "description",
    "examples",
    "exclude",
    "discriminator",
    "json_schema_extra",
    "frozen",
    "validate_default",
    "repr",
    "init_var",
    "kw_only",
    "pattern",
    "strict",
    "gt",
    "ge",
    "lt",
    "le",
    "multiple_of",
    "allow_inf_nan",
    "max_digits",
    "decimal_places",
    "min_length",
    "max_length",
    "union_mode",
})


def _compiled_python_pattern(
    pattern: object,
    data_type: DataType,
    prefix: str | None,
    prepared: dict[str, PythonRuntimeExpression],
    field: DataModelFieldBase,
) -> PythonRuntimeExpression | None:
    """Keep a Python pattern independent of its consuming model's config.

    A ``None`` prefix selects every pattern that needs Python's engine, as ``has_lookaround_pattern`` does
    for ``field``, the field that owns the pattern. A pattern that holds a lone surrogate stays a string, as no
    Pydantic builds a compiled one (``alias_keeps_string_pattern``).
    """
    if (
        not isinstance(pattern, str)
        or holds_lone_surrogate(pattern)
        or not (_needs_python_regex_engine(pattern, data_type, field) if prefix is None else pattern.startswith(prefix))
    ):
        return None
    pattern = str(pattern)
    if compiled := prepared.get(pattern):
        return compiled
    # Pydantic >=2.8 uses Python's engine for compiled patterns, even without an enclosing model's config.
    compiled = _text_constructor_expression(_IMPORT_RE_COMPILE, pattern)
    prepared[pattern] = compiled
    return compiled


def _prepare_field_python_pattern(
    field: DataModelFieldBase, prefix: str | None, prepared: dict[str, PythonRuntimeExpression]
) -> bool:
    """Compile a selected Field() constraint pattern once and report whether it changed."""
    if isinstance(field.extras.get(_COMPILED_PATTERN_KEY), PythonRuntimeExpression) or not (
        isinstance(field, _PydanticBaseDataModelField)
        and isinstance(field.constraints, Constraints)
        and (pattern := _compiled_python_pattern(field.constraints.pattern, field.data_type, prefix, prepared, field))
        and not field._has_anyurl_outside_container()  # noqa: SLF001
    ):
        return False
    field.extras = {**field.extras, _COMPILED_PATTERN_KEY: pattern}
    field._set_runtime_expression_imports((*field.runtime_expression_imports, pattern.import_))  # noqa: SLF001
    return True


def _prepare_python_patterns(
    field: DataModelFieldBase, prefix: str, prepared: dict[str, PythonRuntimeExpression]
) -> None:
    """Prepare selected Python patterns using the existing runtime-expression import machinery."""
    _prepare_field_python_pattern(field, prefix, prepared)
    for data_type in field.data_type.all_data_types:
        if data_type.kwargs and (
            pattern := _compiled_python_pattern(data_type.kwargs.get("pattern"), data_type, prefix, prepared, field)
        ):
            data_type.kwargs["pattern"] = pattern
            data_type._set_runtime_expression_imports((*data_type.runtime_expression_imports, pattern.import_))  # noqa: SLF001


def _copy_alias_data_type(
    data_type: DataType, prepared: dict[str, PythonRuntimeExpression] | None, field: DataModelFieldBase
) -> DataType | None:
    """Copy only the path to alias-specific nodes; collapsed fields may share the other nodes.

    ``con*`` calls render as ``Annotated`` constraints because type checkers reject calls in aliases,
    and patterns that need Python's engine are compiled because an alias has no model config.
    Without ``prepared`` the patterns stay strings, for targets that predate compiled patterns.
    """
    nested = [_copy_alias_data_type(nested_type, prepared, field) for nested_type in data_type.data_types]
    dict_key = None if data_type.dict_key is None else _copy_alias_data_type(data_type.dict_key, prepared, field)
    kwargs = data_type.kwargs
    pattern = (
        _compiled_python_pattern(kwargs.get("pattern"), data_type, None, prepared, field)
        if kwargs and prepared is not None
        else None
    )
    base = (
        _constraint_base(data_type)
        if data_type.is_func and kwargs and not isinstance(data_type, AnnotatedConstraintDataType)
        else None
    )
    if pattern is None and base is None and dict_key is None and not any(nested):
        return None
    runtime_imports = data_type.runtime_expression_imports
    if base is None:
        copied = data_type.model_copy()
    else:
        copied = AnnotatedConstraintDataType.of_call(data_type, base)
        keep_node_records(copied, data_type)
        runtime_imports = (*copied.runtime_expression_imports, *runtime_imports)
    if pattern is not None:
        copied.kwargs = {**cast("dict[str, Any]", kwargs), "pattern": pattern}
        runtime_imports = (*runtime_imports, pattern.import_)
    copied._set_runtime_expression_imports(runtime_imports)  # noqa: SLF001
    if any(nested):
        copied.data_types = [
            copied_type or nested_type for copied_type, nested_type in zip(nested, data_type.data_types, strict=True)
        ]
    if dict_key is not None:
        copied.dict_key = dict_key
    for nested_type in (*nested, dict_key):
        if nested_type is not None:
            nested_type.parent = copied
    return copied


def _prepare_type_alias_field(
    field: DataModelFieldBase,
    prepared: dict[str, PythonRuntimeExpression],
    replace_data_type: Callable[[DataModelFieldBase, DataType], None],
) -> bool:
    """Make a type alias body a valid type expression that validates without a model config.

    A pattern the alias keeps a string (``alias_keeps_string_pattern``) has its engine selected by the models that
    use the alias.
    """
    compiled = prepared if model_target_meets(field.parent, "pydantic.compiled_pattern") else None
    changed = compiled is not None and _prepare_field_python_pattern(field, None, compiled)
    if (data_type := _copy_alias_data_type(field.data_type, compiled, field)) is None:
        return changed
    replace_data_type(field, data_type)
    return True


def _type_without_pattern(data_type: DataType) -> DataType:
    """Return a new type that writes ``data_type`` without its ``pattern`` (#6941).

    It keeps its class and its other keywords; a string type left with none is the ``str`` it constrains.
    """
    kwargs = {key: value for key, value in (data_type.kwargs or {}).items() if key != "pattern"}
    fresh = {"parent": None, "children": [], "data_types": []}
    if kwargs or _constraint_base(data_type, annotated_string=True) != "str":
        return data_type.model_copy(update={**fresh, "kwargs": kwargs or None})
    values = {**dict(data_type), **fresh, "type": "str", "import_": None, "is_func": False, "kwargs": None}
    written = PydanticV2DataType.model_construct(None, **values)
    keep_node_records(written, data_type)
    return written


def _writes_no_call_without_plan(field: DataModelFieldBase) -> bool:
    """Return whether ``field`` has nothing a ``Field()`` call could write and needs no null default, so no call.

    Only the Pydantic v2 field class itself answers it, its subclasses build their plans: its plan is then built
    without a call (``_build_render_plan``) and its imports read no plan (``WRITES_NO_CALL_WITHOUT_PLAN``).
    """
    return (
        type(field) is DataModelField
        and not field.is_class_var
        and not field._may_have_field_statement()  # noqa: SLF001
        and field._alias_generator_name_from_parent() is None  # noqa: SLF001
        and ((parent := field.parent) is None or not parent._uses_custom_root_template)  # noqa: SLF001
        and not field._requires_null_default(  # noqa: SLF001
            missing_sentinel=field._uses_missing_sentinel(factory_present=False)  # noqa: SLF001
        )
    )


def _needs_model_regex_engine(fields: list[DataModelFieldBase]) -> bool:
    """Return whether a pattern of the fields needs the ``python-re`` engine of the model that holds them.

    A lone surrogate is left to ``_select_surrogate_regex_engines``.
    """
    return has_lookaround_pattern(fields, surrogates=False)


def _pattern_needs_holder_engine(
    root_type: type[DataModel], pattern: str, target_pydantic: TargetPydanticVersion | None
) -> bool:
    """Return whether a string pattern in a model of ``root_type`` needs the ``python-re`` engine of its users.

    A type alias has no config, and the patterns it keeps strings (``alias_keeps_string_pattern``) Pydantic builds
    with the config of the model that uses the alias (``models_reaching_string_pattern_aliases``). A lone surrogate
    counts: the model kept for it holds the engine it asks for apart from the other patterns of its users.
    """
    from datamodel_code_generator.model.type_alias import TypeAliasBase  # noqa: PLC0415

    if not issubclass(root_type, TypeAliasBase) or not alias_keeps_string_pattern(pattern, target_pydantic):
        return False
    return pattern_engine(pattern, row="model") != "default"


def _remove_compiled_pattern_metadata(args: _FieldArguments) -> None:
    """Keep prepared expressions out of json_schema_extra while preserving user-supplied extras."""
    if isinstance(args.get(_COMPILED_PATTERN_KEY), PythonRuntimeExpression):
        args.pop(_COMPILED_PATTERN_KEY, "compiled-pattern-metadata", _COMPILED_PATTERN)


def _choice_key(member: DataType) -> object:
    """Return what makes two members of a union one choice: a ``Literal``'s set of values, else the written type."""
    if member.literals and not member.enum_member_literals and not member.alias:
        return frozenset((type(value), value) for value in member.literals)
    return member.type_hint


def _validates_as_union(data_type: DataType) -> bool:
    """Return whether Pydantic validates a type as a union of two or more choices, the one schema ``union_mode`` takes.

    Pydantic validates a union beside None as the union, and a type alias as its value: a type is one when its members,
    None aside, write two or more types (equal ``Literal`` types are one) or one that is such a union, when it names
    a type alias whose value is one, or when the annotation it is given (``x-python-type``) joins two or more types.
    Any other type is a single choice.
    """
    if data_type in CONTAINERS:
        return False
    if not (data_type.alias or data_type.type):
        if data_type.data_types:
            choices = [
                *{_choice_key(member): member for member in data_type.data_types if member.type != NONE}.values()
            ]
            return len(choices) > 1 or (len(choices) == 1 and _validates_as_union(choices[0]))
        if (reference := data_type.reference) is not None and (source := reference.source) is not None:
            return source.is_alias and any(_validates_as_union(field.data_type) for field in source.fields[:1])
    if (bound := data_type.python_type) is None:
        return False
    from datamodel_code_generator._python_type_annotation import iter_union_choices  # noqa: PLC0415

    return len({*iter_union_choices(bound.expression)}) > 1


class DataModelField(_PydanticBaseDataModelField):
    """Pydantic v2 field with Field() constraints and json_schema_extra support."""

    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = True
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = _ANNOTATED_CONSTRAINTS_CONTEXT
    SUPPORTS_DISCRIMINATOR: ClassVar[bool] = True
    _CONSTRAINT_ARGUMENTS: ClassVar[dict[str, str]] = _constraint_arguments(Constraints)
    _UNWRITTEN_CONSTRAINT_ARGUMENTS: ClassVar[frozenset[str]] = frozenset({_UNIQUE_ITEMS_ARGUMENT})
    _TYPE_MANAGER: ClassVar[type[DataTypeManagerBase]] = DataTypeManager
    PREPARE_PYTHON_PATTERNS = staticmethod(_prepare_python_patterns)
    PREPARE_TYPE_ALIAS_FIELD = staticmethod(_prepare_type_alias_field)
    _TYPE_WITHOUT_PATTERN = staticmethod(_type_without_pattern)
    NEEDS_MODEL_REGEX_ENGINE = staticmethod(_needs_model_regex_engine)
    WRITES_NO_CALL_WITHOUT_PLAN = staticmethod(_writes_no_call_without_plan)
    PATTERN_NEEDS_HOLDER_ENGINE = staticmethod(_pattern_needs_holder_engine)
    _EXCLUDE_FIELD_KEYS: ClassVar[set[str]] = {
        "alias",
        "default",
        "gt",
        "ge",
        "lt",
        "le",
        "multiple_of",
        "min_length",
        "max_length",
        "pattern",
    }
    _DEFAULT_FIELD_KEYS: ClassVar[frozenset[str]] = _PYDANTIC_V2_DEFAULT_FIELD_KEYS
    constraints: Optional[Constraints] = None  # ty: ignore[invalid-mutable-override]
    can_have_extra_keys: ClassVar[bool] = False
    _PYDANTIC_EXTRA_FIELD_NAME: ClassVar[str] = "__pydantic_extra__"
    _PYDANTIC_EXTRA_PLAIN_ANNOTATION_KEY: ClassVar[str] = "pydantic_extra_plain_annotation"

    @override
    def _pydantic_constructs(self) -> tuple[PydanticConstruct, ...]:
        """Return the Pydantic constructs that the field's ``Field()`` call and constrained types write."""
        call = self._render_plan().call
        written = {} if call is None else {argument.name: argument.value for argument in call.kwargs}
        constructs: list[PydanticConstruct] = []
        if "union_mode" in written:
            constructs.append("pydantic.union_mode")
        if isinstance(pattern := written.get("pattern"), PythonRuntimeExpression) or any(
            isinstance((data_type.kwargs or {}).get("pattern"), PythonRuntimeExpression)
            for data_type in self.data_type.all_data_types
        ):
            constructs.append("pydantic.compiled_pattern")
        if any(data_type.import_ == _IMPORT_JSON_VALUE for data_type in self.data_type.all_data_types):
            constructs.append("pydantic.json_value")
        if pattern is not None:
            match self._pattern_member_kind():
                case "bytes":
                    constructs.append("pydantic.bytes_pattern")
                case "string" if any(member.type in _BYTES_PATTERN_TYPES for member in self.data_type.data_types):
                    constructs.append("pydantic.str_bytes_union_pattern")
        return tuple(constructs)

    def _pattern_member_kind(self) -> Literal["string", "bytes"] | None:
        """Return what a pattern of the field constrains: a string type, else only a ``bytes`` type, else neither."""
        if _pattern_data_type(self.data_type, includes_bytes=False) is not None:
            return "string"
        return "bytes" if _pattern_data_type(self.data_type, includes_bytes=True) is not None else None

    @override
    def _writes_bytes_pattern_rust_rejects(self) -> bool:
        """Return whether the field writes a string pattern on ``bytes`` whose syntax needs Python's ``re`` (#6673).

        Pydantic compiles it from 2.14 on, with the default engine unless the config selects ``python-re``; the
        decision is ``pattern_engine``'s, as for a string pattern Pydantic compiles.
        """
        if self._pattern_member_kind() != "bytes":
            return False
        call = self._render_plan().call
        return call is not None and any(
            argument.name == "pattern"
            and isinstance(pattern := argument.value, str)
            and pattern_engine(pattern, row="model") == "python"
            for argument in call.kwargs
        )

    @property
    @override
    def dropped_constraints_warning(self) -> str | None:
        """Return the warning of the base field, and one naming base64 constraints the explicit target does not check.

        Pydantic checks the constraints of a value written as base64 text on that text, and the strictness of a strict
        string decoded from it, only from 2.10 on.
        """
        messages = [super().dropped_constraints_warning]
        if not model_target_meets(self.parent, "pydantic.encoded_text_constraints") and (
            checked := sorted({
                name
                for data_type in self.data_type.all_data_types
                if isinstance(data_type, EncodedTextDataType)
                for name in (*(data_type.kwargs or ()), *(("strict",) if data_type.strict else ()))
            })
        ):
            minimum = REQUIREMENTS["pydantic.encoded_text_constraints"].minimum
            messages.append(
                f"Pydantic before {minimum} does not check {', '.join(checked)} on "
                f"base64 text, so {self._constraint_owner} checks {for_count(len(checked), 'it', 'them')} only on "
                f"Pydantic {minimum} or later."
            )
        return " ".join(message for message in messages if message) or None

    @field_validator("extras")
    def validate_extras(cls, values: Any) -> dict[str, Any]:  # noqa: N805
        """Validate and convert example to examples list."""
        if not isinstance(values, dict):  # pragma: no cover - pydantic validates extras to a dict first
            return values
        if "examples" in values:
            return values

        if "example" in values:
            values["examples"] = [values.pop("example")]
        return values

    @override
    def process_const(self) -> None:
        """Process const field constraint using literal type."""
        self._process_const_as_literal()

    @property
    def use_missing_sentinel_default(self) -> bool:
        """Return whether this field should use the Pydantic MISSING sentinel as its default."""
        if (plan := frozen_render_plan(self)) is not None:
            return plan.default.missing_sentinel
        return self._uses_missing_sentinel(factory_present=False) and not self.has_default_factory_in_field

    @override
    def _uses_missing_sentinel(self, *, factory_present: bool) -> bool:
        if not self.use_missing_sentinel:
            return False
        if self.is_class_var or self.required or self.has_default or factory_present:
            return False
        return _default_state(self.default) != "value"

    @property
    @override
    def represented_default(self) -> str:
        """Get the rendered default value for the field."""
        if (plan := frozen_render_plan(self)) is not None:
            default = plan.default
        else:
            default = self._field_default(missing=self.use_missing_sentinel_default)
        return _MISSING_SENTINEL if default.missing_sentinel else self._written_default(default)

    @override
    def should_strip_default_none(self, *, keep_optional: bool = False) -> bool:
        """Return whether an actual None default should be omitted."""
        if self.use_missing_sentinel_default:
            return False
        return super().should_strip_default_none(keep_optional=keep_optional)

    @override
    def _written_assignment(self) -> WrittenAssignment:
        """Return the field's written assignment: the model's class body keeps a stripped ``None`` for a type with None.

        The model template writes ``= None`` for a type rendered with None whatever ``--strip-default-none`` says; the
        root model template strips it.
        """
        return _model_written(
            self,
            keeps_none=((parent := self.parent) is None or not parent.IS_ROOT_MODEL) and self.data_type.admits_none,
        )

    @property
    @override
    def fall_back_to_nullable(self) -> bool:
        """Return whether optional fields should fall back to nullable type hints."""
        if not self.use_missing_sentinel_default:
            return super().fall_back_to_nullable
        return bool(self.nullable or self.type_has_null)

    def _requires_null_default(self, *, missing_sentinel: bool) -> bool:
        """Return whether Pydantic 2 needs ``Field(None)`` for a ``None`` type that has no default."""
        if missing_sentinel:
            return False
        if self.required or self.default is not None or self.has_default_factory:
            return False
        return self.data_type.type == "None"

    @override
    def _build_render_plan(self) -> FieldRenderPlan:
        """Include the explicit null default required by Pydantic v2; a field without metadata writes no call."""
        if _writes_no_call_without_plan(self):
            return self._field_render_plan(
                None,
                self._field_default(missing=self._uses_missing_sentinel(factory_present=False)),
                has_default_factory_in_field=False,
                omitted=tuple(self._warned_omissions()[0]),
            )
        plan = super()._build_render_plan()
        if (
            plan.annotation_form == "classvar"
            or plan.call is not None
            or not self._requires_null_default(missing_sentinel=plan.default.missing_sentinel)
        ):
            return plan
        return self._field_render_plan(
            CallPlan(
                Positional("none", _NULL_DEFAULT), None, (), default_keyword=self.use_default_kwarg, why=_NULL_DEFAULT
            ),
            plan.default,
            has_default_factory_in_field=plan.has_default_factory_in_field,
            omitted=plan.omitted,
        )

    @property
    @override
    def type_hint(self) -> str:
        """Get the type hint including MISSING when this field uses the sentinel default."""
        type_hint = super().type_hint
        if not self.use_missing_sentinel_default:
            return type_hint
        return self._type_hint_with_missing_sentinel(type_hint)

    def _type_hint_with_missing_sentinel(self, type_hint: str) -> str:
        """Write ``MISSING`` after the hint as written, which a union in it keeps (``legacy-missing-appended``)."""
        if not type_hint:
            return _MISSING_SENTINEL
        return join_union((type_hint, _MISSING_SENTINEL), union_operator=self._use_union_operator)

    @property
    def is_pydantic_extra_field(self) -> bool:
        """Return whether this field represents Pydantic typed extra values."""
        return self.name == self._PYDANTIC_EXTRA_FIELD_NAME

    @property
    @override
    def requires_immediate_forward_reference_resolution(self) -> bool:
        """Keep typed-extra class-body annotations quoted until their targets exist."""
        return self.is_pydantic_extra_field

    @property
    def use_pydantic_extra_plain_annotation(self) -> bool:
        """Return whether typed extras can use a regular class annotation."""
        return self.is_pydantic_extra_field and self._parent_flag(self._PYDANTIC_EXTRA_PLAIN_ANNOTATION_KEY)

    def _parent_flag(self, key: str) -> bool:
        """Return whether the parent's template data sets ``key``."""
        return (parent := self.parent) is not None and bool(parent.extra_template_data.get(key))

    @property
    def use_pydantic_extra_annotations_dict(self) -> bool:
        """Return whether typed extras need a class-body __annotations__ dict."""
        return self.is_pydantic_extra_field and not self.use_pydantic_extra_plain_annotation

    @property
    def use_pydantic_extra_annotation_assignment(self) -> bool:
        """Support the typed-extra property used by pre-0.68.1 custom templates."""
        return self.use_pydantic_extra_annotations_dict

    @property
    def pydantic_extra_type_hint(self) -> str:
        """Return a Dict-based type hint for Pydantic 2.0 typed extras."""
        data_type = self.data_type
        if not (data_type.is_dict and data_type.use_standard_collections and not data_type.use_generic_container):
            return self.type_hint
        return self._type_hint_from_data_type(data_type.model_copy(update={"use_standard_collections": False}))

    @override
    def _normalized_constraint_arguments(
        self, omitted: list[Omitted], *, beside_models: bool = True
    ) -> dict[str, Kwarg]:
        """Use prepared patterns after the shared constraint filtering and normalization."""
        arguments = super()._normalized_constraint_arguments(omitted, beside_models=beside_models)
        if (
            isinstance(pattern := self.extras.get(_COMPILED_PATTERN_KEY), PythonRuntimeExpression)
            and (argument := arguments.get("pattern")) is not None
            and argument.value == str(pattern)
        ):
            arguments["pattern"] = argument._replace(value=pattern, why=_COMPILED_PATTERN)
        return arguments

    @override
    def _process_field_arguments(self, args: _FieldArguments) -> None:
        _remove_compiled_pattern_metadata(args)
        if self.const:
            args.pop("const", "consumed-by-type", _CONST)

        args.pop(_UNIQUE_ITEMS_ARGUMENT, "backend-cannot-express", _UNIQUE_ITEMS)

        if self.use_frozen_field and self.read_only:
            args.put("frozen", True, _FROZEN, ("read_only",))  # noqa: FBT003

        match args.take("union_mode"):
            case None:
                pass
            case union_mode if (
                union_mode.value
                and self.data_type.is_union
                and "discriminator" not in args
                and _UNION_WRAPPING_CONSTRAINTS.isdisjoint(args.data())
                and _validates_as_union(self.data_type)
            ):
                args.put("union_mode", cast("Any", union_mode.value).value, _UNION_MODE, union_mode.items)
            case union_mode:
                args.omit_entry(union_mode, "union-mode-inapplicable", _UNION_MODE)

        self._put_alias_arguments(args)
        self._bundle_json_schema_extra(args)

    def _put_alias_arguments(self, args: _FieldArguments) -> None:
        """Write the alias, validation aliases and serialization alias arguments the field's aliases need."""
        pinned_alias = self._update_alias_for_alias_generator(args)
        has_alias = "alias" in args
        if self.validation_aliases:
            self._put_validation_aliases(args, self.validation_aliases, has_alias=has_alias)

        if self.serialization_alias is not None and (has_alias or self._serializes_under(self.serialization_alias)):
            args.put("serialization_alias", self.serialization_alias, _SERIALIZATION_ALIAS, ("serialization_alias",))

        if self.use_serialization_alias and (alias_entry := args.take("alias")) is not None:
            if self.serialization_alias is not None:
                serialization_alias, serialization_items = self.serialization_alias, ("serialization_alias",)
            else:
                serialization_alias, serialization_items = cast("str", alias_entry.value), ()
            if self._serializes_under(serialization_alias):
                args.put(
                    "serialization_alias",
                    serialization_alias,
                    _SERIALIZATION_ALIAS_FROM_ALIAS,
                    (*alias_entry.items, *serialization_items),
                )
            else:
                args.omit_entry(alias_entry, "serialization-alias-is-name", _SERIALIZATION_ALIAS_FROM_ALIAS)

        if self.serialization_alias is not None and not args.writes("serialization_alias"):
            args.omit(
                "serialization_alias", self.serialization_alias, "serialization-alias-is-name", _SERIALIZATION_ALIAS
            )

        if pinned_alias is not None and "alias" not in args:
            args.put("alias", pinned_alias, _ALIAS_GENERATOR_PIN)

    def _put_validation_aliases(self, args: _FieldArguments, validation_aliases: list[str], *, has_alias: bool) -> None:
        """Write ``AliasChoices`` in place of the alias, and the serialization alias it leaves to write."""
        alias = args.get("alias")
        alias_items = () if (alias_entry := args.entry("alias")) is None else alias_entry.items
        unique_validation_aliases = list(dict.fromkeys(validation_aliases))
        if self.serialization_alias is not None:
            serialization_alias, serialization_items = self.serialization_alias, ("serialization_alias",)
        elif has_alias:
            serialization_alias, serialization_items = alias, alias_items
        else:
            serialization_alias, serialization_items = unique_validation_aliases[0], ("validation_aliases",)
        args.pop("alias", "validation-aliases", _VALIDATION_ALIASES)
        args.put(
            "validation_alias",
            _RawRepr(call_text("AliasChoices", map(stable_repr, unique_validation_aliases))),
            _VALIDATION_ALIASES,
            ("validation_aliases",),
        )
        if (
            self.use_serialization_alias
            and serialization_alias is not None
            and self._serializes_under(serialization_alias)
        ):
            args.put("serialization_alias", serialization_alias, _SERIALIZATION_ALIAS, serialization_items)

    def _serializes_under(self, alias: str) -> bool:
        """Return whether serializing under ``alias`` needs it written: it is not the name the generator gives it."""
        return alias != self.name or self._alias_generator_renames(alias)

    def _bundle_json_schema_extra(self, args: _FieldArguments) -> None:
        """Move the arguments ``Field()`` does not take into ``json_schema_extra``."""
        extra_field_keys = tuple(
            k
            for k in args.names()
            if k not in self._DEFAULT_FIELD_KEYS
            and (k != "deprecated" or not model_target_meets(self.parent, "pydantic.field_deprecated"))
        )
        existing = args.entry("json_schema_extra")
        json_schema_extra = {
            **((existing.value if existing is not None else None) or {}),  # ty: ignore[invalid-argument-type]
            **{k: args.get(k) for k in extra_field_keys},
        }
        if json_schema_extra:
            moved = [cast("Kwarg", args.take(key)) for key in extra_field_keys]
            items = tuple(item for argument in moved for item in argument.items)
            if existing is None:
                args.put("json_schema_extra", json_schema_extra, _JSON_SCHEMA_EXTRA, items)
            else:
                args.update("json_schema_extra", json_schema_extra, _JSON_SCHEMA_EXTRA, items)

    def _update_alias_for_alias_generator(self, args: _FieldArguments) -> str | None:
        """Keep only an alias the parent's generator does not reproduce and return the generated alias to pin.

        A target predating the 2.8 generator algorithms pins that alias on the fields of a BaseModel and, with
        ``--pin-aliases-with-alias-generator``, of a dataclass whose config names the generator: never a RootModel,
        enum, union or type alias.
        """
        if self.name is None or self.is_pydantic_extra_field:
            return None
        if (generator_name := self._alias_generator_name_from_parent()) is None:
            return None
        generated_alias = _generate_alias(generator_name, self.name, self.data_type.python_version)
        pinned_alias: str | None = None
        if not model_target_meets(parent := self.parent, "pydantic.alias_generators") and (
            (isinstance(parent, BaseModel) and not parent.IS_ROOT_MODEL)
            or (self._pins_aliases() and _ALIAS_GENERATOR_TEMPLATE_DATA_KEY in self._parent_config_items())
        ):
            pinned_alias = self.name if generated_alias is None else generated_alias
        alias = args.get("alias")
        if alias is None and self._automatic_alias_disabled_for_alias_generator():
            return pinned_alias
        if (wire_name := alias if alias is not None else self.original_name) is None:
            return pinned_alias
        if generated_alias == wire_name:
            args.pop("alias", "alias-generator-reproduces", _ALIAS_GENERATOR)
            return pinned_alias
        if "alias" in args:
            args.update("alias", wire_name, _ALIAS_GENERATOR)
        else:
            args.put("alias", wire_name, _ALIAS_GENERATOR)
        return pinned_alias

    def _pins_aliases(self) -> bool:
        """Return whether ``--pin-aliases-with-alias-generator`` decides the aliases of the parent's fields."""
        return self._parent_flag(_PIN_ALIASES_INTERNAL_KEY)

    def _parent_config_items(self) -> dict[str, str]:
        """Return the config items that Pydantic applies to the parent model, or none for a parent of another kind."""
        return parent._applied_config_items() if isinstance(parent := self.parent, BaseModel) else {}  # noqa: SLF001

    def _alias_generator_name_from_parent(self) -> str | None:
        """Return the generator the parent's template data names, else the generic base's generator.

        With ``--pin-aliases-with-alias-generator`` it is the generator of the config Pydantic applies to the parent,
        else the generic base's generator. A rendered ``None`` is no generator; a rendered generator that dcg does not
        know is returned as its source, which ``_generate_alias`` cannot run, so every field keeps its alias.
        """
        if (parent := self.parent) is None:
            return None
        if not self._pins_aliases():
            alias_generator = parent.extra_template_data.get(_ALIAS_GENERATOR_TEMPLATE_DATA_KEY)
            if alias_generator is None:
                alias_generator = parent.extra_template_data.get(_ALIAS_GENERATOR_INTERNAL_KEY)
            return _alias_generator_name(alias_generator)
        if _ALIAS_GENERATOR_TEMPLATE_DATA_KEY in (config_items := self._parent_config_items()):
            rendered = config_items[_ALIAS_GENERATOR_TEMPLATE_DATA_KEY]
            return None if rendered == _RENDERED_NONE else _alias_generator_name(rendered) or rendered
        return _alias_generator_name(parent.extra_template_data.get(_ALIAS_GENERATOR_INTERNAL_KEY))

    def _alias_generator_renames(self, name: str) -> bool:
        """Return whether the parent's alias generator serializes a field of this name under another one."""
        return (generator_name := self._alias_generator_name_from_parent()) is not None and (
            _generate_alias(generator_name, name, self.data_type.python_version) != name
        )

    def _automatic_alias_disabled_for_alias_generator(self) -> bool:
        return self._parent_flag(_NO_ALIAS_INTERNAL_KEY)

    def _missing_sentinel_import(self) -> Import:
        """Import MISSING from pydantic for an explicit 2.14+ target; an unset target keeps the experimental path."""
        if model_target_meets(self.parent, "pydantic.missing_sentinel"):
            return IMPORT_PYDANTIC_MISSING
        return IMPORT_MISSING

    def _has_discriminator_in_data_type(self) -> bool:
        """Check if any nested DataType has a discriminator."""
        if not self.data_type.discriminator and not self.data_type.data_types and self.data_type.dict_key is None:
            return False
        return any(dt.discriminator for dt in self.data_type.all_data_types)

    @property
    @override
    def imports(self) -> tuple[Import, ...]:
        """Get all required imports including AliasChoices and Field for discriminator."""
        base_imports = super().imports
        extra_imports: list[Import] = []
        if self.use_missing_sentinel_default:
            extra_imports.append(self._missing_sentinel_import())
            if not self._use_union_operator and IMPORT_UNION not in base_imports:
                extra_imports.append(IMPORT_UNION)
        if self.is_class_var:
            extra_imports.append(IMPORT_CLASSVAR)
        if self.validation_aliases:
            extra_imports.append(IMPORT_ALIAS_CHOICES)
        if IMPORT_ANNOTATED in base_imports and self._has_discriminator_in_data_type():
            extra_imports.append(IMPORT_FIELD)
        if self.use_pydantic_extra_annotations_dict:
            extra_imports.append(IMPORT_DICT)
        if extra_imports:
            return chain_as_tuple(base_imports, tuple(extra_imports))
        return base_imports


_BYTES_PATTERN_TYPES: frozenset[str | None] = frozenset({"bytes", "StrictBytes"})
_STRING_PATTERN_TYPES: frozenset[str | None] = (
    _applicability.engine_pattern_types(_applicability.PYDANTIC_OUTPUT) - _BYTES_PATTERN_TYPES
)


def _string_pattern_data_type(data_type: DataType, model: DataModel | None) -> DataType | None:
    """Return the string type or ``Literal`` a pattern constrains, looking through unions such as ``str | None``.

    Pydantic compiles the patterns of plain and optional ``bytes`` fields from 2.14 on, ignores them from 2.11
    to 2.13 and rejects them at import before, so ``bytes`` counts only for an explicit target of ``model`` that
    compiles them: an unset target keeps the default engine of older releases. Pydantic 2.12 also compiles the
    pattern of a ``bytes | MISSING`` field, which keeps the default engine like the other unset-target output.
    Otherwise a ``bytes`` member is left out of a union, whose string members Pydantic constrains from 2.2 on:
    2.0 and 2.1 reject the schema, as the stated floor says.
    """
    return _pattern_data_type(data_type, includes_bytes=model_target_meets(model, "pydantic.bytes_pattern"))


def _pattern_data_type(data_type: DataType, *, includes_bytes: bool) -> DataType | None:
    """Return the member a ``pattern`` constrains: a string type or the ``Literal`` of a const or an enum.

    The string types are those the constraint table reads a pattern on (``_applicability.engine_pattern_types``)
    other than the raw ``bytes`` types: ``EmailStr``, ``AnyStr`` and the types that decode base64 text are among
    them, as Pydantic compiles their pattern on every version. Pydantic compiles the pattern of a ``Literal`` as it
    does a string's. With ``includes_bytes`` a ``bytes`` type is one too. The ``Annotated`` form of a ``con*`` call
    in a type alias body (``AnnotatedConstraintDataType``) is the type it constrains.
    """
    if (
        (
            type_name := data_type.constrained_base
            if isinstance(data_type, AnnotatedConstraintDataType)
            else data_type.type
        )
        in _STRING_PATTERN_TYPES
        or getattr(data_type, "annotated_string", False)
        or _is_value_literal(data_type)
        or (type_name in _BYTES_PATTERN_TYPES and includes_bytes)
    ):
        return data_type
    if (
        data_type.type is not None or not (members := data_type.data_types) or data_type._is_container  # noqa: SLF001
    ):
        return None
    string_type: DataType | None = None
    for member in members:
        if _pattern_data_type(member, includes_bytes=includes_bytes) is member:
            string_type = string_type or member
        elif member.type not in _BYTES_PATTERN_TYPES:
            return None
    return string_type


def _model_pattern_engine(pattern: str, data_type: DataType, field: DataModelFieldBase) -> PatternEngine | None:
    """Return what a pattern emitted for ``field`` asks of the config that builds it (row ``model``).

    Lookaround keeps its long-standing textual check. Other syntax counts for the patterns of a string or a
    ``Literal``, which Pydantic compiles, keyed on the target Pydantic version, so the output never depends on the
    installed pydantic-core or on the Python running the generator.
    """
    string_type = _string_pattern_data_type(data_type, field.parent)
    return pattern_engine(pattern, row="model", compiled=string_type is not None)


def _needs_python_regex_engine(pattern: str, data_type: DataType, field: DataModelFieldBase) -> bool:
    """Return whether the syntax of a pattern emitted for ``field`` needs Python's ``re``."""
    return _model_pattern_engine(pattern, data_type, field) == "python"


if TYPE_CHECKING:

    class _ParserSimpleFieldData(TypedDict, total=False):
        name: str | None
        default: object | None
        required: bool
        alias: str | None
        validation_aliases: list[str] | None
        serialization_alias: str | None
        data_type: DataType
        constraints: Constraints | dict[str, object] | None
        strip_default_none: bool
        order_constructor_fields_by_written_defaults: bool
        nullable: bool | None
        extras: dict[str, object] | None
        use_annotated: bool
        use_serialize_as_any: bool
        has_default: bool
        use_field_description: bool
        use_field_description_example: bool
        use_inline_field_description: bool
        const: bool
        original_name: str | None
        use_default_kwarg: bool
        use_missing_sentinel: bool
        target_python_version: PythonVersion | None
        type_has_null: bool | None
        read_only: bool
        write_only: bool
        use_frozen_field: bool
        use_serialization_alias: bool
        use_default_factory_for_optional_nested_models: bool
        use_default_with_required: bool


_PARSER_SIMPLE_FIELD_DEFAULTS = MappingProxyType({
    name: None if field_info.is_required() or field_info.default_factory is not None else field_info.default
    for name, field_info in DataModelField.model_fields.items()
})
_PARSER_SIMPLE_FIELD_HAS_PRIVATE_STATE = DataModelField.__pydantic_post_init__ is not None
_SET_PARSER_FIELD_ATTRIBUTE = object.__setattr__


def _construct_parser_simple_field(**data: Unpack[_ParserSimpleFieldData]) -> DataModelField:
    """Construct a parser-normalized simple field without Pydantic validation."""
    match (
        data.get("constraints"),
        data.get("extras"),
        data.get("const", False),
    ):
        case (None, None | {} as extras, False) if not extras and "data_type" in data:
            pass
        case _:
            return DataModelField(**cast("dict[str, Any]", data))

    # Keep Pydantic's private instance layout in this one compatibility boundary.
    values = _PARSER_SIMPLE_FIELD_DEFAULTS.copy()
    values.update(data)
    values["extras"] = {}
    field = object.__new__(DataModelField)
    _SET_PARSER_FIELD_ATTRIBUTE(field, "__dict__", values)
    _SET_PARSER_FIELD_ATTRIBUTE(field, "__pydantic_fields_set__", set(data))
    _SET_PARSER_FIELD_ATTRIBUTE(field, "__pydantic_extra__", None)
    _SET_PARSER_FIELD_ATTRIBUTE(field, "__pydantic_private__", {} if _PARSER_SIMPLE_FIELD_HAS_PRIVATE_STATE else None)
    if (data_type := field.data_type).reference or data_type.data_types:
        data_type.parent = field
    return field


# The output field owns its validation-free construction contract. Parser code
# resolves this exact-class capability once and keeps calling this function
# directly on the hot path. External subclasses intentionally use normal
# Pydantic construction unless they declare their own constructor.
DataModelField.PARSER_CONSTRUCTOR = _construct_parser_simple_field


def assigns_forward_reference_field(
    model: DataModel, field: DataModelFieldBase, positions: Mapping[str, int], index: int
) -> bool:
    """Return whether a field that names a later model is written by assignment for a target before Pydantic 2.11.

    A deferred annotation naming a model defined later in the module cannot be evaluated while the class is
    created, and older Pydantic then mishandles the ``Field()`` inside ``Annotated``: it drops aliases and
    descriptions, ignores ``validate_default`` or fails on constraints. A self reference can be evaluated, so
    it is not a forward reference here. ``positions`` maps each model path in the module to its order and
    ``index`` is the order of ``model``.
    """
    return (
        field.use_annotated
        and not model_target_meets(model, "pydantic.annotated_forward_ref")
        and any(
            (reference := data_type.reference) is not None and positions.get(reference.path, -1) > index
            for data_type in field.data_type.all_data_types
        )
        and not _only_describes_own_named_type(field)
    )


def _only_describes_own_named_type(field: DataModelFieldBase) -> bool:
    """Return whether a field named like the type it names carries nothing but a description in ``Field()``.

    By assignment the field's value would hide the type, and a description is all that older Pydantic may lose
    from ``Annotated``.
    """
    if not any(
        (reference := data_type.reference) is not None and _written_name(data_type, reference) == field.name
        for data_type in field.data_type.all_data_types
    ):
        return False
    call = field._render_plan().call  # noqa: SLF001
    return call is None or (call.factory is None and all(argument.name == "description" for argument in call.kwargs))


def _field_patterns(fields: Iterable[DataModelFieldBase]) -> Iterator[tuple[str, DataType, DataModelFieldBase, bool]]:
    """Yield each pattern the fields write, with its type and field, and whether ``re.compile`` wraps it."""
    for field in fields:
        if pattern := isinstance(field.constraints, Constraints) and field.constraints.pattern:
            compiled = isinstance(field.extras.get(_COMPILED_PATTERN_KEY), PythonRuntimeExpression)
            yield pattern, field.data_type, field, compiled
        for data_type in field.data_type.all_data_types:
            if isinstance(pattern := (data_type.kwargs or {}).get("pattern"), PythonRuntimeExpression):
                yield str(pattern), data_type, field, True
            elif pattern:
                yield pattern, data_type, field, False


def _pattern_engines(
    fields: Iterable[DataModelFieldBase],
    *,
    include_compiled: bool = True,
    counts: Callable[[str], bool] = _counts_every_pattern,
) -> Iterator[tuple[str, PatternEngine]]:
    """Yield each pattern of the fields with what it asks of the config that builds it (``_model_pattern_engine``).

    A pattern ``re.compile`` wraps runs on Python's ``re`` whatever the config: it needs that engine where
    ``include_compiled`` counts it and its syntax needs it, and asks for nothing otherwise. ``counts`` selects the
    string patterns that ask for an engine; another one still runs on the engine of the config, so one that the
    default engine runs as written counts.
    """
    for pattern, data_type, field, compiled in _field_patterns(fields):
        if compiled:
            if include_compiled and counts(pattern) and _needs_python_regex_engine(pattern, data_type, field):
                yield pattern, "python"
        elif (engine := _counted_engine(_model_pattern_engine(pattern, data_type, field), pattern, counts)) is not None:
            yield pattern, engine


def _followed_fields(fields: list[DataModelFieldBase]) -> Iterator[list[DataModelFieldBase]]:
    """Yield the fields, then those of every model they reach (``_pattern_models_followed``), each read once."""
    yield fields
    for model in reach(_pattern_models_followed(fields), lambda model: _pattern_models_followed(model.fields), key=id):
        yield model.fields


def has_lookaround_pattern(
    fields: list[DataModelFieldBase],
    *,
    follow_references: bool = False,
    include_compiled: bool = True,
    counts: Callable[[str], bool] = _counts_every_pattern,
    surrogates: bool = True,
) -> bool:
    """Check if the patterns of the fields select pydantic's ``python-re`` regex engine (``selects_python_engine``).

    This covers lookaround assertions and, for string and ``Literal`` patterns pydantic compiles, any other syntax
    pydantic-core's Rust engine rejects, such as backreferences, and, with ``surrogates``, a lone surrogate where no
    other pattern of the fields runs on the default engine as written.

    When ``follow_references`` is True, also inspect the patterns of the models the fields reach
    (``_pattern_models_followed``) -- needed for Pydantic v2 dataclasses, where alias patterns are compiled with the
    consuming dataclass's config rather than their own.
    When ``include_compiled`` is False, patterns the parser already wrapped in ``re.compile`` are skipped, and
    ``counts`` selects the patterns that count.
    """
    scopes: Iterable[list[DataModelFieldBase]] = _followed_fields(fields) if follow_references else (fields,)
    return selects_python_engine(
        (
            engine
            for scope in scopes
            for _, engine in _pattern_engines(
                scope, include_compiled=include_compiled, counts=_counts_every_pattern if follow_references else counts
            )
        ),
        surrogates=surrogates,
    )


def _pattern_models_followed(fields: Iterable[DataModelFieldBase]) -> Iterator[DataModel]:
    """Yield the models whose patterns a Pydantic dataclass reads through the references of ``fields``.

    Row ``legacy-dataclass-follows-every-reference``: every model with fields that a field's type names
    (``_field_references``), whatever config of its own the model has, where the models that use a type alias read
    only the aliases keeping string patterns (``models_reaching_string_pattern_aliases``). A site that earlier
    releases wrote as a model without fields (``FIELDLESS_SITE_KEY``) is not followed where the target compiles the
    patterns of type aliases: its patterns then need no engine of the model that uses it, whose other patterns keep
    the engine they had.
    """
    for reference in _field_references(fields):
        source = reference.source
        if is_fieldless_site(reference) and model_target_meets(cast("DataModel", source), "pydantic.compiled_pattern"):
            continue
        if getattr(source, "fields", None) is not None:
            yield cast("DataModel", source)


def _field_references(fields: Iterable[DataModelFieldBase]) -> Iterator[Reference]:
    """Yield the references that the types of ``fields`` name."""
    for field in fields:
        for data_type in field.data_type.all_data_types:
            if (reference := data_type.reference) is not None:
                yield reference


def _model_references(model: DataModel, base_classes: Iterable[BaseClassDataType]) -> Iterator[Reference]:
    """Yield the references that the fields of ``model`` and the given base classes name."""
    yield from _field_references(model.fields)
    for base_class in base_classes:
        if (reference := base_class.reference) is not None:
            yield reference


def _internal_runtime_validation(model: DataModel) -> SchemaRuntimeValidation | None:
    """Return the parser-owned runtime validation of a model, or None when it has none or the validation has no rule."""
    validation = model._internal_template_data.get("schema_runtime_validation")  # noqa: SLF001
    return validation if _is_internal_schema_runtime_validation(validation) and validation else None


def _inherits_schema_runtime_validation_base(model: DataModel, local_model_ids: Collection[int]) -> bool:
    """Return whether a model already inherits the generated runtime validation base.

    It does where one of the generated models it inherits from through models of ``local_model_ids``, however distant,
    has runtime validation (``_internal_runtime_validation``).
    """
    walk = _base_walk(
        (model,), bases_of=lambda current: [base for base in _find_base_classes(current) if id(base) in local_model_ids]
    )
    return any(
        _internal_runtime_validation(walk.node_of[key]) for key in walk.graph.reached(walk.starts[0], (EdgeKind.BASE,))
    )


_HELPER_LAYER_NAMES: tuple[str, ...] = ("Core", "PropertyCount", "ConstantTypes", "ConditionalBounds", "Contains")
"""The helper layers, innermost first: the core helper template, then the Python-rendered layers."""
_HELPER_PREFIXES: dict[tuple[bool, ...], str] = {
    (True, False): "Core",
    (False, True): "PropertyCount",
    (True, True): "Combined",
    (False, False): "",
}


def _helper_capability_sets() -> dict[tuple[bool, ...], str]:
    """Return each non-empty set of helper layers with the suffix of its helper's name, in rendering order.

    A set is numbered by its layers as bits, the core the lowest, so a helper follows the helper it extends; the
    name is that of its core and property-count part, then the names of its later layers.
    """
    count = len(_HELPER_LAYER_NAMES)
    sets: dict[tuple[bool, ...], str] = {}
    for number in range(1, 2**count):
        capabilities = tuple(bool(number >> index & 1) for index in range(count))
        sets[capabilities] = _HELPER_PREFIXES[capabilities[:2]] + "".join(
            name for name, capability in zip(_HELPER_LAYER_NAMES[2:], capabilities[2:], strict=True) if capability
        )
    return sets


_HELPER_SUFFIXES: dict[tuple[bool, ...], str] = _helper_capability_sets()
_CORE_CAPABILITIES: tuple[bool, ...] = (True, *(False for _ in _HELPER_LAYER_NAMES[1:]))
_PRIMARY_HELPERS: tuple[tuple[bool, ...], ...] = tuple(
    dict.fromkeys((
        *(
            (*capabilities, *(False for _ in _HELPER_LAYER_NAMES[3:]))
            for capabilities in (
                (True, True, True),
                (True, True, False),
                (False, True, True),
                (False, True, False),
                (True, False, True),
                (False, False, True),
                (True, False, False),
            )
        ),
        *_HELPER_SUFFIXES,
    ))
)
"""The helper sets in the order that gives the plain base class name to the first one a module needs."""


def _helper_inner_capabilities(capabilities: tuple[bool, ...]) -> tuple[bool, ...]:
    """Return the capabilities of the helper the outermost layer of ``capabilities`` is rendered on.

    The layers are those of ``_HELPER_LAYER_NAMES``, innermost first.
    """
    outermost = max(index for index, capability in enumerate(capabilities) if capability)
    return tuple(capability and index != outermost for index, capability in enumerate(capabilities))


def _python_layer_rules(runtime_validation: SchemaRuntimeValidation) -> tuple[bool, ...]:
    """Return whether a model has rules of each Python-rendered layer (``_HELPER_LAYER_NAMES`` after the core)."""
    return (
        bool(runtime_validation.property_count),
        bool(runtime_validation.constant_types),
        bool(runtime_validation.conditional_bounds),
        bool(runtime_validation.contains),
    )


def _helper_template_facts(runtime_validations: list[SchemaRuntimeValidation]) -> dict[str, bool]:
    """Return what the schema validator helpers write for the rules of a module's models; imports read it too."""
    facts = {
        "has_pattern_properties": any(
            runtime_validation.pattern_properties for runtime_validation in runtime_validations
        ),
        "has_required_groups": any(
            rule.keyword != "not"
            for runtime_validation in runtime_validations
            for rule in runtime_validation.required_groups
        ),
        "has_not_required_groups": any(
            rule.keyword == "not"
            for runtime_validation in runtime_validations
            for rule in runtime_validation.required_groups
        ),
        "has_conditional_required": any(
            runtime_validation.conditional_required for runtime_validation in runtime_validations
        ),
        "has_conditional_presence": any(
            not expected
            for runtime_validation in runtime_validations
            for rule in runtime_validation.conditional_required
            for _names, expected in rule.condition
        ),
        "has_json_value": any(
            runtime_validation.conditional_required
            or runtime_validation.conditional_bounds
            or runtime_validation.contains
            for runtime_validation in runtime_validations
        ),
        "has_conditional_json_equality": any(
            runtime_validation.conditional_bounds
            or any(rule.uses_json_equality for rule in runtime_validation.contains)
            or any(
                conditional_value_uses_json_equality(value)
                for rule in runtime_validation.conditional_required
                for _names, expected in rule.condition
                for value in expected
            )
            for runtime_validation in runtime_validations
        ),
        "has_unique_items": any(runtime_validation.unique_items for runtime_validation in runtime_validations),
        "has_unique_items_regex_paths": any(
            unique_items_path_uses_regex(rule.path)
            for runtime_validation in runtime_validations
            for rule in runtime_validation.unique_items
        ),
    }
    if any(
        isinstance(rule, IndependentDeclaredPatternPropertiesRule)
        or (
            isinstance(rule, IndependentModelPatternPropertiesRule)
            and len({data_type.reference.path for _, data_type in rule.pattern_properties if data_type.reference}) > 1
        )
        for validation in runtime_validations
        for rule in validation.pattern_properties
    ):
        facts["has_pattern_property_intersections"] = True
    if any(rule.python_regex_types for validation in runtime_validations for rule in validation.pattern_properties):
        facts["has_python_regex_types"] = True
    return facts


def _alias_pattern_engines(alias: DataModel) -> Iterator[tuple[str, PatternEngine]]:
    """Yield what the string patterns of a type alias ask of the config of the models that use it.

    A type alias has no config, and keeps the patterns ``alias_keeps_string_pattern`` names strings.
    """
    return _pattern_engines(alias.fields, include_compiled=False, counts=alias_pattern_counter(alias))


def _reaching_aliases(models: list[DataModel], aliases: Iterable[DataModel]) -> set[int]:
    """Return the ids of ``aliases`` and of the models that reach one of them through fields and base classes."""
    return reaching(
        map(id, aliases),
        (
            (id(model), (id(reference.source) for reference in _model_references(model, model.base_classes)))
            for model in models
        ),
    )


def models_reaching_string_pattern_aliases(models: Iterable[DataModel]) -> set[int]:
    """Return the ids of the models that reach a type alias keeping a string pattern whose syntax needs ``python-re``.

    Pydantic builds an alias that is named more than once with the config of the outermost model, and a model
    with the fields of its bases, so a model reaches an alias through any chain of fields and base classes. A lone
    surrogate is left to ``_select_surrogate_regex_engines``.
    """
    from datamodel_code_generator.model.type_alias import TypeAliasBase  # noqa: PLC0415

    models = list(models)
    pending = [
        model
        for model in models
        if isinstance(model, TypeAliasBase)
        and selects_python_engine((engine for _, engine in _alias_pattern_engines(model)), surrogates=False)
    ]
    return _reaching_aliases(models, pending) if pending else set()


def _holds_lone_surrogate(models: Iterable[DataModel]) -> bool:
    """Return whether a string pattern of the models holds a lone surrogate (``holds_lone_surrogate``)."""
    return any(
        not compiled and holds_lone_surrogate(pattern)
        for model in models
        for pattern, _, _, compiled in _field_patterns(model.fields)
    )


def _built_bases(model: DataModel) -> Iterator[DataModel]:
    """Yield the model, then every base it inherits, whose fields Pydantic builds again with the config it applies."""
    walk = _base_walk((model,))
    for key in walk.graph.preorder(walk.starts[0], EdgeKind.BASE):
        yield walk.node_of[key]


def _config_scopes(model: DataModel) -> Iterator[tuple[DataModel, bool]]:
    """Yield the models whose patterns the config a model applies builds, each with whether it is a type alias.

    The model and the bases it inherits (``_built_bases``), and the type aliases it reaches through any chain of
    fields and base classes (``models_reaching_string_pattern_aliases``), whose string patterns it builds.
    """
    from datamodel_code_generator.model.type_alias import TypeAliasBase  # noqa: PLC0415

    for current in _built_bases(model):
        yield current, False
    reached = reach(
        (model,),
        lambda current: [
            source
            for reference in _model_references(current, current.base_classes)
            if isinstance(source := reference.source, DataModel)
        ],
        key=id,
    )
    for alias in reached:
        if isinstance(alias, TypeAliasBase):
            yield alias, True


def _config_pattern_engines(model: DataModel) -> Iterator[tuple[str, PatternEngine]]:
    """Yield the patterns that the config a model applies builds (``_config_scopes``), with what each asks of it."""
    for current, alias in _config_scopes(model):
        yield from _alias_pattern_engines(current) if alias else _pattern_engines(current.fields)


def _uses_reached_alias(model: _PydanticV2Model, reached: Collection[int]) -> bool:
    """Return whether a model names a model of ``reached`` in its fields or ``_bases_without_inherited_config``."""
    return any(
        id(reference.source) in reached
        for reference in _model_references(model, model._bases_without_inherited_config())  # noqa: SLF001
    )


def _select_python_engine(model: _PydanticV2Model, *, through_alias: bool) -> None:
    """Select Python's regex engine in a model; for a type alias it reaches, its bases name no such alias too."""
    if through_alias:
        model._keep_string_pattern_aliases_out_of_bases()  # noqa: SLF001
    model._select_python_regex_engine()  # noqa: SLF001


def _select_surrogate_regex_engines(models: list[DataModel], alias_users: type[DataModel]) -> None:
    """Select ``python-re`` for a lone surrogate where no pattern the config builds runs on the default engine (#6320).

    The default engine reads a lone surrogate as U+FFFD, so a model wants Python's engine for one in a pattern of its
    fields (``_surrogate_pattern_scopes``) or of a type alias it reaches as ``finalize_type_alias_users`` reads it,
    and the patterns of the config decide (``selects_python_engine``): those it builds (``_config_pattern_engines``)
    and those of every model that takes the config (``_takes_config_of``). Where one of them runs on the default
    engine as written, switching would change how Python reads it or refuse it at import, so the model keeps the
    default engine and warns.
    """
    from datamodel_code_generator.model.type_alias import TypeAliasBase  # noqa: PLC0415

    if not _holds_lone_surrogate(models):
        return
    holders = [model for model in models if isinstance(model, _PydanticV2Model)]
    surrogate_aliases = [
        model
        for model in models
        if isinstance(model, TypeAliasBase)
        and any(engine == "surrogate" for _, engine in _alias_pattern_engines(model))
    ]
    reached = _reaching_aliases(models, surrogate_aliases) if surrogate_aliases else set()
    takers: defaultdict[int, list[_PydanticV2Model]] = defaultdict(list)
    for model in holders:
        for base in _find_base_classes(model):
            if model._takes_config_of(base):  # noqa: SLF001
                takers[id(base)].append(model)
    for model in holders:
        through_alias = isinstance(model, alias_users) and _uses_reached_alias(model, reached)
        own = [
            pattern
            for scope in model._surrogate_pattern_scopes()  # noqa: SLF001
            for pattern, engine in _pattern_engines(scope)
            if engine == "surrogate"
        ]
        if not (own or through_alias):
            continue
        engines = list(_config_pattern_engines(model))
        if any(engine == "python" for _, engine in engines):
            continue
        family = reach((model,), lambda current: takers[id(current)], key=id)
        blocker = next(
            (
                pattern
                for member in family
                for pattern, engine in (engines if member is model else _config_pattern_engines(member))
                if engine == "default"
            ),
            None,
        )
        if blocker is None:
            _select_python_engine(model, through_alias=through_alias)
            continue
        surrogate = next(chain(own, (pattern for pattern, engine in engines if engine == "surrogate")))
        warn(
            f"Pydantic's default regex engine reads the lone surrogate in the pattern {stable_repr(surrogate)} as "
            f"U+FFFD, so {model.class_name} accepts text the schema rejects: its config keeps that engine for the "
            f"pattern {stable_repr(blocker)}, which Python's engine reads otherwise.",
            stacklevel=2,
        )


def _explicit_alias_conflicts_with_pydantic(field: DataModelFieldBase, name: str) -> bool:
    """Respect configured namespaces for actual attribute collisions, retaining warning-only aliases."""
    if name == "model_config" or name.startswith("_"):
        return True
    if name not in PYDANTIC_BASE_MODEL_ATTRIBUTES:
        return False
    model = cast("DataModel", field.parent)
    namespaces = (
        ("model_validate", "model_dump") if model_target_meets(model, "pydantic.protected_namespaces") else ("model_",)
    )

    def conflicts(models: Iterable[tuple[DataModel, bool]]) -> bool:
        """Read the namespaces from the first of ``models``, each with whether it has generated bases, that sets them.

        A model with no generated bases and an external base class first leaves them unknown.
        """
        for current, has_bases in models:
            if (
                configured := getattr(current.extra_template_data.get("config"), "protected_namespaces", None)
            ) is not None:
                return name.startswith(configured)
            if not has_bases and current.custom_base_class and current.custom_base_class != "pydantic.BaseModel":
                return False
        return name.startswith(namespaces)

    walk = _base_walk((model,))
    graph = walk.graph
    return conflicts(
        (walk.node_of[key], bool(graph.ordered_targets(key, EdgeKind.BASE)))
        for key in graph.preorder(walk.starts[0], EdgeKind.BASE)
    )


def _validated_value_hashes(field: DataModelFieldBase) -> bool:
    """Read the facts of a field of this exact class; a subclass may validate otherwise, and keeps no builtin hash."""
    return type(field) is DataModelField and field_value_hashes(
        default_factory=field.has_default_factory,
        default=field.default,
        defaulted=field.default is not UNDEFINED,
        values=map(_value_hash_facts, field.data_type.all_data_types),
    )


def _value_hash_facts(data_type: DataType) -> ValueHashFacts:
    """Read the facts of a data type that tell whether the values Pydantic validates for it hash as builtin values."""
    return ValueHashFacts(
        container=data_type in UNHASHABLE_CONTAINERS,
        opaque=bool(
            data_type.reference
            or data_type.data_types
            or data_type.is_custom_type
            or data_type.is_func
            or data_type.kwargs
        ),
        python_type=python_type.expression if (python_type := data_type.python_type) else None,
        literals=tuple(data_type.literals or ()),
        builtin_name=None if data_type.import_ else data_type.type,
    )


class _PydanticV2Model(DataModel):
    """The config items, constructs, type alias users and forward references of the Pydantic BaseModel and dataclass."""

    WRITES_CLOSED_ADDITIONAL_PROPERTIES: ClassVar[bool] = True
    _CONFIG_ITEMS_KEY: ClassVar[str] = _CONFIG_ITEMS_TEMPLATE_DATA_KEY
    _CONFIG_AS_CONFIG_DICT: ClassVar[bool] = True
    _OVERWRITES_REGEX_ENGINE: ClassVar[bool] = False
    _FOLLOWS_PATTERN_REFERENCES: ClassVar[bool] = False

    @override
    def assigns_forward_reference_field(
        self, field: DataModelFieldBase, positions: Mapping[str, int], index: int
    ) -> bool:
        """Leave ``Annotated`` for a field that names a later model when the target predates Pydantic 2.11."""
        return assigns_forward_reference_field(self, field, positions, index)

    @classmethod
    @override
    def finalize_type_alias_users(cls, models: list[DataModel]) -> None:
        """Select Python's regex engine where the patterns ask for it, once every model and its patterns are final.

        A BaseModel whose own patterns need the engine selects it (``_own_patterns_select_python``); a config that
        already names an engine is kept. So does a model that reaches a string pattern type alias: the references of
        its fields count, and those of the bases whose config it does not take (``_bases_without_inherited_config``). A
        lone surrogate selects it where the config allows it (``_select_surrogate_regex_engines``), and so does a
        dataclass for the patterns of the bases its config builds (``_select_engines_for_bases``). Every model whose
        patterns a config selecting it builds (``_config_scopes``), a dataclass's once it selected its engine, is
        marked once (``PYTHON_ENGINE_BUILDS_KEY``), so its fields leave out a pattern Python's ``re`` rejects (#6593).
        A config that selects the engine only for the patterns of its bases marks the models that apply it alone: they
        redeclare each inherited field whose pattern the engine cannot build (``_redeclare_rejected_patterns``), and the
        bases keep their output (#7452).
        """
        for model in models:
            if isinstance(model, BaseModel) and model._own_patterns_select_python():  # noqa: SLF001
                model._select_python_regex_engine()  # noqa: SLF001
        if reached := models_reaching_string_pattern_aliases(models):
            for model in models:
                if isinstance(model, cls) and _uses_reached_alias(model, reached):
                    _select_python_engine(model, through_alias=True)
        _select_surrogate_regex_engines(models, cls)
        for_bases: set[int] = set()
        cls._select_engines_for_bases(models, for_bases)
        built: dict[int, DataModel] = {}
        for model in models:
            if isinstance(model, _PydanticV2Model) and model._builds_patterns_with_python_engine():  # noqa: SLF001
                if id(model) in for_bases:
                    built[id(model)] = model
                else:
                    built.update((id(current), current) for current, _ in _config_scopes(model))
        for current in built.values():
            current._set_internal_template_data(PYTHON_ENGINE_BUILDS_KEY, value=True)  # noqa: SLF001
            for field in current.fields:
                field.invalidate_semantic_caches()
        cls._redeclare_rejected_patterns(models, for_bases, built)

    @classmethod
    def _select_engines_for_bases(cls, models: list[DataModel], selected: set[int]) -> None:
        """Select ``python-re`` where a config builds a base's pattern that needs it; ``selected`` gains its users.

        None does: a model merges the config of every base, which selects the engine for its own patterns.
        """

    @classmethod
    def _redeclare_rejected_patterns(
        cls, models: list[DataModel], selected: set[int], built: Mapping[int, DataModel]
    ) -> None:
        """Redeclare in each model of ``selected`` the inherited fields whose pattern its engine cannot build."""

    @override
    def _builds_patterns_with_python_engine(self) -> bool:
        """Return whether the config the model applies selects ``python-re``."""
        return self._applied_config_items().get("regex_engine") == '"python-re"'

    def reads_json_bytes_as_text(self) -> bool:
        """Return whether validating the model reads bytes from JSON text as the text's UTF-8 bytes.

        It does where the config the model applies is known, so ``val_json_bytes`` keeps its default, UTF-8.

        The generator writes no ``val_json_bytes``; a configured base class it does not read may set it.
        """
        return self.applied_config()[1] is None

    def applied_config(self) -> tuple[dict[str, str], frozenset[str] | None]:
        """Return the config items Pydantic applies to the model, the bases included, and which of them are known.

        A configured base class the generator does not read may set any item; the second value names the items set
        after the last such base, or is None where the model has none.
        """
        return self._applied_config()

    def _applied_config_items(self) -> dict[str, str]:
        """Return the config items Pydantic applies to the model that the generator reads."""
        return self._applied_config()[0]

    @abstractmethod
    def _applied_config(self, visiting: frozenset[int] = frozenset()) -> tuple[dict[str, str], frozenset[str] | None]:
        """Return the config items Pydantic applies to the model and which of them are known (``applied_config``)."""

    def _own_patterns_select_python(self) -> bool:
        """Return whether the model's own patterns select ``python-re`` (``has_lookaround_pattern``).

        The rule of ``_needs_model_regex_engine`` (a lone surrogate is left to ``_select_surrogate_regex_engines``).
        A dataclass's own are also those of every model its fields reach (``_FOLLOWS_PATTERN_REFERENCES``, row
        ``legacy-dataclass-follows-every-reference``).
        """
        return has_lookaround_pattern(self.fields, follow_references=self._FOLLOWS_PATTERN_REFERENCES, surrogates=False)

    def _surrogate_pattern_scopes(self) -> Iterator[list[DataModelFieldBase]]:
        """Yield the fields whose lone surrogates ask for the model's engine: those its other patterns count in."""
        return _followed_fields(self.fields) if self._FOLLOWS_PATTERN_REFERENCES else iter((self.fields,))

    def _takes_config_of(self, base: DataModel) -> bool:  # noqa: ARG002, PLR6301
        """Return whether the model applies the config of a base it names: a model merges the config of every base."""
        return True

    def _bases_without_inherited_config(self) -> Sequence[BaseClassDataType]:  # noqa: PLR6301
        """Return the bases whose config the model does not take: none, as a model takes the config of every base."""
        return ()

    def _keep_string_pattern_aliases_out_of_bases(self) -> None:
        """Keep the bases free of types Pydantic builds before the config applies; a model names none there."""

    def _select_python_regex_engine(self, applied_config: object = None) -> None:
        """Set ``regex_engine`` in the config the model renders, unless the config already names an engine.

        Row ``legacy-dataclass-overwrites-engine`` (``_OVERWRITES_REGEX_ENGINE``): a dataclass replaces an engine its
        config names (#5637). The model gets a config of its own: the one in the template data may be shared with
        every model of the same name (``--extra-template-data``). It reads the items of that config, so a config that
        is no mapping adds none. A config the model took from a base (``applied_config``) gives its items first, as
        Pydantic stops applying it once the model has a config of its own.
        """
        config_parameters = {
            **dict(_config_dict_items(applied_config)),
            **dict(_config_dict_items(self.extra_template_data.get("config"))),
        }
        if "regex_engine" in config_parameters and not self._OVERWRITES_REGEX_ENGINE:
            return
        config_parameters["regex_engine"] = '"python-re"'
        self._store_config(config_parameters)
        self.invalidate_render_caches()

    def _store_config(self, config_parameters: dict[str, Any]) -> None:
        """Store the config the model renders with the items the template writes, and import ``ConfigDict``.

        A model validates it as a ``ConfigDict``; a dataclass keeps the mapping (``_CONFIG_AS_CONFIG_DICT``), so the
        keys ``ConfigDict`` leaves out stay.
        """
        from datamodel_code_generator.model.pydantic_v2 import ConfigDict  # noqa: PLC0415

        validates = self._CONFIG_AS_CONFIG_DICT
        if validates and (left_out := [key for key in config_parameters if key not in ConfigDict.model_fields]):
            warn(
                f"The config of {self.name} sets {', '.join(map(message_repr, left_out))}, which this model leaves out "
                f"of model_config. It writes only {', '.join(ConfigDict.model_fields)}. "
                "pydantic_v2.dataclass writes every key as given.",
                stacklevel=2,
            )
        self.extra_template_data["config"] = (
            ConfigDict.model_validate(config_parameters) if validates else config_parameters
        )
        self._set_internal_template_data(self._CONFIG_ITEMS_KEY, self._config_items())
        if IMPORT_CONFIG_DICT not in self._additional_imports:
            self._additional_imports.append(IMPORT_CONFIG_DICT)

    def _config_items(self) -> list[tuple[str, str]]:
        """Return the ``ConfigDict`` items of the model's config, which the built-in templates write for the target."""
        return _template_config_items(self.extra_template_data, self.target_python_version)

    def _rendered_config_items(self) -> list[tuple[str, str]]:
        """Return the ``ConfigDict`` items the built-in template writes; imports and alias decisions read them too.

        In a parse they are the items stored with the config (``_store_config``); outside one a caller may change the
        config after ``__init__``, so they are read from it.
        """
        if RENDER_READY.get() is None or (stored := self._internal_template_data.get(self._CONFIG_ITEMS_KEY)) is None:
            return self._config_items()
        return stored

    @override
    def _pydantic_constructs(self) -> Iterator[tuple[PydanticConstruct, str | None]]:
        """Also yield the rows of the keys that the rendered config writes."""
        yield from super()._pydantic_constructs()
        yield from _config_constructs(self._rendered_config_items())


class BaseModel(_PydanticV2Model, BaseModelBase):
    """Pydantic v2 BaseModel with ConfigDict and pattern-based regex_engine support."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "pydantic_v2/BaseModel.jinja2"
    SCHEMA_RUNTIME_VALIDATION_HELPERS_TEMPLATE_FILE_PATH: ClassVar[str] = (
        "pydantic_v2/schema_runtime_validation_helpers.jinja2"
    )
    SCHEMA_RUNTIME_VALIDATION_BASE_CLASS_NAME: ClassVar[str] = "_JsonSchemaRuntimeValidationBase"
    _SCHEMA_RUNTIME_VALIDATION_MODULE_PLAN_CACHE_KEY: ClassVar[str] = "_schema_runtime_validation_module_plan"
    _CORE_VALIDATION_BASE_MARKER: ClassVar[str] = "_core_validation_base"
    _PROPERTY_COUNT_VALIDATION_BASE_MARKER: ClassVar[str] = "_property_count_validation_base"
    _CONSTANT_TYPES_VALIDATION_BASE_MARKER: ClassVar[str] = "_constant_types_validation_base"
    _CONDITIONAL_BOUNDS_VALIDATION_BASE_MARKER: ClassVar[str] = "_conditional_bounds_validation_base"
    _CONTAINS_VALIDATION_BASE_MARKER: ClassVar[str] = "_contains_validation_base"
    BASE_CLASS: ClassVar[str] = "pydantic.BaseModel"
    BASE_CLASS_NAME: ClassVar[str] = "BaseModel"
    _READ_BY_PYDANTIC: ClassVar[bool] = True
    _CHECKS_CONSTRAINTS: ClassVar[bool] = True
    _FIELD_VALUE_HASHES = staticmethod(_validated_value_hashes)
    BASE_CLASS_ALIAS: ClassVar[str] = "_BaseModel"
    SUPPORTS_TREE_SCOPE_REUSE_MODEL_INHERITANCE: ClassVar[bool] = True
    FIELD_NAME_MODEL_TYPE: ClassVar[ModelType] = ModelType.PYDANTIC
    FIELD_NAME_RESOLVER_CLASS: ClassVar[type[FieldNameResolver]] = PydanticFieldNameResolver
    EXPLICIT_ALIAS_CONFLICT_CHECKER = staticmethod(_explicit_alias_conflicts_with_pydantic)
    SUPPORTS_DISCRIMINATOR: ClassVar[bool] = True
    SUPPORTS_INHERITED_DISCRIMINATOR_ENUM: ClassVar[bool] = True
    SUPPORTS_FIELD_RENAMING: ClassVar[bool] = True
    SUPPORTS_FIELD_TYPE_RENAMING: ClassVar[bool] = True
    NORMALIZES_FIELD_NAMES: ClassVar[bool] = True
    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = True
    SUPPORTS_SCHEMA_RUNTIME_VALIDATION: ClassVar[bool] = True
    PLAIN_PATTERN_ROOT_TYPES = staticmethod(_get_plain_pattern_root_types)
    PLAIN_PATTERN_ROOT_CHECKER = staticmethod(_supports_plain_pattern_root_annotations)
    SCHEMA_RUNTIME_VALIDATION_ROOT_MODEL = staticmethod(_get_schema_runtime_validation_root_model)
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = _ANNOTATED_CONSTRAINTS_CONTEXT
    SUPPORTS_CONFIG_EXTRA: ClassVar[bool] = True
    SUPPORTS_ARBITRARY_TYPES_ALLOWED: ClassVar[bool] = True
    CUSTOM_TEMPLATE_ADAPTER = staticmethod(_adapt_legacy_pydantic_extra_template)
    _INCLUDE_DICT_KEY_REFERENCE_CLASSES = staticmethod(_includes_dict_key_reference_classes)
    _TYPED_EXTRA_DICT_KEY_CAPABILITY = staticmethod(_supports_pydantic_typed_extra_dict_key)
    TYPED_EXTRA_FIELD_NAME: ClassVar[str | None] = "__pydantic_extra__"
    TYPED_EXTRA_PLAIN_ANNOTATION_TEMPLATE_DATA_KEY: ClassVar[str | None] = "pydantic_extra_plain_annotation"
    # In Pydantic 2.11+, populate_by_name is deprecated in favor of validate_by_name + validate_by_alias
    # Default to V2 compatible (populate_by_name) unless target_pydantic_version is specified
    _CONFIG_ATTRIBUTES_V2: ClassVar[list[ConfigAttribute]] = config_attributes(by_name="populate_by_name", frozen=True)
    _CONFIG_ATTRIBUTES_V2_11: ClassVar[list[ConfigAttribute]] = config_attributes(
        by_name="validate_by_name", frozen=True
    )

    @classmethod
    def _uses_builtin_hash_implementation(cls) -> bool:
        """Return whether hash analysis can trust this model implementation."""
        return cls is BaseModel

    @override
    def _hash_class_facts(self) -> HashClassFacts:
        """Read ``frozen`` from the class's ``ConfigDict``; the builtin class hashes frozen instances by value."""
        from datamodel_code_generator.model.pydantic_v2 import ConfigDict  # noqa: PLC0415

        config = self.extra_template_data.get("config")
        return replace(
            super()._hash_class_facts(),
            frozen=config.frozen if isinstance(config, ConfigDict) else None,
            builtin_hash=self._uses_builtin_hash_implementation(),
        )

    @classmethod
    @override
    def resolve_nested_constrained_model_type(
        cls,
        configured_root_model_type: type[DataModel],
    ) -> type[DataModel]:
        """Use a runtime-compatible alias for nested constrained values."""
        from datamodel_code_generator.model.type_alias import TypeAliasTypeBackport  # noqa: PLC0415

        return TypeAliasTypeBackport

    @classmethod
    @override
    def resolve_type_alias_model_type(
        cls,
        configured_root_model_type: type[DataModel],
        target_python_version: PythonVersion,
    ) -> type[DataModel]:
        """Replace a RootModel wrapper with the alias form that --use-type-alias selects."""
        if not configured_root_model_type.IS_ROOT_MODEL:
            return configured_root_model_type

        return get_data_model_types(
            DataModelType.PydanticV2BaseModel, target_python_version, use_type_alias=True
        ).root_model

    @classmethod
    @override
    def prepare_module_code(cls, models: list[DataModel]) -> None:
        """Plan shared schema validation helpers before imports are collected."""
        cls._get_schema_runtime_validation_module_plan(models, scan_later_models=True)

    @classmethod
    @override
    def render_module_code(cls, models: list[DataModel]) -> str:
        """Render shared schema runtime validation helpers for the module once."""
        if (module_plan := cls._get_schema_runtime_validation_module_plan(models)) is None:
            return ""

        _runtime_models, runtime_validations = cls._get_schema_runtime_validation_models(models)
        if not module_plan.plans_capabilities:
            return cls._render_core_schema_runtime_validation_helpers(
                module_plan.base_class_name,
                runtime_validations,
                models,
            )
        helper_base_class_names = dict(module_plan.helper_base_class_names)
        if not helper_base_class_names:
            return ""
        return cls._render_capability_validation_helpers(
            module_plan.base_class_name,
            helper_base_class_names,
            runtime_validations,
            models,
        )

    @classmethod
    def _get_schema_runtime_validation_module_plan(
        cls,
        models: list[DataModel],
        *,
        scan_later_models: bool = False,
    ) -> SchemaRuntimeValidationModulePlan | None:
        """Build one compact helper plan, reusing it for the final renderer."""
        if not models or (
            (module_plan := models[0].__dict__.get(cls._SCHEMA_RUNTIME_VALIDATION_MODULE_PLAN_CACHE_KEY)) is None
            and not models[0].extra_template_data.get("schema_runtime_validation_enabled")
            and (
                not scan_later_models
                or not any(model.extra_template_data.get("schema_runtime_validation_enabled") for model in models[1:])
            )
        ):
            return None
        if module_plan is not None:
            return cast("SchemaRuntimeValidationModulePlan", module_plan)

        from datamodel_code_generator.model.pydantic_v2._schema_runtime_validation import (  # noqa: PLC0415
            SchemaRuntimeValidationModulePlan,
            plan_schema_runtime_validation_bases,
        )

        runtime_models, runtime_validations = cls._get_schema_runtime_validation_models(models)
        if not runtime_models:
            return None

        first_model = runtime_models[0]
        base_class_name = (
            _normalized_safe_python_name(
                first_model.extra_template_data.get("schema_validator_base_class_name"),
                first_model.target_python_version,
            )
            or cls.SCHEMA_RUNTIME_VALIDATION_BASE_CLASS_NAME
        )
        runtime_validation_by_model = {
            id(model): runtime_validations[index] for index, model in enumerate(runtime_models)
        }
        plans_capabilities = any(
            any(_python_layer_rules(runtime_validation)) for runtime_validation in runtime_validations
        )
        uses_generated_generic_base_class = cls._has_generated_generic_base_class(models)
        local_helpers: tuple[bool, ...] | None = _CORE_CAPABILITIES
        if not plans_capabilities:
            local_model_ids = {id(model) for model in models}
            for model in runtime_models:
                model._set_internal_template_data(  # noqa: SLF001
                    "schema_runtime_validation_base_class_name",
                    base_class_name,
                )
                model._set_internal_template_data(  # noqa: SLF001
                    "schema_runtime_validation_use_base",
                    not _inherits_schema_runtime_validation_base(model, local_model_ids),
                )
            module_plan = SchemaRuntimeValidationModulePlan(base_class_name, plans_capabilities=False)
        else:
            missing_capabilities_by_model = plan_schema_runtime_validation_bases(
                runtime_models,
                runtime_validation_by_model,
                get_base_models=_find_base_classes,
                get_external_capabilities=cls._get_external_schema_runtime_validation_capabilities,
                get_model_requirements=cls._get_schema_runtime_validation_requirements,
            )
            helper_capabilities = frozenset(
                capabilities for capabilities in missing_capabilities_by_model.values() if any(capabilities)
            )
            helper_base_class_names = (
                cls._get_schema_runtime_validation_base_class_names(base_class_name, helper_capabilities, models)
                if helper_capabilities
                else {}
            )
            for model in runtime_models:
                missing_capabilities = missing_capabilities_by_model[id(model)]
                cls._set_schema_runtime_validation_base(
                    model,
                    helper_base_class_names.get(missing_capabilities),
                    missing_capabilities,
                )
            local_helpers = (
                tuple(map(any, zip(*helper_base_class_names, strict=True))) if helper_base_class_names else None
            )
            module_plan = SchemaRuntimeValidationModulePlan(
                base_class_name,
                plans_capabilities=True,
                helper_base_class_names=tuple(helper_base_class_names.items()),
            )
        if local_helpers is not None:
            cls._neutralize_generic_extra_config_for_runtime_root_models(
                runtime_models,
                uses_generated_generic_base_class=uses_generated_generic_base_class,
            )
            cls._add_schema_runtime_validation_helper_imports(
                runtime_models[0],
                runtime_validations,
                local_helpers=local_helpers,
                uses_generated_generic_base_class=uses_generated_generic_base_class,
            )
        models[0].__dict__[cls._SCHEMA_RUNTIME_VALIDATION_MODULE_PLAN_CACHE_KEY] = module_plan
        return module_plan

    @classmethod
    def _has_local_generated_generic_base_class(cls, models: list[DataModel]) -> bool:
        """Return whether this module renders its configured ``BaseModel`` before helpers."""
        if not models or models[0].class_name != cls.BASE_CLASS_NAME:
            return False
        return models[0].base_class == cls.BASE_CLASS_ALIAS

    @classmethod
    def _has_generated_generic_base_class(cls, models: list[DataModel]) -> bool:
        """Return whether local helpers can inherit a generated generic ``BaseModel``."""
        if cls._has_local_generated_generic_base_class(models):
            return True
        return any(
            base.class_name == cls.BASE_CLASS_NAME and base.base_class == cls.BASE_CLASS_ALIAS
            for model in models
            for base in _find_base_classes(model)
        )

    @classmethod
    @override
    def get_module_code_insertion_index(cls, models: list[DataModel]) -> int:
        """Emit a generated generic base before helpers that inherit from it."""
        return 1 if cls._has_local_generated_generic_base_class(models) else 0

    @override
    def invalidate_render_caches(self) -> None:
        """Clear the compact module plan with the model's other render caches."""
        super().invalidate_render_caches()
        self.__dict__.pop(self._SCHEMA_RUNTIME_VALIDATION_MODULE_PLAN_CACHE_KEY, None)

    @override
    def refresh_field_config(self) -> None:
        """Select Python's regex engine for lookaround patterns, also those fields gained after ``__init__``.

        Collapsed root models inline their patterns; a config that already names an engine is kept. The patterns that
        later passes give the fields select it once every model is final (``finalize_type_alias_users``), and so does a
        lone surrogate (``_select_surrogate_regex_engines``).
        """
        if self._own_patterns_select_python():
            self._select_python_regex_engine()

    @override
    def _applied_config(self, visiting: frozenset[int] = frozenset()) -> tuple[dict[str, str], frozenset[str] | None]:
        """Return the config items Pydantic applies: those of the bases merged in order, then the model's own.

        A configured base class the generator does not read comes first and leaves every item unknown, and a base
        that has one leaves unknown the items it does not set. A base on an inheritance cycle, which generation
        rejects later, adds nothing.
        """
        visiting |= {id(self)}
        items: dict[str, str] = {}
        known: set[str] | None = set() if _has_opaque_custom_base(self, self.BASE_CLASS) else None
        for inherited, settled in (
            base._applied_config(visiting)  # noqa: SLF001
            for base in _find_base_classes(self)
            if isinstance(base, BaseModel) and id(base) not in visiting
        ):
            items.update(inherited)
            if settled is not None:
                known = set(settled)
            elif known is not None:
                known.update(inherited)
        items.update(own := self._rendered_config_items())
        if known is None:
            return items, None
        known.update(name for name, _ in own)
        return items, frozenset(known)

    @override
    def _pydantic_constructs(self) -> Iterator[tuple[PydanticConstruct, str | None]]:
        """Also yield the row of ``regex_engine``, which the schema validator adapters write in their config.

        The helper writes ``regex_engine`` in the config of the adapter of a rule's type (``python_regex_types``).
        """
        yield from super()._pydantic_constructs()
        if (validation := self.schema_runtime_validation) is not None and any(
            rule.python_regex_types for rule in validation.pattern_properties
        ):
            yield from _config_constructs((("regex_engine", '"python-re"'),))

    @classmethod
    @override
    def invalidate_module_code_cache(cls, models: list[DataModel]) -> None:
        """Discard the one module plan after parser-side import collision renames."""
        if models:
            models[0].__dict__.pop(cls._SCHEMA_RUNTIME_VALIDATION_MODULE_PLAN_CACHE_KEY, None)

    @staticmethod
    def _get_schema_runtime_validation_models(
        models: list[DataModel],
    ) -> tuple[list[DataModel], list[SchemaRuntimeValidation]]:
        """Return parser-owned runtime-validation models and their metadata."""
        found = [(model, validation) for model in models if (validation := _internal_runtime_validation(model))]
        return [model for model, _ in found], [validation for _, validation in found]

    @classmethod
    def _add_schema_runtime_validation_helper_imports(
        cls,
        model: DataModel,
        runtime_validations: list[SchemaRuntimeValidation],
        *,
        local_helpers: tuple[bool, ...],
        uses_generated_generic_base_class: bool = False,
    ) -> None:
        """Add imports only when this module renders a shared runtime helper.

        ``local_helpers`` says whether the module renders each helper layer (``_HELPER_LAYER_NAMES``).
        """
        has_local_core_helper, _, has_local_constant_types_helper, _, has_local_contains_helper = local_helpers
        additional_imports = model._additional_imports  # noqa: SLF001
        helper_imports = (IMPORT_MODEL_VALIDATOR, IMPORT_ANY, IMPORT_CLASSVAR)
        if (
            any(local_helpers[1:])
            or not cls._has_custom_schema_runtime_validation_helper(model)
            or (model.custom_template_dir is not None and not overrides_builtin_templates(model.custom_template_dir))
        ):
            helper_imports += (
                Import(from_="collections.abc", import_="Mapping", alias="_Mapping", keep_unaliased=True),
            )
        if not uses_generated_generic_base_class:
            helper_imports += (IMPORT_BASE_MODEL,)
        facts = _helper_template_facts(runtime_validations)
        if (
            has_local_constant_types_helper
            or has_local_contains_helper
            or (has_local_core_helper and (facts["has_pattern_properties"] or facts["has_unique_items_regex_paths"]))
        ):
            helper_imports += (Import(import_="re"),)
        if has_local_core_helper and facts["has_pattern_properties"]:
            helper_imports += (IMPORT_TYPE_ADAPTER,)
        if has_local_core_helper and facts["has_json_value"]:
            helper_imports += (Import(from_="enum", import_="Enum", alias="_Enum", keep_unaliased=True),)
        for import_ in helper_imports:
            if import_ not in additional_imports:
                additional_imports.append(import_)

    @classmethod
    def _neutralize_generic_extra_config_for_runtime_root_models(
        cls,
        runtime_models: list[DataModel],
        *,
        uses_generated_generic_base_class: bool,
    ) -> None:
        """Keep a runtime helper's generic ``extra`` config off RootModel subclasses."""
        if not uses_generated_generic_base_class:
            return
        for model in runtime_models:
            if not model.IS_ROOT_MODEL or not model._internal_template_data.get(  # noqa: SLF001
                "schema_runtime_validation_use_base"
            ):
                continue
            model._set_internal_template_data(  # noqa: SLF001
                key=_NEUTRALIZE_ROOT_MODEL_EXTRA_CONFIG_TEMPLATE_DATA_KEY,
                value=True,
            )
            if IMPORT_CONFIG_DICT not in model._additional_imports:  # noqa: SLF001
                model._additional_imports.append(IMPORT_CONFIG_DICT)  # noqa: SLF001
            model.invalidate_render_caches()

    @staticmethod
    def _has_core_schema_runtime_validation(runtime_validation: SchemaRuntimeValidation) -> bool:
        """Return whether a validation needs the existing template-rendered helper.

        A conditional bounds rule and a contains rule need it for the JSON value of a Python value
        (``_json_schema_json_value``) and the JSON equality of a condition or a matcher
        (``_json_schema_conditional_equal``), which their layers call on the core helper they are rendered on.
        """
        return bool(
            runtime_validation.pattern_properties
            or runtime_validation.required_groups
            or runtime_validation.conditional_required
            or runtime_validation.unique_items
            or runtime_validation.conditional_bounds
            or runtime_validation.contains
        )

    @classmethod
    def _get_external_schema_runtime_validation_capabilities(cls, model: DataModel) -> tuple[bool, ...]:
        """Return reusable capabilities supplied by another generated module.

        Core helpers are specialized to the rules rendered in their module;
        reusing one could shadow a child rule with a method it does not have.
        The Python-rendered layers are invariant, so they remain reusable.
        """
        runtime_validation = _internal_runtime_validation(model)
        markers = cls._validation_base_markers()[1:]
        rules = _python_layer_rules(runtime_validation) if runtime_validation else (False,) * len(markers)
        return (
            False,
            *(
                rule or bool(model._internal_template_data.get(marker))  # noqa: SLF001
                for rule, marker in zip(rules, markers, strict=True)
            ),
        )

    @classmethod
    def _validation_base_markers(cls) -> tuple[str, ...]:
        """Return the template data key that records each helper layer of a model's base (``_HELPER_LAYER_NAMES``)."""
        return (
            cls._CORE_VALIDATION_BASE_MARKER,
            cls._PROPERTY_COUNT_VALIDATION_BASE_MARKER,
            cls._CONSTANT_TYPES_VALIDATION_BASE_MARKER,
            cls._CONDITIONAL_BOUNDS_VALIDATION_BASE_MARKER,
            cls._CONTAINS_VALIDATION_BASE_MARKER,
        )

    @classmethod
    def _set_schema_runtime_validation_base(
        cls,
        model: DataModel,
        base_class_name: str | None,
        capabilities: tuple[bool, ...],
    ) -> None:
        """Record the one local helper base, if any, used by a generated model."""
        if base_class_name is not None:
            model._set_internal_template_data("schema_runtime_validation_base_class_name", base_class_name)  # noqa: SLF001
        model._set_internal_template_data("schema_runtime_validation_use_base", any(capabilities))  # noqa: SLF001
        for marker, capability in zip(cls._validation_base_markers(), capabilities, strict=True):
            model._set_internal_template_data(marker, capability)  # noqa: SLF001

    @classmethod
    def _get_schema_runtime_validation_requirements(
        cls,
        model: DataModel,
        runtime_validation: SchemaRuntimeValidation,
    ) -> tuple[bool, ...]:
        """Return the helper layers one generated model requires (``_HELPER_LAYER_NAMES``)."""
        return (
            cls._has_core_schema_runtime_validation(runtime_validation)
            or (bool(runtime_validation.property_count) and cls._has_custom_schema_runtime_validation_helper(model)),
            *_python_layer_rules(runtime_validation),
        )

    @classmethod
    def _has_custom_schema_runtime_validation_helper(cls, model: DataModel) -> bool:
        """Return whether this model overrides the shared schema-validator helper template."""
        return bool(
            model.custom_template_dir
            and (model.custom_template_dir / cls.SCHEMA_RUNTIME_VALIDATION_HELPERS_TEMPLATE_FILE_PATH).is_file()
        )

    @classmethod
    def _get_schema_runtime_validation_base_class_names(
        cls,
        base_class_name: str,
        helper_capabilities: frozenset[tuple[bool, ...]],
        models: list[DataModel],
    ) -> dict[tuple[bool, ...], str]:
        """Allocate at most one shared helper for each missing capability set; the first of ``_PRIMARY_HELPERS``."""
        primary_capabilities = next(
            capabilities for capabilities in _PRIMARY_HELPERS if capabilities in helper_capabilities
        )
        base_class_names = {primary_capabilities: base_class_name}
        reserved_names = {base_class_name}
        for capabilities, suffix in _HELPER_SUFFIXES.items():
            if capabilities not in helper_capabilities or capabilities == primary_capabilities:
                continue
            base_class_names[capabilities] = cls._get_unique_schema_runtime_validation_base_class_name(
                base_class_name,
                suffix,
                models,
                reserved_names,
            )
            reserved_names.add(base_class_names[capabilities])
        return base_class_names

    @staticmethod
    def _get_unique_schema_runtime_validation_base_class_name(
        base_class_name: str,
        suffix: str,
        models: list[DataModel],
        reserved_names: set[str] | None = None,
    ) -> str:
        """Choose a synthetic helper name that cannot collide with generated models."""
        class_names = {model.class_name for model in models}
        class_names.update(
            source.class_name
            for model in models
            for reference in _model_references(model, model.base_classes)
            if isinstance(source := reference.source, DataModel)
        )
        if reserved_names:
            class_names.update(reserved_names)
        return first_free(f"{base_class_name}{suffix}", class_names.__contains__, "joined-from-2")[0]

    @classmethod
    def _render_capability_validation_helpers(
        cls,
        base_class_name: str,
        helper_base_class_names: dict[tuple[bool, ...], str],
        runtime_validations: list[SchemaRuntimeValidation],
        models: list[DataModel],
    ) -> str:
        """Render only the shared helpers needed for local missing capabilities.

        The helper of a set of capabilities is the core helper, or its outermost Python-rendered layer
        (``_HELPER_LAYER_NAMES``) on the helper of the set without that layer; a set no model needs is named for its
        layers.
        """
        from datamodel_code_generator.model.pydantic_v2._schema_runtime_validation import (  # noqa: PLC0415
            render_conditional_bounds_validation_base,
            render_constant_types_validation_base,
            render_contains_validation_base,
            render_property_count_validation_base,
        )

        layer_renderers = (
            render_property_count_validation_base,
            render_constant_types_validation_base,
            render_conditional_bounds_validation_base,
            render_contains_validation_base,
        )

        names = dict(helper_base_class_names)
        needed = set(names)
        for capabilities in reversed(_HELPER_SUFFIXES):
            if capabilities in needed and any(inner := _helper_inner_capabilities(capabilities)):
                needed.add(inner)
        helpers: list[str] = []
        for capabilities, suffix in _HELPER_SUFFIXES.items():
            if capabilities not in needed:
                continue
            if capabilities not in names:
                names[capabilities] = cls._get_unique_schema_runtime_validation_base_class_name(
                    base_class_name, suffix, models, set(names.values())
                )
            if capabilities == _CORE_CAPABILITIES:
                core_runtime_validations = [
                    runtime_validation
                    for runtime_validation in runtime_validations
                    if cls._has_core_schema_runtime_validation(runtime_validation)
                ]
                helpers.append(
                    cls._render_core_schema_runtime_validation_helpers(
                        names[capabilities], core_runtime_validations, models
                    ).rstrip()
                )
                continue
            inner = _helper_inner_capabilities(capabilities)
            render = layer_renderers[max(index for index, capability in enumerate(capabilities) if capability) - 1]
            helpers.append(render(names[capabilities], names[inner] if any(inner) else "BaseModel"))
        return "\n\n\n".join(helpers) + "\n"

    @classmethod
    def _render_core_schema_runtime_validation_helpers(
        cls,
        base_class_name: str,
        runtime_validations: list[SchemaRuntimeValidation],
        models: list[DataModel],
    ) -> str:
        """Render the unchanged core runtime-validation helper template."""
        custom_template_dir = next(
            (model.custom_template_dir for model in models if model.custom_template_dir is not None),
            None,
        )
        context: dict[str, Any] = {
            "schema_runtime_validation_base_class_name": base_class_name,
            **_helper_template_facts(runtime_validations),
        }
        if renderer := _builtin_renderer(
            cls, cls.SCHEMA_RUNTIME_VALIDATION_HELPERS_TEMPLATE_FILE_PATH, custom_template_dir
        ):
            return renderer(**context)

        custom_template_path = (
            custom_template_dir / cls.SCHEMA_RUNTIME_VALIDATION_HELPERS_TEMPLATE_FILE_PATH
            if custom_template_dir is not None and overrides_builtin_templates(custom_template_dir)
            else None
        )
        if custom_template_path is not None and custom_template_path.is_file():
            if context["has_conditional_presence"] or context["has_not_required_groups"]:
                msg = (
                    "Custom schema runtime validation helper overrides do not yet support generated presence-only "
                    "conditions or not-required groups. Remove the helper override or disable schema validators."
                )
                raise Error(msg)
            if context["has_unique_items"]:
                msg = (
                    f"Custom schema runtime validation helper {custom_template_path} overrides do not yet support "
                    "generated uniqueItems validation. Remove the helper override or disable schema validators."
                )
                raise Error(msg)
        template = _get_template_with_custom_dir(
            Path(cls.SCHEMA_RUNTIME_VALIDATION_HELPERS_TEMPLATE_FILE_PATH),
            custom_template_dir,
        )
        return template.render(**context)

    @classmethod
    @override
    def create_typed_extra_field(
        cls,
        *,
        field_model: type[DataModelFieldBase],
        data_type: DataType,
    ) -> DataModelFieldBase:
        """Create the Pydantic v2 typed extra field: a ``dict``, the one annotation Pydantic accepts for extras."""
        if (dict_key := data_type.dict_key) is not None and (
            (capability := cls._TYPED_EXTRA_DICT_KEY_CAPABILITY) is None or not capability(dict_key)
        ):
            drop_tree(dict_key)
            clear_parents(dict_key)
            data_type.dict_key = None
        data_type.use_generic_container = False

        return field_model(
            name=cls.TYPED_EXTRA_FIELD_NAME,
            data_type=data_type,
            required=True,
            original_name=cls.TYPED_EXTRA_FIELD_NAME,
        )

    def __init__(
        self,
        *,
        reference: Reference,
        fields: list[DataModelFieldBase],
        decorators: list[str] | None = None,
        base_classes: list[Reference] | None = None,
        custom_base_class: str | list[str] | None = None,
        custom_template_dir: Path | None = None,
        extra_template_data: defaultdict[str, Any] | None = None,
        path: Path | None = None,
        description: str | None = None,
        default: Any = UNDEFINED,
        nullable: bool = False,
        keyword_only: bool = False,
        treat_dot_as_module: bool | None = None,
    ) -> None:
        """Initialize BaseModel with ConfigDict generation from template data."""
        super().__init__(
            reference=reference,
            fields=fields,
            decorators=decorators,
            base_classes=base_classes,
            custom_base_class=custom_base_class,
            custom_template_dir=custom_template_dir,
            extra_template_data=extra_template_data,
            path=path,
            description=description,
            default=default,
            nullable=nullable,
            keyword_only=keyword_only,
            treat_dot_as_module=treat_dot_as_module,
        )
        self._prepare_schema_runtime_validation_config()
        config_parameters: dict[str, Any] = dict(
            build_base_config_parameters(
                extra_template_data=self.extra_template_data,
                all_data_types=self.all_data_types if self.SUPPORTS_ARBITRARY_TYPES_ALLOWED else (),
                config_attributes_v2=self._CONFIG_ATTRIBUTES_V2,
                config_attributes_v2_11=self._CONFIG_ATTRIBUTES_V2_11,
                include_extra=self.SUPPORTS_CONFIG_EXTRA,
            )
        )

        if alias_generator := _alias_generator_name(self.extra_template_data.get(_ALIAS_GENERATOR_TEMPLATE_DATA_KEY)):
            config_parameters[_ALIAS_GENERATOR_TEMPLATE_DATA_KEY] = alias_generator
            self._additional_imports.append(_ALIAS_GENERATOR_IMPORTS[alias_generator])

        config_parameters.update(_config_dict_items(self.extra_template_data.get("config")))

        # Handle json_schema_extra from schema extensions (x-* fields)
        model_extras = self.extra_template_data.get("model_extras")
        if model_extras:
            existing = cast("dict[str, Any]", config_parameters.get("json_schema_extra") or {})
            config_parameters["json_schema_extra"] = {**existing, **model_extras}

        if config_parameters:
            self._store_config(config_parameters)
            _import_rendered_alias_generator(self, self._rendered_config_items())
        else:
            self.extra_template_data.pop("config", None)
            self._pop_internal_template_data(_CONFIG_ITEMS_TEMPLATE_DATA_KEY)

        self.refresh_field_config()
        self._process_schema_runtime_validation()
        self._process_validators()

    def _get_schema_runtime_validation(self) -> SchemaRuntimeValidation | None:
        if (internal_runtime_validation := _internal_runtime_validation(self)) is not None:
            return internal_runtime_validation
        runtime_validation = self.extra_template_data.get("schema_runtime_validation")
        if _is_internal_schema_runtime_validation(runtime_validation) and runtime_validation:
            return runtime_validation
        return None

    def _prepare_schema_runtime_validation_config(self) -> None:
        """Prepare Pydantic config required by schema-derived runtime validators."""
        runtime_validation = self._get_schema_runtime_validation()
        if runtime_validation is None:
            return
        self.extra_template_data.pop("schema_runtime_validation", None)
        self._set_internal_template_data("schema_runtime_validation", runtime_validation)
        if runtime_validation.pattern_properties:
            self.extra_template_data["force_extra_allow"] = True

    def _process_schema_runtime_validation(self) -> None:
        """Add imports required by schema-derived runtime validators."""
        runtime_validation = self._get_schema_runtime_validation()
        if runtime_validation is None:
            return

        if (property_count_rule := runtime_validation.property_count) is not None:
            from datamodel_code_generator.model.pydantic_v2._schema_runtime_validation import (  # noqa: PLC0415
                render_property_count_rule,
            )

            property_count_line = render_property_count_rule(property_count_rule)
            if property_count_line not in self._internal_template_data.get("class_body_lines", ()):
                self._append_internal_template_data("class_body_lines", property_count_line)

        from datamodel_code_generator.model.pydantic_v2._schema_runtime_validation import (  # noqa: PLC0415
            render_conditional_bounds_rules,
            render_constant_types_rules,
            render_contains_rules,
            render_unique_items_rules,
        )

        rule_lines_by_kind = (
            render_unique_items_rules(runtime_validation.unique_items)
            if runtime_validation.unique_items or runtime_validation.replace_unique_items
            else (),
            render_constant_types_rules(runtime_validation.constant_types)
            if runtime_validation.constant_types or runtime_validation.replace_constant_types
            else (),
            render_conditional_bounds_rules(runtime_validation.conditional_bounds)
            if runtime_validation.conditional_bounds
            else (),
            render_contains_rules(runtime_validation.contains)
            if runtime_validation.contains or runtime_validation.replace_contains
            else (),
        )
        for rule_lines in rule_lines_by_kind:
            if rule_lines and rule_lines[0] not in self._internal_template_data.get("class_body_lines", ()):
                for line in rule_lines:
                    self._append_internal_template_data("class_body_lines", line)

        if runtime_validation.property_count is not None or any(rule_lines_by_kind):
            self._additional_imports.append(IMPORT_ANY)
            self._additional_imports.append(IMPORT_CLASSVAR)

        for data_type in runtime_validation.data_types:
            self._additional_imports.extend(data_type.all_imports)

    def _process_validators(self) -> None:
        """Process validator definitions and prepare them for template rendering."""
        self.extra_template_data.pop("prepared_validators", None)
        validators = self.extra_template_data.get("validators")
        if not validators:
            return

        from datamodel_code_generator.validators import (  # noqa: PLC0415
            format_validation_error,
            normalize_validators,
        )

        try:
            validators = normalize_validators(validators, target=self.target_python_version)
        except ValidationError as e:
            msg = f"Invalid validators configuration: {format_validation_error(e)}"
            raise Error(msg) from e

        prepared_validators: list[dict[str, Any]] = []
        validator_bindings: list[tuple[dict[str, Any], Import]] = []
        for validator in validators:
            fields = validator.get("fields") or [validator.get("field")]
            fields = [f for f in fields if f]
            if not fields:
                continue

            function_path: str = validator["function"]
            function_name = function_path.rsplit(".", 1)[-1]
            mode = validator.get("mode", "after")

            prepared = {
                "fields_str": ", ".join(stable_repr(f) for f in fields),
                "mode_str": f"mode={stable_repr(mode)}",
                "method_name": f"{function_name}_validator",
                "function_name": function_name,
                "mode": mode,
            }
            prepared_validators.append(prepared)
            validator_bindings.append((prepared, import_ := Import.from_full_path(function_path)))

            self._additional_imports.append(import_)

        if prepared_validators:
            self._validator_bindings = validator_bindings
            self._set_internal_template_data("prepared_validators", prepared_validators)
            self._additional_imports.append(IMPORT_FIELD_VALIDATOR)
            self._additional_imports.append(IMPORT_ANY)

            modes = {v["mode"] for v in prepared_validators}
            if modes - {"plain"}:
                self._additional_imports.append(IMPORT_VALIDATION_INFO)
            if "wrap" in modes:
                self._additional_imports.append(IMPORT_VALIDATOR_FUNCTION_WRAP_HANDLER)
            self._prepare_validator_method_names(self, {})

    @classmethod
    def _prepare_validator_method_names(
        cls, model: DataModel, scopes: dict[ModelKey, set[str]], keys: _ModelKeys | None = None
    ) -> set[str]:
        """Reserve inherited methods after their model references have been resolved.

        ``scopes`` holds the names reserved for each model by the model key that ``keys`` gives it.
        """
        keys = keys or _ModelKeys()
        if (reserved_names := scopes.get(key := keys.of(model))) is not None:
            return reserved_names
        reserved_names = scopes[key] = {field.name for field in model.fields if field.name is not None}
        reserved_names.update(re.findall(r"\bdef\s+(\w+)\s*\(", "\n".join(model.methods)))
        for ancestor in _find_base_classes(model):
            reserved_names.update(cls._prepare_validator_method_names(ancestor, scopes, keys))
        from datamodel_code_generator.validators import _reserve_validator_name  # noqa: PLC0415

        next_suffixes: dict[str, int] = {}
        for validator, import_ in getattr(model, "_validator_bindings", ()):
            name = _reserve_validator_name(f"{import_.import_}_validator", reserved_names, next_suffixes)
            if name != validator["method_name"]:
                validator["method_name"] = name
                model.invalidate_render_caches()
        return reserved_names

    @classmethod
    @override
    def resolve_module_import_conflicts(
        cls,
        models: Iterable[DataModel],
        model_imports: Mapping[DataModel, tuple[Import, ...]],
        imports: Imports,
    ) -> None:
        """Bind external validators consistently across every model in a module."""
        if not _holds(imports, IMPORT_FIELD_VALIDATOR):
            return
        models = tuple(models)
        validator_models = [
            (model, validators) for model in models if (validators := getattr(model, "_validator_bindings", None))
        ]
        method_scopes: dict[ModelKey, set[str]] = {}
        method_keys = _ModelKeys()
        for model, _ in validator_models:
            cls._prepare_validator_method_names(model, method_scopes, method_keys)
        validator_imports = {import_ for _, validators in validator_models for _, import_ in validators}
        reserved_names = {model.class_name for model in models}
        reserved_names.update(
            import_.binding_name
            for model in models
            for import_ in model_imports[model]
            if import_ not in validator_imports
        )
        validator_import_keys = set(map(_import_key, validator_imports))
        reserved_names.update(binding for key, binding in _import_bindings(imports) if key not in validator_import_keys)
        from datamodel_code_generator.validators import _reserve_validator_name  # noqa: PLC0415

        names: dict[Import, str] = {}
        next_suffixes: dict[str, int] = {}
        for model, validators in validator_models:
            for validator, import_ in validators:
                if (name := names.get(import_)) is None:
                    name = names[import_] = _reserve_validator_name(import_.import_, reserved_names, next_suffixes)
                if name == validator["function_name"]:
                    continue
                validator["function_name"] = name
                imports.apply_alias(Import(from_=import_.from_, import_=import_.import_, alias=name))
                model.invalidate_render_caches()

    @classmethod
    @override
    def create_base_class_model(
        cls,
        config: dict[str, Any],
        reference: Reference,
        custom_template_dir: Path | None = None,
        keyword_only: bool = False,
        treat_dot_as_module: bool | None = None,
    ) -> BaseModel | None:
        """Create a shared base class model for DRY configuration.

        Creates a BaseModel that inherits from pydantic's BaseModel (aliased as _BaseModel)
        with the specified configuration. Updates the reference path and name in place.
        """
        reference.path = f"#/{cls.BASE_CLASS_NAME}"
        reference.name = cls.BASE_CLASS_NAME

        extra_data: defaultdict[str, dict[str, Any]] = defaultdict(dict)
        for key, value in config.items():
            extra_data[ALL_MODEL][key] = value

        base_model = cls(
            reference=reference,
            fields=[],
            custom_template_dir=custom_template_dir,
            extra_template_data=extra_data,
            keyword_only=keyword_only,
            treat_dot_as_module=treat_dot_as_module,
        )

        base_model.base_classes = [BaseClassDataType(type=cls.BASE_CLASS_ALIAS)]
        base_model._additional_imports = [
            imp
            for imp in base_model._additional_imports
            if not (imp.from_ == IMPORT_BASE_MODEL.from_ and imp.import_ == IMPORT_BASE_MODEL.import_)
        ]
        base_model._additional_imports.append(
            Import(from_=IMPORT_BASE_MODEL.from_, import_=IMPORT_BASE_MODEL.import_, alias=cls.BASE_CLASS_ALIAS)
        )

        return base_model


_rebuild_model_with_datamodel_namespace(Constraints)
_rebuild_model_with_datamodel_namespace(DataModelField)
