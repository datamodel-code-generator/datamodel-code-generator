"""Parity and generation checks for standalone built-in template renderers."""

from __future__ import annotations

import contextlib
import io
import os
from collections import defaultdict
from dataclasses import fields as dataclass_fields
from functools import cached_property
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from datamodel_code_generator import Error
from datamodel_code_generator.model import DataModelFieldBase
from datamodel_code_generator.model._compiled_template_runtime import (
    MISSING,
    Namespace,
    Scope,
    concat,
    filter_default,
    filter_indent,
    filter_join,
    filter_length,
    filter_list,
    filter_pprint,
    filter_replace,
    filter_repr,
    filter_selectattr,
    getattr_,
    getitem,
    is_defined,
    loop_last_iter,
    namespace,
    setattr_,
    stringify,
)
from datamodel_code_generator.model._compiled_templates import get_builtin_renderer
from datamodel_code_generator.model.base import (
    TEMPLATE_DIR,
    DataModel,
    TemplateBase,
    _safe_dataclass_arguments,
    get_template,
)
from datamodel_code_generator.model.dataclass import DataClass
from datamodel_code_generator.model.dataclass import DataModelField as DataclassField
from datamodel_code_generator.model.pydantic_v2.base_model import BaseModel
from datamodel_code_generator.model.pydantic_v2.base_model import DataModelField as PydanticField
from datamodel_code_generator.model.pydantic_v2.dataclass import DataClass as PydanticDataclass
from datamodel_code_generator.model.pydantic_v2.dataclass import DataModelField as PydanticDataclassField
from datamodel_code_generator.model.pydantic_v2.root_model import RootModel
from datamodel_code_generator.model.pydantic_v2.root_model_type_alias import RootModelTypeAlias
from datamodel_code_generator.model.runtime_validation import (
    PropertyCountRule,
    RequiredGroupsRule,
    SchemaRuntimeValidation,
    UniqueItemsRule,
    _make_internal_schema_runtime_validation,
)
from datamodel_code_generator.reference import Reference
from datamodel_code_generator.types import DataType
from scripts._template_compiler import build_environment, compile_template
from scripts._template_compiler import inventory as template_compiler_inventory
from tests.conftest import assert_output

ROOT = Path(__file__).parents[2]
EXPECTED_PATH = ROOT / "tests/data/expected/model/compiled_templates"


def _reference(name: str) -> Reference:
    return Reference(name=name, original_name=name, path=name)


def _model_context(model: DataModel) -> dict[str, Any]:
    """Mirror the public DataModel.render context for its built-in Jinja oracle."""
    return {
        "class_name": model.class_name,
        "fields": model.rendered_fields,
        "decorators": model.decorators,
        "base_class": model.base_class,
        "methods": model.methods,
        "description": model.rendered_description if model.FORMAT_DESCRIPTION_AS_DOCSTRING else model.description,
        "dataclass_arguments": (
            _safe_dataclass_arguments(model.dataclass_arguments)
            if model.USES_DATACLASS_ARGUMENTS
            else model.dataclass_arguments
        ),
        "path": model.path,
        **model._builtin_template_data(),
    }


def _runtime_validation() -> SchemaRuntimeValidation:
    return _make_internal_schema_runtime_validation(
        required_groups=[
            RequiredGroupsRule(
                keyword="oneOf",
                groups=((("first",),),),
            )
        ]
    )


def _property_count_runtime_validation() -> SchemaRuntimeValidation:
    return _make_internal_schema_runtime_validation(
        property_count=PropertyCountRule(min_properties=1),
    )


def _field(*, name: str = "value", type_hint: str = "str") -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        key=name,
        default="'value'",
        field=None,
        type_hint=type_hint,
        base_type_hint=type_hint,
        annotated=None,
        docstring='"""field \\ \\" quote\nnext"""',
        inline_field_docstring=None,
        use_inline_field_description=True,
        use_pydantic_extra_annotations_dict=False,
        pydantic_extra_type_hint="dict[str, str]",
        has_default_factory_in_field=False,
        required=True,
        use_default_with_required=False,
        represented_default="None",
        strip_default_none=False,
        data_type=SimpleNamespace(is_optional=False),
        extras={"is_classvar": False},
    )


def _template_context(path: Path) -> dict[str, Any]:
    """Build a rich, deterministic context which reaches built-in branches."""
    first_field = _field()
    second_field = _field(name="other", type_hint="list[str]")
    rule = SimpleNamespace(
        declared_properties=("known",),
        rejected_patterns=("^forbidden",),
        pattern_properties=(("^x", SimpleNamespace(type_hint="str")),),
        additional_property_type=None,
        allow_unmatched=True,
        keyword="oneOf",
        groups=((("choice",),),),
        condition=((("mode",), ("on",)),),
        then_groups=((("then",),),),
        else_groups=(),
    )
    config: Any = {"frozen": "True"} if path.name == "dataclass.jinja2" else SimpleNamespace(regex_engine='"python-re"')
    return {
        "all_fields": [first_field, second_field],
        "base_class": "BaseModel",
        "class_body_lines": ['marker = {"braces": "{}"}'],
        "class_name": "Unicodeモデル",
        "comment": 'comment \\ " {braces}\nnext',
        "config": config,
        "config_items": [("extra", "'allow'")],
        "_safe_config_items": [("frozen", "True")],
        "dataclass_arguments": {"frozen": True, "repr": False},
        "decorators": ["@decorator"],
        "description": '"""description こんにちは \\ \\" {braces}\nnext"""',
        "fields": [first_field, second_field],
        "has_conditional_required": True,
        "has_pattern_properties": True,
        "has_required_groups": True,
        "is_functional_syntax": path.name in {"TypedDict.jinja2", "TypedDictFunction.jinja2"},
        "methods": ['def generated(self) -> str:\n        return "ok"'],
        "path": "Unicodeモデル",
        "prepared_validators": [
            {
                "fields_str": "'value'",
                "function_name": "validate_value",
                "method_name": "validate_value",
                "mode": "after",
                "mode_str": "mode='after'",
            }
        ],
        "py_type": "str",
        "schema_runtime_validation": SimpleNamespace(
            conditional_required=(rule,),
            pattern_properties=(rule,),
            required_groups=(rule,),
        ),
        "schema_runtime_validation_base_class_name": "_RuntimeBase",
        "schema_runtime_validation_use_base": True,
        "sequence_base_class": "Sequence[str]",
        "sequence_item_type": "str",
        "sequence_slice_type": "list[str]",
        "typed_dict_kwargs_suffix": ", total=False",
    }


def _render_all_builtin_templates(*, compiled: bool) -> str:
    rendered: list[str] = []
    for path in template_compiler_inventory.iter_template_paths(TEMPLATE_DIR):
        relative_path = path.relative_to(TEMPLATE_DIR)
        context = _template_context(relative_path)
        if compiled:
            renderer = get_builtin_renderer(relative_path)
            if renderer is None:  # pragma: no cover - error message aids generated-registry debugging
                pytest.fail(f"missing standalone renderer for {relative_path}")
            output = renderer(**context)
        else:
            output = get_template(relative_path).render(**context)
        rendered.append(f"=== {relative_path.as_posix()}\n{output}\n=== end {relative_path.as_posix()} ===")
    return "\n".join(rendered) + "\n"


def _template_branch_cases() -> tuple[tuple[str, Path, dict[str, Any]], ...]:  # noqa: PLR0914
    """Return branch-focused contexts beyond the full-template smoke context."""

    class VanishingFields:
        """Exercise repeated Jinja truth tests on a context extension object."""

        def __init__(self) -> None:
            self.truth_tests = 0

        def __bool__(self) -> bool:
            self.truth_tests += 1
            return self.truth_tests == 1

    base_path = Path("pydantic_v2/BaseModel.jinja2")
    base_context = _template_context(base_path)
    empty_base = {
        **base_context,
        "fields": [],
        "description": None,
        "config": None,
        "class_body_lines": [],
        "methods": [],
        "prepared_validators": None,
        "schema_runtime_validation": None,
        "schema_runtime_validation_use_base": False,
    }
    wrap_validator = dict(base_context["prepared_validators"][0], mode="wrap", mode_str="mode='wrap'")
    plain_validator = dict(base_context["prepared_validators"][0], mode="plain", mode_str="mode='plain'")
    annotated_field = _field(name="annotated")
    annotated_field.annotated = "Annotated[str, Field()]"
    annotated_field.field = "Field(default='x')"
    default_field = _field(name="default")
    default_field.required = False
    default_field.represented_default = "'value'"
    inline_field = _field(name="inline")
    inline_field.docstring = None
    inline_field.inline_field_docstring = '"""inline \\ description"""'
    msgspec_default_field = _field(name="defaulted")
    msgspec_default_field.docstring = None
    msgspec_default_field.inline_field_docstring = '"""defaulted inline"""'
    msgspec_default_field.required = False
    msgspec_default_field.represented_default = "'fallback'"
    no_description_field = _field(name="undocumented")
    no_description_field.docstring = None
    no_description_field.inline_field_docstring = None
    concrete_field = _field(name="concrete")
    concrete_field.docstring = None
    concrete_field.field = "Field(default='concrete')"
    annotated_only_field = _field(name="annotated_only")
    annotated_only_field.annotated = "Annotated[str, Marker()]"
    annotated_only_field.docstring = None
    annotated_and_field = _field(name="annotated_and_field")
    annotated_and_field.annotated = "Annotated[str, Marker()]"
    annotated_and_field.field = "field(default='annotated')"
    annotated_and_field.docstring = None
    classvar_field = _field(name="tag")
    classvar_field.extras = {"is_classvar": True}
    extra_annotations_field = _field(name="extra")
    extra_annotations_field.docstring = None
    extra_annotations_field.use_pydantic_extra_annotations_dict = True

    root_path = Path("pydantic_v2/RootModel.jinja2")
    root_context = _template_context(root_path)
    root_without_optional_context = dict(root_context)
    root_without_optional_context.pop("comment")
    root_without_optional_context.pop("sequence_base_class")
    root_without_optional_context.pop("sequence_item_type")
    root_without_optional_context.pop("sequence_slice_type")
    root_empty = {
        **root_context,
        "fields": [],
        "description": None,
        "config": None,
        "class_body_lines": [],
        "methods": [],
        "schema_runtime_validation": None,
        "schema_runtime_validation_use_base": False,
    }

    typed_dict_path = Path("TypedDict.jinja2")
    typed_dict_class = {**_template_context(typed_dict_path), "is_functional_syntax": False}
    enum_path = Path("Enum.jinja2")
    enum_empty = {**_template_context(enum_path), "fields": [], "description": None}
    schema_path = Path("pydantic_v2/schema_runtime_validation.jinja2")
    any_of_rule = SimpleNamespace(
        keyword="anyOf",
        groups=((("other",),),),
        pattern_properties=(),
        conditional_required=(),
    )
    namespace_after_empty = {
        **_template_context(schema_path),
        "description": None,
        "config": None,
        "class_body_lines": [],
        "schema_runtime_validation": SimpleNamespace(
            pattern_properties=(),
            required_groups=(any_of_rule,),
            conditional_required=(),
        ),
    }
    multiple_pattern_rule = SimpleNamespace(
        declared_properties=("known",),
        rejected_patterns=(),
        pattern_properties=(
            ("^first", SimpleNamespace(type_hint="str")),
            ("^second", SimpleNamespace(type_hint="int")),
        ),
        additional_property_type=SimpleNamespace(type_hint="bool"),
        allow_unmatched=False,
    )
    schema_multiple_patterns_without_prior = {
        **_template_context(schema_path),
        "description": None,
        "config": None,
        "class_body_lines": [],
        "schema_runtime_validation": SimpleNamespace(
            pattern_properties=(multiple_pattern_rule,),
            required_groups=(),
            conditional_required=(),
        ),
    }

    def context_for(template: str, **updates: Any) -> tuple[Path, dict[str, Any]]:
        path = Path(template)
        return path, {**_template_context(path), **updates}

    def context_without(template: str, *names: str, **updates: Any) -> tuple[Path, dict[str, Any]]:
        path, context = context_for(template, **updates)
        for name in names:
            context.pop(name, None)
        return path, context

    alias_cases: list[tuple[str, Path, dict[str, Any]]] = []
    for template in ("TypeAliasAnnotation.jinja2", "TypeAliasType.jinja2", "TypeStatement.jinja2"):
        label = Path(template).stem.lower()
        alias_cases.extend((
            (
                f"{label}_annotated_missing_comment",
                *context_without(template, "comment", description=None, fields=[annotated_only_field]),
            ),
            (
                f"{label}_field_without_docstring",
                *context_without(template, "comment", description=None, fields=[concrete_field]),
            ),
            (
                f"{label}_field_docstring",
                *context_without(template, "comment", description=None, fields=[_field(name="documented")]),
            ),
            (
                f"{label}_empty_missing_comment",
                *context_without(template, "comment", description=None, fields=[]),
            ),
        ))

    return (
        ("pydantic_empty_missing_comment", base_path, empty_base),
        ("pydantic_annotated_default", base_path, {**base_context, "fields": [annotated_field, default_field]}),
        (
            "pydantic_inline_description",
            base_path,
            {**base_context, "fields": [inline_field, _field(name="after_inline")]},
        ),
        ("pydantic_inline_description_last", base_path, {**base_context, "fields": [inline_field]}),
        (
            "pydantic_runtime_custom_base_and_extra_field",
            base_path,
            {
                **base_context,
                "base_class": "CustomBase",
                "fields": [extra_annotations_field, concrete_field],
            },
        ),
        ("pydantic_validator_wrap", base_path, {**base_context, "prepared_validators": [wrap_validator]}),
        ("pydantic_validator_plain", base_path, {**base_context, "prepared_validators": [plain_validator]}),
        ("root_missing_optional_context", root_path, root_without_optional_context),
        ("root_empty", root_path, root_empty),
        ("root_repeated_truth_testing", root_path, {**root_context, "fields": VanishingFields()}),
        ("typed_dict_class", typed_dict_path, typed_dict_class),
        ("enum_empty", enum_path, enum_empty),
        ("namespace_after_empty", schema_path, namespace_after_empty),
        (
            "dataclass_inline_description",
            *context_for("dataclass.jinja2", fields=[inline_field, _field(name="after_inline")]),
        ),
        (
            "dataclass_empty",
            *context_for("dataclass.jinja2", base_class=None, dataclass_arguments={}, description=None, fields=[]),
        ),
        (
            "dataclass_concrete_and_inline_last",
            *context_for("dataclass.jinja2", fields=[concrete_field, inline_field]),
        ),
        (
            "dataclass_default_without_field",
            *context_for("dataclass.jinja2", fields=[default_field]),
        ),
        (
            "enum_inline_description",
            *context_for("Enum.jinja2", fields=[inline_field, _field(name="after_inline")]),
        ),
        (
            "enum_without_and_with_last_inline_description",
            *context_for("Enum.jinja2", fields=[no_description_field, inline_field]),
        ),
        (
            "msgspec_no_base_default_and_inline",
            *context_for(
                "msgspec.jinja2",
                base_class="Struct",
                base_class_kwargs={"frozen": "True"},
                description=None,
                fields=[
                    classvar_field,
                    concrete_field,
                    annotated_only_field,
                    annotated_and_field,
                    no_description_field,
                    msgspec_default_field,
                    _field(name="after_inline"),
                    inline_field,
                ],
            ),
        ),
        (
            "msgspec_empty_without_base",
            *context_for("msgspec.jinja2", base_class=None, description=None, fields=[]),
        ),
        (
            "pydantic_dataclass_inline_description",
            *context_for(
                "pydantic_v2/dataclass.jinja2",
                fields=[inline_field, _field(name="after_inline")],
            ),
        ),
        (
            "pydantic_dataclass_empty",
            *context_for(
                "pydantic_v2/dataclass.jinja2",
                base_class=None,
                config=None,
                dataclass_arguments={},
                description=None,
                fields=[],
            ),
        ),
        (
            "pydantic_dataclass_annotated_and_inline_last",
            *context_for("pydantic_v2/dataclass.jinja2", fields=[annotated_only_field, inline_field]),
        ),
        (
            "root_empty_with_description",
            *context_for("pydantic_v2/RootModel.jinja2", fields=[], config=None, class_body_lines=[], methods=[]),
        ),
        (
            "root_inline_description",
            *context_for(
                "pydantic_v2/RootModel.jinja2",
                description=None,
                config=None,
                fields=[inline_field],
            ),
        ),
        (
            "root_concrete_without_description",
            *context_for(
                "pydantic_v2/RootModel.jinja2",
                description=None,
                config=None,
                fields=[concrete_field],
            ),
        ),
        (
            "root_annotated",
            *context_for("pydantic_v2/RootModel.jinja2", config=None, fields=[annotated_only_field]),
        ),
        (
            "root_type_alias_empty",
            *context_for(
                "pydantic_v2/RootModelTypeAlias.jinja2",
                fields=[],
                description=None,
                config=None,
            ),
        ),
        (
            "root_type_alias_field_docstring",
            *context_for(
                "pydantic_v2/RootModelTypeAlias.jinja2",
                description=None,
                config=None,
                fields=[_field(name="documented")],
            ),
        ),
        ("schema_multiple_patterns_without_prior", schema_path, schema_multiple_patterns_without_prior),
        (
            "schema_one_of_without_prior",
            schema_path,
            {
                **namespace_after_empty,
                "schema_runtime_validation": SimpleNamespace(
                    pattern_properties=(),
                    required_groups=(SimpleNamespace(keyword="oneOf", groups=((("one",),),)),),
                    conditional_required=(),
                ),
            },
        ),
        (
            "schema_any_of_with_prior",
            schema_path,
            {
                **namespace_after_empty,
                "description": '"""prior"""',
                "schema_runtime_validation": SimpleNamespace(
                    pattern_properties=(),
                    required_groups=(SimpleNamespace(keyword="anyOf", groups=((("any",),),)),),
                    conditional_required=(),
                ),
            },
        ),
        (
            "schema_conditional_without_prior",
            schema_path,
            {
                **namespace_after_empty,
                "schema_runtime_validation": SimpleNamespace(
                    pattern_properties=(),
                    required_groups=(),
                    conditional_required=(SimpleNamespace(condition=(), then_groups=(), else_groups=()),),
                ),
            },
        ),
        (
            "schema_helpers_without_optional_rules",
            *context_for(
                "pydantic_v2/schema_runtime_validation_helpers.jinja2",
                has_pattern_properties=False,
                has_required_groups=False,
                has_conditional_required=False,
            ),
        ),
        (
            "scalar_without_description",
            *context_for("ScalarTypeAliasAnnotation.jinja2", description=None),
        ),
        (
            "scalar_type_alias_without_description",
            *context_for("ScalarTypeAliasType.jinja2", description=None),
        ),
        (
            "scalar_statement_without_description",
            *context_for("ScalarTypeStatement.jinja2", description=None),
        ),
        (
            "type_alias_annotation_without_fields",
            *context_for("TypeAliasAnnotation.jinja2", fields=[]),
        ),
        (
            "type_alias_type_without_fields",
            *context_for("TypeAliasType.jinja2", fields=[]),
        ),
        (
            "type_statement_without_fields",
            *context_for("TypeStatement.jinja2", fields=[]),
        ),
        (
            "typed_dict_class_inline_description",
            *context_for("TypedDictClass.jinja2", fields=[inline_field, _field(name="after_inline")]),
        ),
        (
            "typed_dict_class_empty",
            *context_for("TypedDictClass.jinja2", description=None, fields=[]),
        ),
        (
            "typed_dict_class_without_and_with_last_inline_description",
            *context_for("TypedDictClass.jinja2", fields=[no_description_field, inline_field]),
        ),
        (
            "typed_dict_function_inline_description",
            *context_for("TypedDictFunction.jinja2", all_fields=[inline_field, _field(name="after_inline")]),
        ),
        (
            "typed_dict_function_without_and_with_last_inline_description",
            *context_for(
                "TypedDictFunction.jinja2",
                description=None,
                all_fields=[no_description_field, inline_field],
            ),
        ),
        (
            "union_annotation_single",
            *context_for("UnionTypeAliasAnnotation.jinja2", description=None, fields=[_field(name="Single")]),
        ),
        (
            "union_annotation_empty",
            *context_for("UnionTypeAliasAnnotation.jinja2", fields=[]),
        ),
        (
            "union_type_empty",
            *context_for("UnionTypeAliasType.jinja2", fields=[]),
        ),
        (
            "union_type_single",
            *context_for("UnionTypeAliasType.jinja2", description=None, fields=[_field(name="Single")]),
        ),
        (
            "union_statement_single",
            *context_for("UnionTypeStatement.jinja2", description=None, fields=[_field(name="Single")]),
        ),
        (
            "union_statement_empty",
            *context_for("UnionTypeStatement.jinja2", fields=[]),
        ),
        *alias_cases,
    )


def _render_template_branch_cases(*, compiled: bool) -> str:
    rendered: list[str] = []
    for name, path, context in _template_branch_cases():
        if compiled:
            renderer = get_builtin_renderer(path)
            if renderer is None:  # pragma: no cover - error message aids generated-registry debugging
                pytest.fail(f"missing standalone renderer for {path}")
            output = renderer(**context)
        else:
            output = get_template(path).render(**context)
        rendered.append(f"=== {name}\n{output}\n")
    return "\n".join(rendered)


def _render_runtime_subset(*, compiled: bool) -> str:  # noqa: PLR0914
    from jinja2 import Environment, select_autoescape

    values = {
        "missing": MISSING,
        "none": None,
        "false": False,
        "empty_string": "",
        "empty_collection": [],
        "unicode": 'こんにちは {braces} \\ " quote\nnext',
    }
    items = [SimpleNamespace(kind="one"), SimpleNamespace(kind="two"), SimpleNamespace(kind="one")]
    if compiled:
        indentation = filter_indent("head\n\ntail\n", width="->", first=True, blank=True)
        joined = filter_join(["a", MISSING, "b"], ",")
        listed = filter_list(("a", "b"))
        pretty = filter_pprint({"a": [1, 2]})
        replaced = filter_replace("a\na", "a", "b", 1)
        representation = filter_repr({"brace": "{}", "unicode": "こんにちは"})
        selected = [item.kind for item in filter_selectattr(items, "kind", "equalto", "one")]
        loop_values = [(value, last) for value, last in loop_last_iter(("a", "b"))]
        output = [
            f"{name}|{stringify(filter_default(value, 'fallback'))}|{is_defined(value)}|"
            f"{stringify(filter_default(value, 'boolean', True))}"
            for name, value in values.items()
        ]
        output.extend([
            f"indent={indentation!r}",
            f"join={joined!r}",
            f"length={stringify(filter_length(MISSING))!r}/{stringify(filter_length(items))!r}",
            f"list={stringify(filter_list(MISSING))!r}/{stringify(listed)!r}",
            f"pprint={pretty!r}",
            f"replace={replaced!r}",
            f"repr={representation!r}",
            f"selectattr={stringify(selected)!r}",
            f"loop={''.join(stringify(value) for value in loop_values)!r}",
        ])
        state = namespace(value="first")
        state.value = "second"
        scope_value = Scope({"present": None}).get("present")
        output.extend([
            f"namespace={state.value!r}/{isinstance(state, Namespace)!r}",
            f"scope={scope_value!r}/{Scope().get('absent') is MISSING!r}",
        ])
        return "\n".join(output) + "\n"

    environment = Environment(autoescape=select_autoescape(["html", "xml"], default_for_string=False))
    environment.filters["repr"] = repr
    default_template = environment.from_string(
        "{{ name }}|{{ value|default('fallback') }}|{{ value is defined }}|{{ value|default('boolean', true) }}"
    )
    output = [
        default_template.render(name=name, **({} if value is MISSING else {"value": value}))
        for name, value in values.items()
    ]
    indentation = environment.from_string("{{ value|indent(width=width, first=true, blank=true) }}").render(
        value="head\n\ntail\n",
        width="->",
    )
    joined = environment.from_string("{{ value|join(',') }}").render(value=["a", MISSING, "b"])
    length_missing = environment.from_string("{{ value|length }}").render(value=MISSING)
    length_items = environment.from_string("{{ value|length }}").render(value=items)
    list_missing = environment.from_string("{{ value|list }}").render(value=MISSING)
    listed = environment.from_string("{{ value|list }}").render(value=("a", "b"))
    pretty = environment.from_string("{{ value|pprint }}").render(value={"a": [1, 2]})
    replaced = environment.from_string("{{ value|replace('a', 'b', 1) }}").render(value="a\na")
    representation = environment.from_string("{{ value|repr }}").render(value={"brace": "{}", "unicode": "こんにちは"})
    selected = environment.from_string(
        "{{ values|selectattr('kind', 'equalto', 'one')|map(attribute='kind')|list }}"
    ).render(values=items)
    loop_values = environment.from_string("{% for value in values %}{{ (value, loop.last) }}{% endfor %}").render(
        values=("a", "b")
    )
    namespace_value = environment.from_string(
        "{% set state = namespace(value='first') %}{% set state.value = 'second' %}{{ state.value }}"
    ).render()
    output.extend([
        f"indent={indentation!r}",
        f"join={joined!r}",
        f"length={length_missing!r}/{length_items!r}",
        f"list={list_missing!r}/{listed!r}",
        f"pprint={pretty!r}",
        f"replace={replaced!r}",
        f"repr={representation!r}",
        f"selectattr={selected!r}",
        f"loop={loop_values!r}",
        f"namespace={namespace_value!r}/True",
        "scope=None/True",
    ])
    return "\n".join(output) + "\n"


def _runtime_edge_output() -> str:
    """Exercise standard-library runtime paths not selected by current templates."""
    value = SimpleNamespace(attribute="value")
    nested_scope = Scope({"parent": "value"}).child()
    indent_with_lines = filter_indent("head\ntail")
    indent_without_lines = filter_indent("head")
    return (
        "\n".join((
            f"missing_repr={MISSING!r}",
            f"missing_iter={list(MISSING)!r}",
            f"loop_missing={list(loop_last_iter(MISSING))!r}",
            f"loop_empty={list(loop_last_iter(()))!r}",
            f"attribute={getattr_(value, 'attribute')!r}/{getattr_({'key': 'value'}, 'key')!r}",
            f"attribute_missing={getattr_(value, 'missing') is MISSING!r}/{getattr_(MISSING, 'missing') is MISSING!r}",
            f"item={getitem(('zero',), 0)!r}/{getitem(('zero',), 1) is MISSING!r}",
            f"item_missing={getitem(MISSING, 0) is MISSING!r}",
            f"concat={concat('a', MISSING, 'b')!r}",
            f"scope_parent={nested_scope.get('parent')!r}/{nested_scope.get('missing') is MISSING!r}",
            f"indent_no_blank={indent_with_lines!r}/{indent_without_lines!r}",
            f"join_missing={filter_join(MISSING)!r}",
            f"replace_all={filter_replace('a-a', 'a', 'b')!r}",
            f"select_missing={filter_selectattr(MISSING, 'attribute', 'equalto', 'value')!r}",
        ))
        + "\n"
    )


def test_template_inventory_is_derived_from_all_builtin_sources() -> None:
    """The checked-in inventory records every currently supported template feature."""
    inventory = template_compiler_inventory.inventory_templates(TEMPLATE_DIR)
    template_paths = "\n".join(
        path.relative_to(TEMPLATE_DIR).as_posix()
        for path in template_compiler_inventory.iter_template_paths(TEMPLATE_DIR)
    )
    features = "\n".join(
        f"{field.name}:{f' {values}' if (values := ', '.join(sorted(getattr(inventory, field.name)))) else ''}"
        for field in dataclass_fields(inventory)
    )
    output = (
        f"template count: {len(template_compiler_inventory.iter_template_paths(TEMPLATE_DIR))}\n"
        f"{template_paths}\n\n{features}\n"
    )
    assert_output(
        output,
        EXPECTED_PATH / "inventory.txt",
    )


def test_inventory_rejects_new_features_with_path_line_and_compiler_guidance(tmp_path: Path) -> None:
    """Unsupported syntax must be rejected at generation time rather than miscompiled."""
    template = tmp_path / "mapping_dot.jinja2"
    template.write_text("{% set values = {'key': 'value'} %}\n{{ values.key }}\n", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=(
            r"mapping_dot\.jinja2:2: unsupported Jinja mapping dot access 'key'; "
            "update the standalone template compiler"
        ),
    ):
        template_compiler_inventory.inventory_templates(tmp_path)


def test_inventory_rejects_unknown_filter_with_actionable_diagnostic(tmp_path: Path) -> None:
    """Feature inventory errors identify the exact unsupported filter and source location."""
    template = tmp_path / "unknown_filter.jinja2"
    template.write_text("{{ value|upper }}\n", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=r"unknown_filter\.jinja2:1: unsupported Jinja filter 'upper'; update the standalone template compiler",
    ):
        template_compiler_inventory.inventory_templates(tmp_path)


@pytest.mark.parametrize(
    ("filename", "source", "diagnostic"),
    [
        (
            "scoped_assignment.jinja2",
            "{% if enabled %}{% set value = 'local' %}{% endif %}{{ value }}",
            (
                r"scoped_assignment\.jinja2:1: unsupported Jinja scoped assignment in If 'value'; "
                r"update the standalone template compiler"
            ),
        ),
        (
            "macro_capture.jinja2",
            "{% set prefix = 'P' %}{% macro get_type_hint() %}{{ prefix }}{% endmacro %}{{ get_type_hint() }}",
            (
                r"macro_capture\.jinja2:1: unsupported Jinja macro local capture 'prefix'; "
                r"update the standalone template compiler"
            ),
        ),
        (
            "nested_target.jinja2",
            "{% for (first, second), third in values %}{{ first }}{% endfor %}",
            (
                r"nested_target\.jinja2:1: unsupported Jinja for target 'nested tuple'; "
                r"update the standalone template compiler"
            ),
        ),
        (
            "unknown_mapping.jinja2",
            "{% for record in records %}{{ record.value }}{% endfor %}",
            (
                r"unknown_mapping\.jinja2:1: unsupported Jinja mapping dot access 'value'; "
                r"update the standalone template compiler"
            ),
        ),
    ],
)
def test_inventory_rejects_unsafe_supported_shapes(
    tmp_path: Path,
    filename: str,
    source: str,
    diagnostic: str,
) -> None:
    """Accepted node classes must not permit constructs the compiler would misrender."""
    tmp_path.joinpath(filename).write_text(source, encoding="utf-8")

    with pytest.raises(ValueError, match=diagnostic):
        template_compiler_inventory.inventory_templates(tmp_path)


def test_inventory_rejects_include_capture_of_parent_macro(tmp_path: Path) -> None:
    """Static includes cannot call a macro that exists only in the including module."""
    tmp_path.joinpath("parent.jinja2").write_text(
        "{% macro get_type_hint() %}parent{% endmacro %}{% include 'child.jinja2' %}",
        encoding="utf-8",
    )
    tmp_path.joinpath("child.jinja2").write_text("{{ get_type_hint() }}", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=(
            r"child\.jinja2:1: unsupported Jinja include macro capture 'get_type_hint'; "
            r"update the standalone template compiler"
        ),
    ):
        template_compiler_inventory.inventory_templates(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires elevated privileges")
def test_inventory_rejects_template_source_symlink_outside_root(tmp_path: Path) -> None:
    """Template discovery must not read a source reached through an escaping symlink."""
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    outside_template = tmp_path / "outside.jinja2"
    outside_template.write_text("outside\n", encoding="utf-8")
    template_dir.joinpath("linked.jinja2").symlink_to(outside_template)

    with pytest.raises(
        ValueError,
        match=(
            r"linked\.jinja2: template source resolves outside the template root; "
            r"update the standalone template compiler or move the source under the template directory"
        ),
    ):
        template_compiler_inventory.inventory_templates(template_dir)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires elevated privileges")
def test_inventory_accepts_template_source_symlink_within_root(tmp_path: Path) -> None:
    """A source alias remains valid when its resolved target stays inside the template root."""
    source = tmp_path / "source.jinja2"
    source.write_text("safe\n", encoding="utf-8")
    tmp_path.joinpath("linked.jinja2").symlink_to(source)

    template_compiler_inventory.inventory_templates(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires elevated privileges")
def test_inventory_rejects_broken_template_source_symlink(tmp_path: Path) -> None:
    """Broken source aliases fail with an actionable compiler diagnostic."""
    tmp_path.joinpath("broken.jinja2").symlink_to(tmp_path / "missing.jinja2")

    with pytest.raises(
        ValueError,
        match=(
            r"broken\.jinja2: template source does not exist; "
            r"update the standalone template compiler or remove the broken template path"
        ),
    ):
        template_compiler_inventory.inventory_templates(tmp_path)


@pytest.mark.parametrize(
    ("error", "expected_name"),
    [
        pytest.param(
            PermissionError(13, "permission denied"),
            "template_source_permission_error.txt",
            id="os-error",
        ),
        pytest.param(
            RuntimeError("symlink loop"),
            "template_source_runtime_error.txt",
            id="runtime-error",
        ),
    ],
)
def test_inventory_normalizes_template_source_resolution_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error: OSError | RuntimeError,
    expected_name: str,
) -> None:
    """Resolution failures retain the source location and compiler guidance."""
    tmp_path.joinpath("template.jinja2").write_text("safe\n", encoding="utf-8")

    def fail_resolve(*_: Any, **__: Any) -> Path:
        raise error

    with monkeypatch.context() as path_patch:
        path_patch.setattr(Path, "resolve", fail_resolve)
        with pytest.raises(
            ValueError,
            match=r"template\.jinja2: template source cannot be resolved",
        ) as exc_info:
            template_compiler_inventory.inventory_templates(tmp_path)
    assert_output(f"{exc_info.value}\n", EXPECTED_PATH / expected_name)


def test_inventory_rejects_template_source_directory(tmp_path: Path) -> None:
    """A directory whose name matches the source suffix is not a template file."""
    tmp_path.joinpath("directory.jinja2").mkdir()

    with pytest.raises(
        ValueError,
        match=(
            r"directory\.jinja2: template source is not a file; "
            r"update the standalone template compiler or remove the invalid template path"
        ),
    ):
        template_compiler_inventory.inventory_templates(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires elevated privileges")
def test_template_source_read_keeps_the_verified_descriptor_after_symlink_swap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A swap after validation cannot redirect the actual template read outside the root."""
    template = tmp_path / "template.jinja2"
    template.write_text("safe\n", encoding="utf-8")
    outside_template = tmp_path / "outside.jinja2"
    outside_template.write_text("outside\n", encoding="utf-8")
    original_samestat = os.path.samestat
    swapped = False

    def swap_after_validation(first: os.stat_result, second: os.stat_result) -> bool:
        nonlocal swapped
        template.unlink()
        template.symlink_to(outside_template)
        swapped = True
        return original_samestat(first, second)

    monkeypatch.setattr(os.path, "samestat", swap_after_validation)

    content = template_compiler_inventory._read_template_source(tmp_path, template)
    assert_output(
        f"content: {content!r}\nswapped: {swapped}\n",
        EXPECTED_PATH / "template_source_verified_descriptor.txt",
    )


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires elevated privileges")
def test_template_source_read_rejects_symlink_swapped_after_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A swap between containment validation and open is rejected before reading."""
    template = tmp_path / "template.jinja2"
    template.write_text("safe\n", encoding="utf-8")
    outside_template = tmp_path / "outside.jinja2"
    outside_template.write_text("outside\n", encoding="utf-8")
    original_resolver = template_compiler_inventory._resolve_template_path
    swapped = False

    def validate_then_swap(template_dir: Path, path: Path) -> Path:
        nonlocal swapped
        resolved_path = original_resolver(template_dir, path)
        template.unlink()
        template.symlink_to(outside_template)
        swapped = True
        return resolved_path

    monkeypatch.setattr(template_compiler_inventory, "_resolve_template_path", validate_then_swap)

    with pytest.raises(ValueError, match=r"template source changed during validation; retry"):
        template_compiler_inventory._read_template_source(tmp_path, template)
    assert_output(f"swapped: {swapped}\n", EXPECTED_PATH / "template_source_swap_rejected.txt")


def test_compile_template_rejects_static_include_outside_template_root(tmp_path: Path) -> None:
    """Static include escapes use the same guarded source-path diagnostic."""
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    template_dir.joinpath("parent.jinja2").write_text("{% include '../outside.jinja2' %}", encoding="utf-8")
    tmp_path.joinpath("outside.jinja2").write_text("outside\n", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=(
            r"parent\.jinja2:1: invalid static include '\.\./outside\.jinja2': "
            r"\.\./outside\.jinja2: template source resolves outside the template root; "
            r"update the standalone template compiler or move the source under the template directory"
        ),
    ):
        compile_template(template_dir, Path("parent.jinja2"), build_environment())


def test_compiler_rejects_generated_module_name_collisions() -> None:
    """Different template paths cannot silently overwrite one generated module."""
    from scripts.compile_builtin_templates import _validate_module_names

    with pytest.raises(
        ValueError,
        match=(
            r"template module name 'registry' for 'registry\.jinja2' is reserved for generated package code; "
            r".*update the standalone template compiler"
        ),
    ):
        _validate_module_names((Path("registry.jinja2"),))

    with pytest.raises(
        ValueError,
        match=(
            r"template module name collision: 'nested/foo-bar\.jinja2' and 'nested/foo_bar\.jinja2' "
            r"both map to 'nested_foo_bar'; .*update the standalone template compiler"
        ),
    ):
        _validate_module_names((Path("nested/foo-bar.jinja2"), Path("nested/foo_bar.jinja2")))


def test_compile_check_detects_missing_and_stale_generated_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """--check reports each missing or stale artifact without writing it."""
    from scripts import compile_builtin_templates

    generated_dir = tmp_path / "generated"
    monkeypatch.setattr(compile_builtin_templates, "ROOT", tmp_path)
    monkeypatch.setattr(compile_builtin_templates, "OUTPUT_DIR", generated_dir)
    monkeypatch.setattr(compile_builtin_templates, "FASTAPI_OUTPUT_DIR", tmp_path / "fastapi")
    sources = compile_builtin_templates.generated_sources()
    for path, source in sources.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    stale_path = generated_dir / "enum.py"
    stale_path.write_text("# stale\n", encoding="utf-8")
    missing_path = generated_dir / "msgspec.py"
    missing_path.unlink()
    orphan_path = generated_dir / "orphan.py"
    orphan_path.write_text("# orphan\n", encoding="utf-8")

    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        status = compile_builtin_templates.main(["--check"])

    assert_output(
        f"status: {status}\n{stderr.getvalue()}",
        EXPECTED_PATH / "compile_check_stale.txt",
    )

    generation_status = compile_builtin_templates.main([])
    check_status = compile_builtin_templates.main(["--check"])
    assert_output(
        "".join([
            f"generation status: {generation_status}\n",
            f"check status: {check_status}\n",
            f"orphan exists: {orphan_path.exists()}\n",
        ]),
        EXPECTED_PATH / "compile_generation_cleanup.txt",
    )


def test_compile_check_matches_checked_in_generated_artifacts() -> None:
    """The public generation command leaves the working tree untouched when current."""
    from scripts import compile_builtin_templates

    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        status = compile_builtin_templates.main(["--check"])

    assert_output(f"status: {status}\n{stderr.getvalue()}", EXPECTED_PATH / "compile_check_current.txt")


def test_every_built_in_template_matches_its_jinja_oracle() -> None:
    """Every generated root/include/macro renderer remains byte-for-byte Jinja-compatible."""
    expected = EXPECTED_PATH / "all_templates_parity.txt"
    assert_output(_render_all_builtin_templates(compiled=False), expected)
    assert_output(_render_all_builtin_templates(compiled=True), expected)


def test_branch_variants_match_jinja_for_models_macros_and_namespace_state() -> None:
    """Optional contexts and output branches retain their exact Jinja whitespace and values."""
    expected = EXPECTED_PATH / "branch_variants_parity.txt"
    assert_output(_render_template_branch_cases(compiled=False), expected)
    assert_output(_render_template_branch_cases(compiled=True), expected)


def test_runtime_subset_matches_jinja_for_missing_filters_loops_and_namespace() -> None:
    """The compact standalone runtime preserves exactly the built-in Jinja subset."""
    expected = EXPECTED_PATH / "runtime_subset_parity.txt"
    assert_output(_render_runtime_subset(compiled=False), expected)
    assert_output(_render_runtime_subset(compiled=True), expected)


def test_runtime_edge_and_error_paths_remain_explicit() -> None:
    """Unsupported runtime operations fail loudly and non-template helpers stay deterministic."""
    assert_output(_runtime_edge_output(), EXPECTED_PATH / "runtime_edges.txt")

    with pytest.raises(TypeError, match="cannot assign attribute on a non-namespace object"):
        setattr_("not a namespace", "value", "new")
    with pytest.raises(ValueError, match="supports selectattr"):
        filter_selectattr([], "attribute")


def test_model_rendering_uses_generated_renderers_with_real_model_objects() -> None:
    """Real Pydantic and dataclass models keep their exact pre-formatter Jinja output."""
    pydantic_reference = _reference("PydanticModel")
    pydantic_model = BaseModel(
        fields=[
            PydanticField(name="first", data_type=DataType(type="str"), required=True),
            PydanticField(name="second", data_type=DataType(type="int"), required=False, default=1),
        ],
        reference=pydantic_reference,
        description="A unicode こんにちは description.",
        extra_template_data=defaultdict(
            dict,
            {
                pydantic_reference.path: {
                    "unused_extension_data": {"still": "accepted"},
                }
            },
        ),
    )
    pydantic_model.methods.append('def generated(self) -> str:\n        return "ok"')
    pydantic_model._set_internal_template_data("class_body_lines", ["marker = {'braces': '{}'}"])
    dataclass_reference = _reference("DataclassModel")
    dataclass_model = DataClass(
        fields=[
            DataclassField(name="value", data_type=DataType(type="str"), required=True),
            DataclassField(
                name="standard_default",
                data_type=DataType(type="str"),
                default="fallback",
                required=False,
            ),
        ],
        reference=dataclass_reference,
        description="Dataclass description.",
        frozen=True,
    )
    pydantic_dataclass_model = PydanticDataclass(
        fields=[
            PydanticDataclassField(
                name="pydantic_default",
                data_type=DataType(type="str"),
                default="fallback",
                required=False,
            ),
            PydanticDataclassField(
                name="items",
                data_type=DataType(type="list[str]"),
                required=False,
                extras={"default_factory": "list"},
            ),
        ],
        reference=_reference("PydanticDataclassModel"),
    )
    root_model = RootModel(
        fields=[
            DataModelFieldBase(
                name="root",
                data_type=DataType(type="str"),
                default="fallback",
                required=False,
            )
        ],
        reference=_reference("RootDefault"),
    )
    root_model_type_alias = RootModelTypeAlias(
        fields=[DataModelFieldBase(name="root", data_type=DataType(type="str"), required=True)],
        reference=_reference("RootAlias"),
    )

    models = (pydantic_model, dataclass_model, pydantic_dataclass_model, root_model, root_model_type_alias)
    generated = "\n\n".join(model.render() for model in models) + "\n"
    jinja = (
        "\n\n".join(get_template(model.template_file_path).render(**_model_context(model)) for model in models) + "\n"
    )
    expected = EXPECTED_PATH / "real_model_parity.txt"
    assert_output(jinja, expected)
    assert_output(generated, expected)


def test_template_base_extension_seams_and_unknown_templates_stay_on_jinja(tmp_path: Path) -> None:
    """Direct, external, and unknown template consumers retain the original Jinja path."""

    class DirectTemplate(TemplateBase):
        @cached_property
        def template_file_path(self) -> Path:
            return Path("ScalarTypeAliasAnnotation.jinja2")

        def render(self) -> str:
            return self._render(class_name="Direct", py_type="str", description=None)

    class ExplicitRenderOverride(BaseModel):
        def _render(self, *args: Any, **kwargs: Any) -> str:  # noqa: ARG002
            return f"external override: {kwargs['class_name']}"

    class ExternalJinjaConsumer(BaseModel):
        pass

    class ProjectOwnedUnknownTemplate(BaseModel):
        pass

    ProjectOwnedUnknownTemplate.__module__ = "datamodel_code_generator.model.extension"
    template_path = tmp_path / "unknown.jinja2"
    template_path.write_text("unknown {{ class_name }}: {{ future_extension }}\n", encoding="utf-8")
    ProjectOwnedUnknownTemplate.TEMPLATE_FILE_PATH = str(template_path)
    absolute_template_path = tmp_path / "absolute.jinja2"
    absolute_template_path.write_text(
        "absolute {{ class_name }}: {{ class_body_lines | join(', ') }}\n",
        encoding="utf-8",
    )

    class ExternalAbsoluteTemplate(BaseModel):
        TEMPLATE_FILE_PATH = str(absolute_template_path)

    custom_template_dir = tmp_path / "custom" / "pydantic_v2"
    custom_template_dir.mkdir(parents=True)
    custom_template_dir.joinpath("BaseModel.jinja2").write_text(
        "custom {{ class_name }}: {{ future_extension }}\n",
        encoding="utf-8",
    )

    external = ExplicitRenderOverride(fields=[], reference=_reference("External"))
    external_jinja = ExternalJinjaConsumer(fields=[], reference=_reference("ExternalJinja"))
    unknown_reference = _reference("Unknown")
    unknown = ProjectOwnedUnknownTemplate(
        fields=[],
        reference=unknown_reference,
        extra_template_data=defaultdict(dict, {unknown_reference.path: {"future_extension": "accepted"}}),
    )
    pydantic_reference = _reference("Positional")
    positional = BaseModel(
        fields=[],
        reference=pydantic_reference,
        extra_template_data=defaultdict(dict, {pydantic_reference.path: {"future_extension": "accepted"}}),
    )
    positional_context = _model_context(positional)
    custom_reference = _reference("Custom")
    custom = BaseModel(
        fields=[],
        reference=custom_reference,
        custom_template_dir=custom_template_dir.parent,
        extra_template_data=defaultdict(dict, {custom_reference.path: {"future_extension": "accepted"}}),
    )
    absolute_reference = _reference("Absolute")
    absolute = ExternalAbsoluteTemplate(
        fields=[],
        reference=absolute_reference,
        extra_template_data=defaultdict(dict, {absolute_reference.path: {"class_body_lines": ["raw template data"]}}),
    )
    output = "\n".join((
        DirectTemplate().render(),
        external.render(),
        external_jinja.render(),
        unknown.render(),
        custom.render(),
        absolute.render(),
        positional._render(positional_context),
    ))
    assert_output(output + "\n", EXPECTED_PATH / "extension_seams.txt")


def test_external_base_model_module_helper_stays_on_jinja(monkeypatch: pytest.MonkeyPatch) -> None:
    """External subclasses retain Jinja when inheriting the module helper renderer."""

    class ExternalBaseModel(BaseModel):
        pass

    reference = _reference("ExternalRuntimeModel")
    model = ExternalBaseModel(
        fields=[],
        reference=reference,
        extra_template_data=defaultdict(
            dict,
            {
                reference.path: {
                    "schema_runtime_validation": _runtime_validation(),
                    "schema_runtime_validation_enabled": True,
                }
            },
        ),
    )
    jinja = get_template(Path(ExternalBaseModel.SCHEMA_RUNTIME_VALIDATION_HELPERS_TEMPLATE_FILE_PATH)).render(
        schema_runtime_validation_base_class_name=ExternalBaseModel.SCHEMA_RUNTIME_VALIDATION_BASE_CLASS_NAME,
        has_pattern_properties=False,
        has_required_groups=True,
        has_conditional_required=False,
    )

    from datamodel_code_generator.model import _compiled_templates

    compiled_lookup = Mock(side_effect=AssertionError("external BaseModel subclass attempted compiled template lookup"))
    monkeypatch.setattr(_compiled_templates, "get_builtin_renderer", compiled_lookup)
    rendered = ExternalBaseModel.render_module_code([model])
    compiled_lookup.assert_not_called()

    expected = EXPECTED_PATH / "module_helper_parity.txt"
    assert_output(f"{jinja!r}\n", expected)
    assert_output(f"{rendered!r}\n", expected)


def test_module_runtime_validation_helper_uses_generated_or_custom_jinja_renderer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Module helpers bypass Jinja only when no model has a custom template directory."""
    reference = _reference("RuntimeModel")
    model = BaseModel(
        fields=[],
        reference=reference,
        extra_template_data=defaultdict(
            dict,
            {
                reference.path: {
                    "schema_runtime_validation": _runtime_validation(),
                    "schema_runtime_validation_enabled": True,
                }
            },
        ),
    )
    generated = BaseModel.render_module_code([model])
    jinja = get_template(Path(BaseModel.SCHEMA_RUNTIME_VALIDATION_HELPERS_TEMPLATE_FILE_PATH)).render(
        schema_runtime_validation_base_class_name=BaseModel.SCHEMA_RUNTIME_VALIDATION_BASE_CLASS_NAME,
        has_pattern_properties=False,
        has_required_groups=True,
        has_conditional_required=False,
    )
    expected = EXPECTED_PATH / "module_helper_parity.txt"
    assert_output(f"{jinja!r}\n", expected)
    assert_output(f"{generated!r}\n", expected)

    from datamodel_code_generator.model import _compiled_templates

    with monkeypatch.context() as patch_context:
        patch_context.setattr(_compiled_templates, "get_builtin_renderer", lambda _: None)
        fallback = BaseModel.render_module_code([model])
    assert_output(f"{fallback!r}\n", expected)

    custom_dir = tmp_path / "pydantic_v2"
    custom_dir.mkdir()
    custom_dir.joinpath("schema_runtime_validation_helpers.jinja2").write_text(
        "custom helper {{ has_required_groups }}\n",
        encoding="utf-8",
    )
    custom_reference = _reference("CustomRuntimeModel")
    custom_model = BaseModel(
        fields=[],
        reference=custom_reference,
        custom_template_dir=tmp_path,
        extra_template_data=defaultdict(
            dict,
            {
                custom_reference.path: {
                    "schema_runtime_validation": _runtime_validation(),
                    "schema_runtime_validation_enabled": True,
                }
            },
        ),
    )
    custom = BaseModel.render_module_code([custom_model])
    assert_output(f"{custom}\n", EXPECTED_PATH / "module_helper_custom_template.txt")


def test_module_property_count_runtime_validation_helper_uses_custom_jinja_renderer(tmp_path: Path) -> None:
    """Keep property-only runtime validation compatible with a custom helper template."""
    custom_dir = tmp_path / "pydantic_v2"
    custom_dir.mkdir()
    custom_dir.joinpath("schema_runtime_validation_helpers.jinja2").write_text(
        "class {{ schema_runtime_validation_base_class_name }}(BaseModel):\n    custom_helper = True\n",
        encoding="utf-8",
    )
    property_count_reference = _reference("PropertyCountRuntimeModel")
    property_count_model = BaseModel(
        fields=[],
        reference=property_count_reference,
        custom_template_dir=tmp_path,
        extra_template_data=defaultdict(
            dict,
            {
                property_count_reference.path: {
                    "schema_runtime_validation": _property_count_runtime_validation(),
                    "schema_runtime_validation_enabled": True,
                }
            },
        ),
    )
    property_count_custom = BaseModel.render_module_code([property_count_model])
    assert_output(
        property_count_custom,
        EXPECTED_PATH / "module_helper_property_count_custom_template.txt",
    )


def test_module_unique_items_runtime_validation_requires_supported_custom_helper(tmp_path: Path) -> None:
    """Fail clearly instead of emitting a custom helper that silently skips uniqueItems."""
    no_override_reference = _reference("NoOverrideUniqueItemsRuntimeModel")
    no_override_model = BaseModel(
        fields=[],
        reference=no_override_reference,
        custom_template_dir=tmp_path,
        extra_template_data=defaultdict(
            dict,
            {
                no_override_reference.path: {
                    "schema_runtime_validation": _make_internal_schema_runtime_validation(
                        unique_items=[UniqueItemsRule(path=())]
                    ),
                    "schema_runtime_validation_enabled": True,
                }
            },
        ),
    )
    assert_output(
        f"{BaseModel.SCHEMA_RUNTIME_VALIDATION_BASE_CLASS_NAME in BaseModel.render_module_code([no_override_model])}\n",
        EXPECTED_PATH / "module_helper_unique_items_no_custom_override.txt",
    )

    custom_dir = tmp_path / "pydantic_v2"
    custom_dir.mkdir()
    custom_dir.joinpath("schema_runtime_validation_helpers.jinja2").write_text(
        "class {{ schema_runtime_validation_base_class_name }}(BaseModel):\n    custom_helper = True\n",
        encoding="utf-8",
    )
    reference = _reference("UniqueItemsRuntimeModel")
    model = BaseModel(
        fields=[],
        reference=reference,
        custom_template_dir=tmp_path,
        extra_template_data=defaultdict(
            dict,
            {
                reference.path: {
                    "schema_runtime_validation": _make_internal_schema_runtime_validation(
                        unique_items=[UniqueItemsRule(path=())]
                    ),
                    "schema_runtime_validation_enabled": True,
                }
            },
        ),
    )

    with pytest.raises(Error, match="overrides do not yet support generated uniqueItems"):
        BaseModel.render_module_code([model])


def test_module_runtime_validation_helper_gates() -> None:
    """Do not render helper code when runtime validation is unavailable or disabled."""
    disabled_model = BaseModel(fields=[], reference=_reference("DisabledRuntimeModel"))
    enabled_without_runtime_reference = _reference("EnabledWithoutRuntimeModel")
    enabled_without_runtime_model = BaseModel(
        fields=[],
        reference=enabled_without_runtime_reference,
        extra_template_data=defaultdict(
            dict,
            {enabled_without_runtime_reference.path: {"schema_runtime_validation_enabled": True}},
        ),
    )
    gates = "\n".join((
        repr(BaseModel.render_module_code([])),
        repr(BaseModel.render_module_code([disabled_model])),
        repr(BaseModel.render_module_code([enabled_without_runtime_model])),
    ))
    assert_output(gates + "\n", EXPECTED_PATH / "module_helper_gates.txt")
