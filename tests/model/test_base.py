"""Tests for base model classes and utilities."""

from __future__ import annotations

import ast
import os
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from errno import ENOENT
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from unittest.mock import Mock

import pytest
from typing_extensions import TypedDict, Unpack

from datamodel_code_generator import Error
from datamodel_code_generator._format_types import PythonVersion
from datamodel_code_generator.imports import (
    IMPORT_ANNOTATED,
    IMPORT_ANY,
    IMPORT_DECIMAL,
    IMPORT_DICT,
    IMPORT_OPTIONAL,
    IMPORT_UNION,
    Import,
)
from datamodel_code_generator.model.base import (
    _MAX_CUSTOM_TEMPLATE_SIGNATURES,
    _MAX_MISSING_CUSTOM_TEMPLATE_SUBDIRS,
    UNDEFINED,
    DataModel,
    DataModelFieldBase,
    TemplateBase,
    _annotation_typing_import_names,
    _clear_custom_template_caches,
    _get_custom_template_signature,
    _get_environment,
    _get_environment_with_absolute_path,
    _get_template_with_absolute_path,
    _get_template_with_custom_dir,
    _missing_custom_template_state,
    _refresh_custom_template_paths,
    _remember_missing_custom_template_subdir,
    _RenderedDataModelField,
    _safe_dataclass_arguments,
    _safe_extra_template_data,
    _TypingImportRequirements,
    comment_safe,
    escape_docstring,
    format_docstring,
    get_effective_fields,
    get_module_path,
    get_template,
    inline_comment_safe,
    sanitize_module_name,
)
from datamodel_code_generator.model.dataclass import DataClass as DataclassModel
from datamodel_code_generator.model.imports import (
    IMPORT_MSGSPEC_CONVERT,
    IMPORT_MSGSPEC_FIELD,
    IMPORT_MSGSPEC_META,
    IMPORT_MSGSPEC_UNSET,
    IMPORT_MSGSPEC_UNSETTYPE,
)
from datamodel_code_generator.model.msgspec import DataModelField as MsgspecDataModelField
from datamodel_code_generator.model.msgspec import DataTypeManager as MsgspecDataTypeManager
from datamodel_code_generator.model.msgspec import Struct as MsgspecStruct
from datamodel_code_generator.model.msgspec import import_extender
from datamodel_code_generator.model.pydantic_base import DataModelField as PydanticBaseDataModelField
from datamodel_code_generator.model.pydantic_v2 import BaseModel
from datamodel_code_generator.model.pydantic_v2 import DataModelField as PydanticV2DataModelField
from datamodel_code_generator.model.pydantic_v2.base_model import (
    Constraints as PydanticV2Constraints,
)
from datamodel_code_generator.model.pydantic_v2.base_model import (
    _safe_config_dict_items,
    _strip_legacy_pydantic_extra_post_class_assignment,
)
from datamodel_code_generator.model.pydantic_v2.dataclass import DataClass as PydanticDataclassModel
from datamodel_code_generator.model.pydantic_v2.dataclass import DataModelField as PydanticDataclassField
from datamodel_code_generator.model.pydantic_v2.imports import IMPORT_CONSTR, IMPORT_FIELD, IMPORT_MISSING
from datamodel_code_generator.model.runtime_validation import (
    RequiredGroupsRule,
    SchemaRuntimeValidation,
    _InternalSchemaRuntimeValidation,
)
from datamodel_code_generator.model.scalar import DataTypeScalarTypeBackport
from datamodel_code_generator.model.typed_dict import DataModelField as TypedDictDataModelField
from datamodel_code_generator.model.typed_dict import TypedDict as TypedDictModel
from datamodel_code_generator.python_literal import (
    PythonCode,
    _InternalTypeExpression,
    _make_internal_type_expression,
    is_safe_public_type_name,
)
from datamodel_code_generator.reference import Reference
from datamodel_code_generator.types import ANY, NONE, DataType, Types
from tests.conftest import assert_output

_SYMLINK_CREATION_SKIP = pytest.mark.skipif(os.name == "nt", reason="symlink creation requires elevated privileges")


class A(TemplateBase):
    """Test helper class for TemplateBase testing."""

    def __init__(self, path: Path) -> None:
        """Initialize with template file path."""
        self._path = path

    @property
    def template_file_path(self) -> Path:
        """Return the template file path."""
        return self._path

    def render(self) -> str:
        """Render the template."""
        return ""


class B(DataModel):
    """Test helper class for DataModel testing with template path."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: D107
        super().__init__(*args, **kwargs)

    TEMPLATE_FILE_PATH = ""


class C(DataModel):
    """Test helper class for DataModel testing without template path."""


@dataclass
class ReferenceSource:
    """Test helper for reference source nullability."""

    nullable: bool
    is_alias: bool = False


def test_get_effective_fields_without_base_classes() -> None:
    """Return only named fields directly when inheritance resolution is unnecessary."""
    named_field = DataModelFieldBase(name="value", data_type=DataType(type="str"))
    model = DataclassModel(
        reference=Reference(path="Model", name="Model"),
        fields=[
            named_field,
            DataModelFieldBase(name=None, data_type=DataType(type="str")),
        ],
    )

    assert get_effective_fields(model) == (named_field,)

    inherited_model = DataclassModel(
        reference=Reference(path="InheritedModel", name="InheritedModel"),
        fields=[],
        base_classes=[model.reference],
    )

    assert get_effective_fields(inherited_model) == (named_field,)


@pytest.mark.parametrize(
    ("model_type", "supports_inherited_enum"),
    [
        pytest.param(DataModel, False, id="base"),
        pytest.param(BaseModel, True, id="pydantic"),
        pytest.param(DataclassModel, True, id="dataclass"),
        pytest.param(MsgspecStruct, True, id="msgspec"),
        pytest.param(PydanticDataclassModel, False, id="pydantic-dataclass"),
        pytest.param(TypedDictModel, False, id="typed-dict"),
    ],
)
def test_inherited_discriminator_enum_capability(
    model_type: type[DataModel],
    *,
    supports_inherited_enum: bool,
) -> None:
    """Output models explicitly declare inherited discriminator enum support."""
    assert model_type.SUPPORTS_INHERITED_DISCRIMINATOR_ENUM is supports_inherited_enum
    external_model_type = type(f"External{model_type.__name__}", (model_type,), {})
    assert external_model_type.SUPPORTS_INHERITED_DISCRIMINATOR_ENUM is supports_inherited_enum


@pytest.mark.parametrize(
    ("field_type", "supports_discriminator"),
    [
        pytest.param(DataModelFieldBase, False, id="base"),
        pytest.param(PydanticV2DataModelField, True, id="pydantic"),
        pytest.param(PydanticDataclassField, True, id="pydantic-dataclass"),
        pytest.param(MsgspecDataModelField, False, id="msgspec"),
        pytest.param(TypedDictDataModelField, False, id="typed-dict"),
    ],
)
def test_discriminator_field_capability(
    field_type: type[DataModelFieldBase],
    *,
    supports_discriminator: bool,
) -> None:
    """Only Pydantic fields opt in to Pydantic discriminator behavior."""
    assert field_type.SUPPORTS_DISCRIMINATOR is supports_discriminator
    external_field_type = type(f"External{field_type.__name__}", (field_type,), {})
    assert external_field_type.SUPPORTS_DISCRIMINATOR is supports_discriminator


def test_msgspec_apply_discriminator_tag() -> None:
    """The msgspec model owns its tagged-union mutation policy."""
    field = MsgspecDataModelField(name="kind", data_type=DataType(literals=["pet"]))
    model = MsgspecStruct(
        fields=[field],
        reference=Reference(path="Pet", original_name="Pet", name="Pet"),
    )

    model.apply_discriminator_tag(field, "kind", "pet")

    assert model.extra_template_data["base_class_kwargs"] == {
        "tag_field": "'kind'",
        "tag": "'pet'",
    }
    assert field.extras["is_classvar"] is True


def test_msgspec_custom_template_data_keeps_raw_options() -> None:
    """Trusted custom templates continue to receive their raw msgspec options."""
    reference = Reference(path="Model", original_name="Model", name="Model")
    extra_template_data = defaultdict(dict, {"Model": {"base_class_kwargs": {"tag": "raw"}}})
    model = MsgspecStruct(fields=[], reference=reference, extra_template_data=extra_template_data)
    model.add_base_class_kwarg("kw_only", "True")

    assert model._custom_template_data()["base_class_kwargs"] == {"tag": "raw", "kw_only": "True"}

    empty_model = MsgspecStruct(
        fields=[],
        reference=Reference(path="Empty", original_name="Empty", name="Empty"),
        extra_template_data=defaultdict(dict, {"Empty": {"base_class_kwargs": "invalid"}}),
    )
    assert empty_model._custom_template_data()["base_class_kwargs"] == "invalid"

    keyword_only_model = MsgspecStruct(
        fields=[],
        reference=Reference(path="KeywordOnly", original_name="KeywordOnly", name="KeywordOnly"),
        extra_template_data=defaultdict(dict, {"KeywordOnly": {"base_class_kwargs": "invalid"}}),
        keyword_only=True,
    )
    assert keyword_only_model.has_keyword_only_definition() is True
    assert "class KeywordOnly(Struct, kw_only=True):" in keyword_only_model.render()


def test_builtin_pydantic_config_literals_are_safe() -> None:
    """Built-in ConfigDict output only uses serialized extension data."""

    class StringOnly:
        def __str__(self) -> str:
            return "config object"

    config_items = _safe_config_dict_items({
        "alias_generator": "to_camel",
        "legacy": "True",
        "quoted": "'quoted'",
        "number": "'1'",
        "broken": "'broken\\'",
        "nested": [{"value": "safe"}, ("one",)],
        "fallback": StringOnly(),
        "regex_engine": '"python-re"',
        "not-valid": True,
        "class": True,
    })

    assert config_items == [
        ("alias_generator", "to_camel"),
        ("legacy", "True"),
        ("quoted", "'quoted'"),
        ("number", "'1'"),
        ("broken", "\"'broken\\\\'\""),
        ("nested", "[{'value': 'safe'}, ('one',)]"),
        ("fallback", "'config object'"),
        ("regex_engine", '"python-re"'),
    ]


def test_builtin_renderers_normalize_hostile_public_string_subclasses() -> None:
    """Built-in Python syntax never trusts overridable methods on ``str`` subclasses."""

    class HostileString(str):  # noqa: FURB189, SLOT000 - intentionally exercises hostile string subclasses
        def __str__(self) -> str:  # pragma: no cover - the renderer must bypass this override
            return "__import__('os').system('marker')"

        def split(
            self, *_: object, **__: object
        ) -> list[str]:  # pragma: no cover - the renderer must bypass this override
            return ["str"]

    class HostileComment(HostileString):
        def replace(self, *_: object, **__: object) -> str:  # pragma: no cover - the renderer must bypass this override
            return self

    class HostileDocstring(HostileString):
        def strip(self, *_: object, **__: object) -> str:  # pragma: no cover - the renderer must bypass this override
            return self

        def replace(self, *_: object, **__: object) -> str:  # pragma: no cover - the renderer must bypass this override
            return self

        def __contains__(self, _: object) -> bool:  # pragma: no cover - the renderer must bypass this override
            return False

    class EvilInt(int):
        def __repr__(self) -> str:  # pragma: no cover - the renderer must bypass this override
            return marker

    class EvilFloat(float):
        def __repr__(self) -> str:  # pragma: no cover - the renderer must bypass this override
            return marker

    marker = "__import__('os').system('marker')"
    scalar = DataTypeScalarTypeBackport(
        fields=[],
        reference=Reference(path="Evil", original_name="Evil", name="Evil"),
        extra_template_data=defaultdict(dict, {"Evil": {"py_type": HostileString(marker)}}),
    )
    typed_dict = TypedDictModel(
        fields=[],
        reference=Reference(path="Typed", original_name="Typed", name="Typed"),
        extra_template_data=defaultdict(dict, {"Typed": {"additionalPropertiesType": HostileString(marker)}}),
    )
    safe_typed_dict = TypedDictModel(
        fields=[],
        reference=Reference(path="SafeTyped", original_name="SafeTyped", name="SafeTyped"),
        extra_template_data=defaultdict(dict, {"SafeTyped": {"additionalPropertiesType": HostileString("str")}}),
    )
    pydantic_dataclass = PydanticDataclassModel(
        fields=[],
        reference=Reference(path="Data", original_name="Data", name="Data"),
        extra_template_data=defaultdict(
            dict,
            {
                "Data": {
                    "config": {
                        "alias_generator": HostileString("to_camel"),
                        "json_schema_extra": {"integer": EvilInt(1), "floating": EvilFloat(1.5)},
                    }
                }
            },
        ),
    )
    msgspec = MsgspecStruct(
        fields=[],
        reference=Reference(path="Struct", original_name="Struct", name="Struct"),
        extra_template_data=defaultdict(
            dict,
            {"Struct": {"base_class_kwargs": {1: "ignored", "class": "ignored", "tag": EvilInt(1)}}},
        ),
    )
    base_model = BaseModel(
        fields=[],
        reference=Reference(path="Model", original_name="Model", name="Model"),
        description=HostileDocstring(f'"""\n{marker}'),
        extra_template_data=defaultdict(dict, {"Model": {"comment": HostileComment(f"safe\n{marker}")}}),
    )
    field_description_model = BaseModel(
        fields=[
            PydanticV2DataModelField(
                name="value",
                data_type=DataType(type="str"),
                required=True,
                description=HostileDocstring(f'"""\n{marker}'),
                use_inline_field_description=True,
            )
        ],
        reference=Reference(path="WithField", original_name="WithField", name="WithField"),
    )

    scalar_rendered = scalar.render()
    typed_dict_rendered = typed_dict.render()
    safe_typed_dict_rendered = safe_typed_dict.render()
    pydantic_dataclass_rendered = pydantic_dataclass.render()
    msgspec_rendered = msgspec.render()
    base_model_rendered = base_model.render()
    field_description_rendered = field_description_model.render()

    assert typed_dict._internal_template_data["typed_dict_kwargs"] == {"extra_items": repr(marker)}
    assert safe_typed_dict._internal_template_data["typed_dict_kwargs"] == {"extra_items": "str"}
    assert "alias_generator=to_camel" in pydantic_dataclass_rendered
    assert "'integer': 1" in pydantic_dataclass_rendered
    assert "tag=1" in msgspec_rendered
    assert "# " + marker in base_model_rendered
    for rendered in (
        scalar_rendered,
        typed_dict_rendered,
        safe_typed_dict_rendered,
        pydantic_dataclass_rendered,
        msgspec_rendered,
        base_model_rendered,
        field_description_rendered,
    ):
        assert not any(isinstance(node, ast.Name) and node.id == "__import__" for node in ast.walk(ast.parse(rendered)))


def test_builtin_renderers_quote_non_string_public_type_and_kwargs() -> None:
    """Public non-string scalar types and non-mapping kwargs remain data."""
    msgspec = MsgspecStruct(
        fields=[],
        reference=Reference(path="RawStruct", original_name="RawStruct", name="RawStruct"),
        extra_template_data=defaultdict(dict, {"RawStruct": {"base_class_kwargs": "not-a-dictionary"}}),
    )
    scalar = DataTypeScalarTypeBackport(
        fields=[],
        reference=Reference(path="PublicCode", original_name="PublicCode", name="PublicCode"),
        extra_template_data=defaultdict(
            dict,
            {"PublicCode": {"py_type": 1}},
        ),
    )

    msgspec_rendered = msgspec.render()
    scalar_rendered = scalar.render()

    assert "base_class_kwargs" not in msgspec_rendered
    assert 'TypeAliasType("PublicCode", 1)' in scalar_rendered
    assert ast.parse(msgspec_rendered)
    assert not any(
        isinstance(node, ast.Name) and node.id == "__import__" for node in ast.walk(ast.parse(scalar_rendered))
    )


def test_pydantic_dataclass_uses_safe_config_items() -> None:
    """Pydantic dataclass config is prepared in the private template context."""
    model = PydanticDataclassModel(
        fields=[],
        reference=Reference(path="Model", original_name="Model", name="Model"),
        extra_template_data=defaultdict(dict, {"Model": {"config": {"title": "safe"}}}),
    )

    assert model._internal_template_data["_safe_config_items"] == [("title", "'safe'")]


def test_pydantic_dataclass_ignores_invalid_config_keys_and_alias_generators() -> None:
    """Built-in ConfigDict rendering ignores invalid keys and quotes unknown aliases."""
    model = PydanticDataclassModel(
        fields=[],
        reference=Reference(path="Data", original_name="Data", name="Data"),
        extra_template_data=defaultdict(
            dict,
            {"Data": {"config": {"alias_generator": "unknown_alias", 1: "ignored"}}},
        ),
    )

    rendered = model.render()

    assert "alias_generator='unknown_alias'" in rendered
    assert "ignored" not in rendered
    assert ast.parse(rendered)


def test_pydantic_validators_are_prepared_in_private_template_data() -> None:
    """Validated Pydantic validators never reuse public prepared source fragments."""
    model = BaseModel(
        fields=[],
        reference=Reference(path="Model", original_name="Model", name="Model"),
        extra_template_data=defaultdict(
            dict,
            {
                "Model": {
                    "prepared_validators": [{"function_name": "ignored"}],
                    "validators": [{"field": "name", "function": "myapp.validators.validate_name", "mode": "before"}],
                }
            },
        ),
    )

    assert "prepared_validators" not in model.extra_template_data
    assert model._internal_template_data["prepared_validators"] == [
        {
            "fields_str": "'name'",
            "mode_str": "mode='before'",
            "method_name": "validate_name_validator",
            "function_name": "validate_name",
            "mode": "before",
        }
    ]


def test_typed_dict_template_type_expressions_are_non_executing() -> None:
    """PEP 728 public template data accepts only simple type names."""
    assert is_safe_public_type_name("datetime.date")
    assert not is_safe_public_type_name("dict[str, int] | None")
    assert not is_safe_public_type_name("__import__('os')")

    reference = Reference(path="Typed", original_name="Typed", name="Typed")
    public_model = TypedDictModel(
        fields=[],
        reference=reference,
        extra_template_data=defaultdict(
            dict,
            {"Typed": {"additionalPropertiesType": "datetime.date"}},
        ),
    )
    parser_model = TypedDictModel(
        fields=[],
        reference=Reference(path="Parser", original_name="Parser", name="Parser"),
        extra_template_data=defaultdict(
            dict,
            {"Parser": {"additionalPropertiesType": _make_internal_type_expression("dict[str, int]")}},
        ),
    )
    unsafe_model = TypedDictModel(
        fields=[],
        reference=Reference(path="Unsafe", original_name="Unsafe", name="Unsafe"),
        extra_template_data=defaultdict(
            dict,
            {"Unsafe": {"additionalPropertiesType": "__import__('os')"}},
        ),
    )
    code_model = TypedDictModel(
        fields=[],
        reference=Reference(path="Code", original_name="Code", name="Code"),
        extra_template_data=defaultdict(
            dict,
            {"Code": {"additionalPropertiesType": PythonCode("__import__('os').system('id')")}},
        ),
    )
    reference_model = TypedDictModel(
        fields=[],
        reference=Reference(path="Reference", original_name="Reference", name="Reference"),
        extra_template_data=defaultdict(
            dict,
            {
                "Reference": {
                    "additionalPropertiesType": PythonCode("__import__('os').system('id')"),
                    "additionalPropertiesReferenceClasses": {"ReferenceType"},
                }
            },
        ),
    )

    assert public_model._internal_template_data["typed_dict_kwargs"] == {"extra_items": "datetime.date"}
    assert public_model._has_pep728_kwargs is True
    assert parser_model._internal_template_data["typed_dict_kwargs"] == {"extra_items": "dict[str, int]"}
    assert unsafe_model._internal_template_data["typed_dict_kwargs"] == {"extra_items": "\"__import__('os')\""}
    assert code_model._internal_template_data["typed_dict_kwargs"] == {
        "extra_items": "\"__import__('os').system('id')\""
    }
    assert reference_model._internal_template_data["typed_dict_kwargs"] == {
        "extra_items": "\"__import__('os').system('id')\""
    }

    with pytest.raises(TypeError, match="must be created by the parser"):
        _InternalTypeExpression("dict[str, int]", object())


def test_typed_dict_extra_item_imports_do_not_leak_to_other_outputs() -> None:
    """Only TypedDict consumes imports attached to PEP 728 extra_items metadata."""
    extra_item_import = Import.from_full_path("datetime.datetime")
    typed_dict_metadata: dict[str, Any] = {}
    pydantic_metadata: dict[str, Any] = {}

    TypedDictModel.store_additional_properties_type(
        typed_dict_metadata,
        "datetime",
        imports=(extra_item_import,),
    )
    BaseModel.store_additional_properties_type(
        pydantic_metadata,
        "datetime",
        imports=(extra_item_import,),
    )

    typed_dict = TypedDictModel(
        fields=[],
        reference=Reference(path="Typed", original_name="Typed", name="Typed"),
        extra_template_data=defaultdict(dict, {"Typed": typed_dict_metadata}),
    )
    pydantic_model = BaseModel(
        fields=[],
        reference=Reference(path="Pydantic", original_name="Pydantic", name="Pydantic"),
        extra_template_data=defaultdict(dict, {"Pydantic": pydantic_metadata}),
    )

    assert extra_item_import in typed_dict.imports
    assert extra_item_import not in pydantic_model.imports


def test_typed_dict_include_only_custom_dir_keeps_builtin_context_safe(tmp_path: Path) -> None:
    """A custom include directory cannot make a built-in TypedDict consume raw kwargs."""
    model = TypedDictModel(
        fields=[],
        reference=Reference(path="Typed", original_name="Typed", name="Typed"),
        custom_template_dir=tmp_path,
        extra_template_data=defaultdict(dict, {"Typed": {"typed_dict_kwargs": {"closed": "True"}}}),
    )

    assert not model._uses_custom_root_template
    with pytest.raises(Error, match="typed_dict_kwargs is reserved"):
        model.render()


def test_builtin_template_data_comment_preserves_missing_none_and_values() -> None:
    """Built-in comment normalization preserves missing and explicit values."""

    class Comment:
        def __str__(self) -> str:
            return "custom\nstatement"

    missing = _safe_extra_template_data({})
    explicit_none = _safe_extra_template_data({"comment": None})

    assert "comment" not in missing
    assert explicit_none["comment"] is None
    assert _safe_extra_template_data({"comment": False})["comment"] == "False"
    assert not _safe_extra_template_data({"comment": ""})["comment"]
    assert _safe_extra_template_data({"comment": Comment()})["comment"] == "custom\n# statement"


def test_builtin_template_data_normalizes_public_mapping_keys() -> None:
    """Built-in key checks cannot be bypassed by a stateful string subclass."""

    class StatefulKey(str):  # noqa: FURB189, SLOT000 - intentionally violates the hash contract
        calls = 0
        __eq__ = str.__eq__

        def __hash__(self) -> int:
            type(self).calls += 1
            return 0 if type(self).calls < 3 else hash(str.__str__(self))

    class BenignKey(str):  # noqa: FURB189, SLOT000 - intentionally exercises public string-subclass keys
        pass

    class HiddenDict(dict[str, Any]):  # noqa: FURB189 - intentionally lies about public template data
        def __iter__(self) -> Any:  # pragma: no cover - built-in rendering bypasses this override
            return iter(())

    marker = "__import__('os').system('marker')"
    reserved_model = BaseModel(
        fields=[],
        reference=Reference(path="Reserved", original_name="Reserved", name="Reserved"),
        extra_template_data=defaultdict(dict, {"Reserved": {StatefulKey("class_body_lines"): [marker]}}),
    )
    comment_model = BaseModel(
        fields=[],
        reference=Reference(path="Comment", original_name="Comment", name="Comment"),
        extra_template_data=defaultdict(dict, {"Comment": {StatefulKey("comment"): f"safe\n{marker}"}}),
    )
    hidden_model = BaseModel(
        fields=[],
        reference=Reference(path="Hidden", original_name="Hidden", name="Hidden"),
        extra_template_data=defaultdict(dict, {"Hidden": HiddenDict({BenignKey("class_body_lines"): [marker]})}),
    )

    with pytest.raises(Error, match="class_body_lines is reserved"):
        reserved_model.render()
    with pytest.raises(Error, match="class_body_lines is reserved"):
        hidden_model.render()

    comment_rendered = comment_model.render()
    normalized = _safe_extra_template_data({BenignKey("extra"): "value"})

    assert type(next(iter(normalized))) is str
    assert normalized == {"extra": "value"}
    assert "# " + marker in comment_rendered
    assert not any(
        isinstance(node, ast.Name) and node.id == "__import__" for node in ast.walk(ast.parse(comment_rendered))
    )


def test_builtin_dataclass_arguments_are_typed_and_custom_templates_remain_raw() -> None:
    """Decorator arguments are safe in built-in templates and unchanged in custom roots."""

    class EvilArgument:
        def __repr__(self) -> str:
            return "__import__('os').system('marker')"

    class FabricatedArguments(dict[str, Any]):  # noqa: FURB189 - intentionally lies about public decorator data
        def __bool__(self) -> bool:  # pragma: no cover - built-in rendering receives an exact snapshot
            return True

        def items(self) -> Any:  # pragma: no cover - built-in rendering bypasses this override
            return (("slots", EvilArgument()),)

    marker = "__import__('os').system('marker')"
    builtin_models = (
        DataclassModel(
            fields=[],
            reference=Reference(path="Data", original_name="Data", name="Data"),
            dataclass_arguments={"slots": EvilArgument()},  # type: ignore[typeddict-item]
        ),
        PydanticDataclassModel(
            fields=[],
            reference=Reference(path="PydanticData", original_name="PydanticData", name="PydanticData"),
            dataclass_arguments={"slots": EvilArgument()},  # type: ignore[typeddict-item]
        ),
    )
    custom_model = PydanticDataclassModel(
        fields=[],
        reference=Reference(path="Custom", original_name="Custom", name="Custom"),
        custom_template_dir=Path("tests/data/templates_pydantic_v2_dataclass_legacy"),
        dataclass_arguments={"slots": EvilArgument()},  # type: ignore[typeddict-item]
    )
    fabricated_model = DataclassModel(
        fields=[],
        reference=Reference(path="Fabricated", original_name="Fabricated", name="Fabricated"),
        dataclass_arguments=FabricatedArguments(),  # type: ignore[arg-type]
    )

    for model in builtin_models:
        with pytest.raises(Error, match="dataclass argument 'slots' must be a bool"):
            model.render()

    assert marker in custom_model.render()
    fabricated_rendered = fabricated_model.render()
    assert marker not in fabricated_rendered
    assert ast.parse(fabricated_rendered)


def test_builtin_template_context_rejects_invalid_public_mapping_shapes() -> None:
    """Built-in syntax context rejects malformed and duplicate public keys."""

    class DuplicateKey(str):  # noqa: FURB189, SLOT000 - intentionally violates the hash contract
        __eq__ = str.__eq__

        def __hash__(self) -> int:
            return object.__hash__(self)

    duplicate_template_data = {DuplicateKey("extra"): "one", DuplicateKey("extra"): "two"}
    duplicate_dataclass_arguments = {DuplicateKey("slots"): True, DuplicateKey("slots"): False}

    with pytest.raises(Error, match="extra template data must be a dictionary"):
        _safe_extra_template_data([])  # type: ignore[arg-type]
    with pytest.raises(Error, match="extra template data keys must be strings"):
        _safe_extra_template_data({1: "value"})  # type: ignore[dict-item]
    with pytest.raises(Error, match="duplicate key 'extra'"):
        _safe_extra_template_data(duplicate_template_data)
    with pytest.raises(Error, match="dataclass_arguments must be a dictionary"):
        _safe_dataclass_arguments([])  # type: ignore[arg-type]
    with pytest.raises(Error, match="dataclass_arguments keys must be strings"):
        _safe_dataclass_arguments({1: True})  # type: ignore[dict-item]
    with pytest.raises(Error, match="invalid dataclass argument 'invalid'"):
        _safe_dataclass_arguments({"invalid": True})
    with pytest.raises(Error, match="duplicate key 'slots'"):
        _safe_dataclass_arguments(duplicate_dataclass_arguments)


def test_builtin_schema_runtime_validation_requires_parser_provenance() -> None:
    """A public runtime-validation object cannot reach the built-in repr filter."""

    class EvilString(str):  # noqa: FURB189, SLOT000 - intentionally exercises hostile string subclasses
        def __repr__(self) -> str:  # pragma: no cover - built-in rendering rejects this object before repr()
            return "__import__('os').system('marker')"

    model = BaseModel(
        fields=[],
        reference=Reference(path="Model", original_name="Model", name="Model"),
        extra_template_data=defaultdict(
            dict,
            {
                "Model": {
                    "schema_runtime_validation": SchemaRuntimeValidation(
                        required_groups=[RequiredGroupsRule(keyword="oneOf", groups=(((EvilString("value"),),),))]
                    )
                }
            },
        ),
    )

    with pytest.raises(Error, match="schema_runtime_validation is reserved"):
        model.render()

    with pytest.raises(TypeError, match="must be created by the parser"):
        _InternalSchemaRuntimeValidation(object())


def test_default_apply_discriminator_tag_is_noop() -> None:
    """Models without tagged unions leave fields and template data unchanged."""
    field = TypedDictDataModelField(name="kind", data_type=DataType(literals=["pet"]))
    model = TypedDictModel(
        fields=[field],
        reference=Reference(path="Pet", original_name="Pet", name="Pet"),
    )

    model.apply_discriminator_tag(field, "kind", "pet")

    assert model.extra_template_data == {}
    assert field.extras == {}


template: str = """{%- for decorator in decorators -%}
{{ decorator }}
{%- endfor %}
@dataclass
class {{ class_name }}:
{%- for field in fields -%}
    {%- if field.required %}
    {{ field.name }}: {{ field.type_hint }}
    {%- else %}
    {{ field.name }}: {{ field.type_hint }} = {{field.default}}
    {%- endif %}
{%- endfor -%}"""


def test_template_base() -> None:
    """Test TemplateBase rendering and file path handling."""
    with NamedTemporaryFile("w", delete=False, encoding="utf-8") as dummy_template:
        dummy_template.write("abc")
        dummy_template.seek(0)
        dummy_template.close()
        a: TemplateBase = A(Path(dummy_template.name))
    assert str(a.template_file_path) == dummy_template.name
    assert a._render() == "abc"
    assert not str(a)


def test_data_model() -> None:
    """Test DataModel rendering with fields and decorators."""
    field = DataModelFieldBase(name="a", data_type=DataType(type="str"), default="abc", required=True)

    with NamedTemporaryFile("w", delete=False, encoding="utf-8") as dummy_template:
        dummy_template.write(template)
        dummy_template.seek(0)
        dummy_template.close()
        B.TEMPLATE_FILE_PATH = dummy_template.name
        data_model = B(
            fields=[field],
            decorators=["@validate"],
            base_classes=[Reference(path="base", original_name="base", name="Base")],
            reference=Reference(path="test_model", name="test_model"),
        )

    assert data_model.name == "test_model"
    assert data_model.fields == [field]
    assert data_model.decorators == ["@validate"]
    assert data_model.base_class == "Base"
    assert data_model.render() == "@validate\n@dataclass\nclass test_model:\n    a: str"


def test_data_model_module_code_hooks_default_to_noop() -> None:
    """Keep shared module-code hooks free for model types without helpers."""
    assert DataModel.get_module_code_insertion_index([]) == 0
    assert DataModel.prepare_module_code([]) is None
    assert DataModel.invalidate_module_code_cache([]) is None


def test_data_model_custom_base_class_list() -> None:
    """Preserve every explicitly configured custom base class."""
    model = BaseModel(
        fields=[],
        reference=Reference(path="Model", original_name="Model", name="Model"),
        custom_base_class=["package.First", "package.Second"],
    )

    assert model.base_class == "First, Second"
    assert [(base.type, base.import_) for base in model.base_classes] == [
        ("First", Import(from_="package", import_="First")),
        ("Second", Import(from_="package", import_="Second")),
    ]


def test_data_model_relative_custom_template_without_adapter() -> None:
    """Load a relative custom template unchanged when the model has no adapter."""

    class RelativeTemplateModel(B):
        """Data model using an existing relative custom template fixture."""

        TEMPLATE_FILE_PATH = "pydantic_v2/BaseModel.jinja2"

    model = RelativeTemplateModel(
        fields=[],
        reference=Reference(path="Model", original_name="Model", name="Model"),
        custom_template_dir=Path("tests/data/templates_pydantic_extra_pre_3593"),
    )

    assert Path(model.template.filename).parts[-2:] == ("pydantic_v2", "BaseModel.jinja2")


def test_relative_custom_root_template_keeps_raw_template_data() -> None:
    """An existing relative custom root remains an explicit trusted-template opt-in."""
    model = BaseModel(
        fields=[],
        reference=Reference(path="Model", original_name="Model", name="Model"),
        custom_template_dir=Path("tests/data/templates_pydantic_extra_pre_3593"),
        extra_template_data=defaultdict(dict, {"Model": {"class_body_lines": ["raw_custom_code = True"]}}),
    )

    assert model._uses_custom_root_template
    assert "raw_custom_code = True" in model.render()


def test_template_fields_escape_only_changed_custom_docstrings() -> None:
    """Custom roots use escaped proxies only for the fields that need them."""
    custom_model = BaseModel(
        fields=[
            PydanticV2DataModelField(name="first", data_type=DataType(type="str"), required=True),
            PydanticV2DataModelField(
                name="escaped",
                data_type=DataType(type="str"),
                required=True,
                extras={"description": 'contains """'},
                use_field_description=True,
            ),
            PydanticV2DataModelField(
                name="escaped_again",
                data_type=DataType(type="str"),
                required=True,
                extras={"description": 'also contains """'},
                use_field_description=True,
            ),
            PydanticV2DataModelField(name="last", data_type=DataType(type="str"), required=True),
        ],
        reference=Reference(path="Custom", original_name="Custom", name="Custom"),
        custom_template_dir=Path("tests/data/templates_pydantic_extra_pre_3593"),
    )
    builtin_model = BaseModel(
        fields=[],
        reference=Reference(path="Builtin", original_name="Builtin", name="Builtin"),
    )

    custom_rendered = custom_model.render()
    builtin_rendered = builtin_model.render()

    assert 'contains \\"\\"\\"' in custom_rendered
    assert 'also contains \\"\\"\\"' in custom_rendered
    assert "class Builtin(BaseModel):" in builtin_rendered


def test_legacy_relative_custom_root_template_remains_trusted() -> None:
    """The legacy top-level BaseModel template remains a custom root template."""

    class RawComment:
        def __str__(self) -> str:
            return "safe\nraw_custom_marker = True"

    model = BaseModel(
        fields=[],
        reference=Reference(path="Model", original_name="Model", name="Model"),
        custom_template_dir=Path("tests/data/templates_old_style"),
        extra_template_data=defaultdict(dict, {"Model": {"comment": RawComment()}}),
    )

    assert model._uses_custom_root_template
    assert Path(model.template.filename).resolve() == Path("tests/data/templates_old_style/BaseModel.jinja2").resolve()
    assert "\nraw_custom_marker = True" in model.render()


def test_pydantic_custom_template_legacy_root_keeps_precedence(tmp_path: Path) -> None:
    """The historical root layout still wins when both custom paths exist."""
    legacy_template = tmp_path / "BaseModel.jinja2"
    current_template = tmp_path / "pydantic_v2/BaseModel.jinja2"
    current_template.parent.mkdir()
    legacy_template.write_text("legacy", encoding="utf-8")
    current_template.write_text("current", encoding="utf-8")

    model = BaseModel(
        fields=[],
        reference=Reference(path="Model", original_name="Model", name="Model"),
        custom_template_dir=tmp_path,
    )

    assert model.template_file_path == legacy_template


def test_direct_data_models_reuse_bounded_custom_template_cache(tmp_path: Path) -> None:
    """Direct model consumers retain bounded process-level template reuse."""
    _clear_custom_template_caches()
    try:
        custom_template = tmp_path / "pydantic_v2/BaseModel.jinja2"
        custom_template.parent.mkdir()
        custom_template.write_text("class {{ class_name }}({{ base_class }}):\n    pass\n", encoding="utf-8")

        models = [
            BaseModel(
                fields=[],
                reference=Reference(path=name, original_name=name, name=name),
                custom_template_dir=tmp_path,
            )
            for name in ("First", "Second")
        ]

        assert models[0].template is models[1].template
        assert _get_template_with_absolute_path.cache_parameters()["maxsize"] == 128
        assert _get_template_with_custom_dir.cache_parameters()["maxsize"] == 128
        assert _get_environment.cache_parameters()["maxsize"] == 16
        assert _get_environment_with_absolute_path.cache_parameters()["maxsize"] == 16
        assert get_template.cache_parameters()["maxsize"] == 128

        for index in range(_MAX_MISSING_CUSTOM_TEMPLATE_SUBDIRS + 2):
            include_only_dir = tmp_path / f"include-only-{index}"
            model = BaseModel(
                fields=[],
                reference=Reference(path=f"Model{index}", original_name=f"Model{index}", name=f"Model{index}"),
                custom_template_dir=include_only_dir,
            )
            _ = model.template

        assert _get_template_with_custom_dir.cache_info().currsize <= 128
        assert _missing_custom_template_state.count == _MAX_MISSING_CUSTOM_TEMPLATE_SUBDIRS
        _refresh_custom_template_paths(tmp_path / "overflow-refresh")
        assert not _missing_custom_template_state.paths
        assert _get_template_with_custom_dir.cache_info().currsize == 0

        missing_subdir = tmp_path / "duplicate/pydantic_v2"
        _remember_missing_custom_template_subdir(missing_subdir.parent, missing_subdir)
        _remember_missing_custom_template_subdir(missing_subdir.parent, missing_subdir)
        assert _missing_custom_template_state.count == 1

        for index in range(_MAX_CUSTOM_TEMPLATE_SIGNATURES + 1):
            _refresh_custom_template_paths(tmp_path / f"signature-{index}")
        assert_output(
            f"{len(_missing_custom_template_state.signatures)}\n",
            Path(__file__).parent.parent / "data/expected/model/custom_template_signature_cache_size.txt",
        )
    finally:
        _clear_custom_template_caches()


@pytest.mark.parametrize(
    "failed_path_kind",
    [
        "directory",
        "directory_scan",
        pytest.param("file", marks=_SYMLINK_CREATION_SKIP),
        pytest.param("directory_link", marks=_SYMLINK_CREATION_SKIP),
        pytest.param("file_link", marks=_SYMLINK_CREATION_SKIP),
    ],
)
def test_custom_template_signature_tolerates_stat_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_path_kind: str
) -> None:
    """A disappearing custom directory or template does not abort generation setup."""
    template_path = tmp_path / "pydantic_v2" / "BaseModel.jinja2"
    template_path.parent.mkdir()
    template_path.write_text("template", encoding="utf-8")
    error_message = "simulated template path race"
    if failed_path_kind.endswith("link"):
        link_target = tmp_path / "target"
        template_path.unlink()
        if failed_path_kind == "directory_link":
            failed_path = template_path.parent
            failed_path.rmdir()
            link_target.mkdir()
            failed_path.symlink_to(link_target, target_is_directory=True)
        else:
            failed_path = template_path
            failed_path.symlink_to(link_target)

        def raise_readlink_error(_: Path) -> Path:
            raise FileNotFoundError(ENOENT, error_message)

        monkeypatch.setattr(Path, "readlink", raise_readlink_error)
    elif failed_path_kind == "file":
        template_path.unlink()
        template_path.symlink_to("missing_template")
    elif failed_path_kind == "directory_scan":

        def raise_scandir_error(*_: Any, **__: Any) -> Any:
            raise FileNotFoundError(ENOENT, error_message)

        monkeypatch.setattr(os, "scandir", raise_scandir_error)
    else:

        def raise_stat_error(*_: Any, **__: Any) -> Any:
            raise FileNotFoundError(ENOENT, error_message)

        monkeypatch.setattr(Path, "stat", raise_stat_error)

    assert_output(
        _get_custom_template_signature(tmp_path).hex() + "\n",
        Path(__file__).parent.parent
        / "data/expected/model"
        / (
            "custom_template_signature_dangling_link.txt"
            if failed_path_kind == "file"
            else "custom_template_signature_empty.txt"
        ),
    )


@_SYMLINK_CREATION_SKIP
def test_custom_template_signature_tracks_root_directory_symlink(tmp_path: Path) -> None:
    """A custom template root symlink participates in its signature."""
    (tmp_path / "target").mkdir()
    template_directory = tmp_path / "templates"
    template_directory.symlink_to("target", target_is_directory=True)

    assert_output(
        _get_custom_template_signature(template_directory).hex() + "\n",
        Path(__file__).parent.parent / "data/expected/model/custom_template_signature_root_link.txt",
    )


def test_data_model_create_typed_extra_field_unsupported() -> None:
    """Test the default typed extra field factory for unsupported models."""
    assert (
        DataModel.create_typed_extra_field(
            field_model=DataModelFieldBase,
            data_type=DataType(type="str"),
        )
        is None
    )


def test_pydantic_v2_base_model_create_typed_extra_field() -> None:
    """Test Pydantic v2 typed extra field creation."""
    data_type = DataType(type="str", is_dict=True)

    field = BaseModel.create_typed_extra_field(
        field_model=PydanticV2DataModelField,
        data_type=data_type,
    )

    assert field.name == "__pydantic_extra__"
    assert field.original_name == "__pydantic_extra__"
    assert field.data_type is data_type
    assert field.required is True


@pytest.mark.parametrize(
    ("dict_key", "is_supported"),
    [
        pytest.param(
            DataType(type="constr", is_func=True, kwargs={"pattern": "^[a-z]+$"}, import_=IMPORT_CONSTR),
            True,
            id="constrained-string",
        ),
        pytest.param(DataType(literals=["named"]), True, id="string-literal"),
        pytest.param(DataType(literals=[1]), False, id="non-string-literal"),
        pytest.param(DataType(type="int", literals=["named"]), False, id="integer-with-string-literal"),
        pytest.param(DataType(type="str"), False, id="plain-string"),
        pytest.param(DataType(type="int"), False, id="integer"),
        pytest.param(DataType(type="bool"), False, id="boolean"),
        pytest.param(DataType(type="constr", import_=IMPORT_CONSTR), False, id="unconfigured-constr"),
        pytest.param(DataType(enum_member_literals=[("Key", "member")]), False, id="enum-member-literal"),
        pytest.param(DataType(is_optional=True), False, id="optional"),
        pytest.param(DataType(is_dict=True), False, id="dict"),
        pytest.param(DataType(is_list=True), False, id="list"),
        pytest.param(DataType(is_set=True), False, id="set"),
        pytest.param(DataType(is_frozen_set=True), False, id="frozen-set"),
        pytest.param(DataType(is_mapping=True), False, id="mapping"),
        pytest.param(DataType(is_sequence=True), False, id="sequence"),
        pytest.param(DataType(is_tuple=True), False, id="tuple"),
        pytest.param(
            DataType(data_types=[DataType(type="str"), DataType(type="int")]),
            False,
            id="compound",
        ),
    ],
)
def test_pydantic_v2_base_model_typed_extra_dict_key_capability(
    dict_key: DataType,
    is_supported: bool,
) -> None:
    """Test Pydantic v2 keeps only supported typed-extra key types."""
    data_type = DataType(data_types=[DataType(type="int")], is_dict=True, dict_key=dict_key)

    field = BaseModel.create_typed_extra_field(
        field_model=PydanticV2DataModelField,
        data_type=data_type,
    )

    if not is_supported:
        assert field.data_type.dict_key is None
    else:
        assert field.data_type.dict_key is dict_key


def test_pydantic_v2_base_model_typed_extra_dict_key_capability_unregisters_references() -> None:
    """Test discarded typed-extra key types leave no reverse-reference registration."""
    reference = Reference(path="Key", original_name="Key", name="Key")
    nested_data_type = DataType(reference=reference)
    dict_key = DataType(data_types=[nested_data_type])
    data_type = DataType(data_types=[DataType(type="int")], is_dict=True, dict_key=dict_key)

    BaseModel.create_typed_extra_field(
        field_model=PydanticV2DataModelField,
        data_type=data_type,
    )

    assert data_type.dict_key is None
    assert dict_key.parent is None
    assert nested_data_type.parent is None
    assert nested_data_type not in reference.children


def test_pydantic_v2_base_model_typed_extra_dict_key_capability_fails_closed() -> None:
    """Test typed-extra key constraints are discarded without a backend capability."""

    class NoKeyCapabilityBaseModel(BaseModel):
        _TYPED_EXTRA_DICT_KEY_CAPABILITY = None

    data_type = DataType(
        data_types=[DataType(type="int")],
        is_dict=True,
        dict_key=DataType(type="constr", is_func=True, kwargs={"pattern": "^[a-z]+$"}, import_=IMPORT_CONSTR),
    )

    field = NoKeyCapabilityBaseModel.create_typed_extra_field(
        field_model=PydanticV2DataModelField,
        data_type=data_type,
    )

    assert field.data_type.dict_key is None


def test_data_model_dedup_key_uses_model_base_to_hashable_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test DataModel deduplication resolves to_hashable through model.base."""
    calls: list[object] = []

    def fake_to_hashable(value: object) -> tuple[str, int]:
        calls.append(value)
        return ("patched", len(calls))

    monkeypatch.setattr("datamodel_code_generator.model.base.to_hashable", fake_to_hashable)
    model = BaseModel(fields=[], reference=Reference(path="Model", original_name="Model", name="Model"))

    assert model.get_dedup_key() == (("patched", 1), ("patched", 2))
    assert isinstance(calls[0], str)
    assert calls[1] == model.imports


def test_data_model_imports_cache_clears_after_field_type_replacement() -> None:
    """Test model imports reflect field type replacements after an earlier read."""
    field = DataModelFieldBase(name="a", data_type=DataType(type="str"), required=True)
    model = BaseModel(fields=[field], reference=Reference(path="Model", original_name="Model", name="Model"))

    assert IMPORT_DECIMAL not in model.imports

    field.replace_data_type(DataType.from_import(IMPORT_DECIMAL))

    assert IMPORT_DECIMAL in model.imports


def test_data_model_render_identity_cache_clears_after_field_mutations() -> None:
    """Field mutations must not retain a render-derived deduplication key."""
    field = PydanticDataclassField(name="value", data_type=DataType(type="str"), required=True)
    model = PydanticDataclassModel(
        fields=[field],
        reference=Reference(path="Model", original_name="Model", name="Model"),
    )
    original_key = model.get_dedup_key()

    field.force_field_assignment()
    field.force_field_assignment()

    assert model.get_dedup_key() != original_key


@pytest.mark.parametrize("reference_name", ["Before", "package.Before"])
def test_data_model_render_identity_cache_clears_after_model_rename(reference_name: str) -> None:
    """Model identity must be recomputed after a generated class name changes."""
    model = BaseModel(
        fields=[],
        reference=Reference(path=reference_name, original_name=reference_name, name=reference_name),
    )
    original_key = model.get_dedup_key(None, use_default=False)

    model.class_name = "After"

    assert model.reference.name == ("package.After" if "." in reference_name else "After")
    assert model.get_dedup_key(None, use_default=False) != original_key


def test_data_model_parent_and_reference_path_changes_invalidate_field_semantics() -> None:
    """Direct model ownership and path changes must refresh self-reference state."""
    model_reference = Reference(path="Before", original_name="Before", name="Before")
    other_reference = Reference(path="After", original_name="After", name="After")
    field = PydanticV2DataModelField(name="value", data_type=DataType(reference=other_reference))
    model = BaseModel(fields=[field], reference=model_reference)

    assert not field.self_reference()

    model.set_reference_path("After")

    assert field.self_reference()

    replacement_model = BaseModel(
        fields=[field],
        reference=Reference(path="Replacement", original_name="Replacement", name="Replacement"),
    )

    assert field.parent is replacement_model
    assert not field.self_reference()


def test_field_deep_copy_drops_transient_semantic_caches() -> None:
    """Copied fields must recompute transient renderer and self-reference state."""
    field = PydanticV2DataModelField(name="value", data_type=DataType(type="str"))
    field.__dict__["_computed_default_factory"] = "list"
    field.__dict__["_self_reference_cache"] = True

    copied = field.copy_deep()

    assert "_computed_default_factory" not in copied.__dict__
    assert "_self_reference_cache" not in copied.__dict__


def test_field_semantic_cache_invalidation_supports_legacy_parent_hook() -> None:
    """External parent implementations with only the legacy import hook remain compatible."""

    class ImportsOnlyParent:
        def __init__(self) -> None:
            self.cache_cleared = False

        def clear_imports_cache(self) -> None:
            self.cache_cleared = True

    parent = ImportsOnlyParent()
    field = DataModelFieldBase(name="value", data_type=DataType(type="str"))
    field.parent = parent  # ty: ignore[invalid-assignment]

    field.invalidate_semantic_caches()

    assert parent.cache_cleared

    parent.cache_cleared = False
    field.force_field_assignment()

    assert parent.cache_cleared


def test_pydantic_v2_extra_type_hint_keeps_non_dict_hint() -> None:
    """Test typed-extra type hint conversion leaves non-dict hints unchanged."""
    field = PydanticV2DataModelField(
        name="__pydantic_extra__",
        data_type=DataType(type="str"),
        required=True,
    )

    assert field.pydantic_extra_type_hint == "str"


def test_pydantic_v2_extra_type_hint_uses_structured_root_dict() -> None:
    """Test typed-extra type hint renders only the root dict as typing.Dict."""
    item_type = DataType(type="str", is_list=True, use_standard_collections=True)
    data_type = DataType(data_types=[item_type], is_dict=True, use_standard_collections=True)
    field = PydanticV2DataModelField(
        name="__pydantic_extra__",
        data_type=data_type,
        required=True,
    )

    assert field.type_hint == "dict[str, list[str]]"
    assert field.pydantic_extra_type_hint == "Dict[str, list[str]]"
    assert data_type.use_standard_collections is True
    assert item_type.use_standard_collections is True
    assert IMPORT_DICT in field.imports


def test_pydantic_v2_extra_annotation_mode_defaults_to_annotations_dict() -> None:
    """Test typed extras use class-body __annotations__ by default."""
    field = PydanticV2DataModelField(
        name="__pydantic_extra__",
        data_type=DataType(type="str", is_dict=True, use_standard_collections=True),
        required=True,
    )

    assert field.is_pydantic_extra_field
    assert field.use_pydantic_extra_annotations_dict
    assert not field.use_pydantic_extra_plain_annotation
    assert IMPORT_DICT in field.imports


def test_pydantic_v2_extra_annotation_mode_uses_plain_annotation_for_native_deferred() -> None:
    """Test typed extras use plain annotations for native deferred annotation targets."""
    field = PydanticV2DataModelField(
        name="__pydantic_extra__",
        data_type=DataType(type="str", is_dict=True, use_standard_collections=True),
        required=True,
    )
    model = BaseModel(fields=[field], reference=Reference(path="Model", original_name="Model", name="Model"))

    model.extra_template_data["pydantic_extra_plain_annotation"] = True

    assert field.use_pydantic_extra_plain_annotation
    assert not field.use_pydantic_extra_annotations_dict
    assert IMPORT_DICT not in field.imports


def test_pydantic_v2_legacy_extra_template_supports_relative_custom_path() -> None:
    """Test legacy typed extras render through a relative custom template path."""
    field = PydanticV2DataModelField(
        name="__pydantic_extra__",
        data_type=DataType(type="str", is_dict=True),
        required=True,
    )
    model = BaseModel(
        fields=[field],
        reference=Reference(path="Model", original_name="Model", name="Model"),
        custom_template_dir=Path("tests/data/templates_pydantic_extra_pre_3593"),
    )

    with pytest.warns(UserWarning, match="was rewritten automatically for Pydantic typed-extra"):
        rendered = model.render()

    assert Path(model.template.filename).parts[-2:] == ("pydantic_v2", "BaseModel.jinja2")
    assert "'__pydantic_extra__': Dict[str, str]," in rendered
    assert "Model.__annotations__['__pydantic_extra__']" not in rendered
    assert "Model.model_rebuild(force=True)" not in rendered
    assert "locals()" not in rendered


@pytest.mark.parametrize("customization", ["missing-tail", "missing-class-body"])
def test_pydantic_v2_legacy_extra_template_warns_when_not_fully_rewritten(customization: str, tmp_path: Path) -> None:
    """Warn without failing when a customized legacy template cannot be fully rewritten."""
    source_template = Path("tests/data/templates_pydantic_extra_pre_3593/pydantic_v2/BaseModel.jinja2")
    custom_template = tmp_path / "pydantic_v2/BaseModel.jinja2"
    custom_template.parent.mkdir()
    template_source = source_template.read_text(encoding="utf-8")
    match customization:
        case "missing-tail":
            template_source = template_source.rsplit("{%- for field in fields %}", 1)[0]
        case _:
            template_source = template_source.replace(
                "{%- for line in class_body_lines %}\n    {{ line }}\n{%- endfor %}\n",
                "",
            )
    custom_template.write_text(template_source, encoding="utf-8")
    field = PydanticV2DataModelField(
        name="__pydantic_extra__",
        data_type=DataType(type="str", is_dict=True),
        required=True,
    )
    model = BaseModel(
        fields=[field],
        reference=Reference(path="Model", original_name="Model", name="Model"),
        custom_template_dir=tmp_path,
    )

    with pytest.warns(UserWarning, match="could not be fully rewritten automatically"):
        rendered = model.render()

    match customization:
        case "missing-tail":
            assert "'__pydantic_extra__': Dict[str, str]," in rendered
        case _:
            assert "Model.__annotations__['__pydantic_extra__'] = Dict[str, str]" in rendered


def test_strip_legacy_pydantic_extra_post_class_assignment_is_model_scoped() -> None:
    """Strip only the target model's old assignment while preserving rebuilds and helpers."""
    rendered = (
        "Helper.__annotations__['__pydantic_extra__'] = Dict[str, int]\n"
        "Helper.model_rebuild(force=True)\n"
        'Model . __annotations__ [ "__pydantic_extra__" ] = Dict[str, int]\r\n'
        "\r\n"
        "Model . model_rebuild ( force = True )  # legacy\r\n"
        "Model.model_rebuild()\n"
    )

    assert _strip_legacy_pydantic_extra_post_class_assignment(rendered, "Missing") is None
    assert _strip_legacy_pydantic_extra_post_class_assignment(rendered, "Model") == (
        "Helper.__annotations__['__pydantic_extra__'] = Dict[str, int]\n"
        "Helper.model_rebuild(force=True)\n"
        "Model.model_rebuild()\n"
    )
    unicode_stripped = _strip_legacy_pydantic_extra_post_class_assignment(
        "℘Model.__annotations__['__pydantic_extra__'] = Dict[str, int]\n℘Model.model_rebuild(force=True)\n",
        "℘Model",
    )
    assert unicode_stripped is not None
    assert not unicode_stripped


def test_pydantic_v2_missing_sentinel_default_keeps_explicit_default() -> None:
    """Test explicit defaults are not replaced by the MISSING sentinel."""
    field = PydanticV2DataModelField(
        name="value",
        data_type=DataType(type="str"),
        default="fallback",
        use_missing_sentinel=True,
    )

    assert not field.use_missing_sentinel_default
    assert field.represented_default == "'fallback'"
    assert IMPORT_MISSING not in field.imports


def test_pydantic_v2_missing_sentinel_type_hint_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test defensive MISSING type-hint branches."""
    field = PydanticV2DataModelField(
        name="value",
        data_type=DataType(type="str", use_union_operator=True),
        use_missing_sentinel=True,
    )

    assert field._type_hint_with_missing_sentinel("") == "MISSING"

    monkeypatch.setattr(PydanticV2DataModelField, "_use_union_operator", property(lambda _self: None))

    assert field._type_hint_with_missing_sentinel("str") == "str"


def test_rendered_pydantic_v2_field_uses_structured_values() -> None:
    """Test the built-in proxy exposes both views of the structured render plan."""
    field = PydanticV2DataModelField(
        name="name",
        data_type=DataType(type="str"),
        required=True,
        extras={"title": "Name"},
        use_annotated=True,
    )
    expected_field = field.field
    expected_annotated = field.annotated
    rendered_field = _RenderedDataModelField(field, "")

    assert rendered_field.annotated == expected_annotated
    assert rendered_field.field == expected_field


@pytest.mark.parametrize(
    ("field_kwargs", "expected"),
    [
        pytest.param({"extras": {"title": "Field("}}, "title='Field('", id="title"),
        pytest.param({"extras": {"description": "Field("}}, "description='Field('", id="description"),
        pytest.param({"extras": {"examples": ["Field("]}}, "examples=['Field(']", id="examples"),
        pytest.param(
            {"extras": {"json_schema_extra": {"marker": "Field("}}},
            "json_schema_extra={'marker': 'Field('}",
            id="json-schema-extra",
        ),
        pytest.param({"alias": "Field("}, "alias='Field('", id="alias"),
    ],
)
def test_pydantic_default_kwarg_preserves_field_syntax_in_user_metadata(
    field_kwargs: dict[str, object],
    expected: str,
) -> None:
    """Only the actual positional default is converted to a default keyword."""
    field = PydanticV2DataModelField(
        name="value",
        data_type=DataType(type="str"),
        default="value",
        required=False,
        has_default=True,
        use_default_kwarg=True,
        **field_kwargs,
    )

    assert str(field).startswith("Field('value', ")
    assert field.field == f"Field(default='value', {expected})"


@pytest.mark.parametrize(
    ("default", "has_default", "required", "extra_values", "expected"),
    [
        pytest.param(
            "value",
            True,
            False,
            {},
            "Field(default='value', title='Field(')",
            id="default",
        ),
        pytest.param(
            UNDEFINED,
            True,
            False,
            {"default_factory": "list"},
            "Field(default_factory=list, title='Field(')",
            id="factory",
        ),
        pytest.param(
            UNDEFINED,
            False,
            True,
            {},
            "Field(..., title='Field(')",
            id="required",
        ),
    ],
)
def test_pydantic_field_render_plan_preserves_assignment_semantics(
    default: object,
    has_default: bool,
    required: bool,
    extra_values: dict[str, object],
    expected: str,
) -> None:
    """Default, factory, and required assignments share one structured plan."""
    field = PydanticV2DataModelField(
        name="value",
        data_type=DataType(type="str"),
        default=default,
        required=required,
        has_default=has_default,
        extras={"title": "Field(", **extra_values},
        use_default_kwarg=True,
    )

    assert field.field == expected


def test_pydantic_v2_field_render_plan_preserves_explicit_null_default() -> None:
    """The v2-only Field(None) fallback keeps its distinct assignment form."""
    field = PydanticV2DataModelField(
        name="value",
        data_type=DataType(type="None"),
        default=None,
        required=False,
        has_default=True,
        use_annotated=True,
        use_default_kwarg=True,
    )

    assert str(field) == "Field(None)"
    assert field.field == "Field(default=None)"
    assert field.annotated == "Annotated[None, Field(None)]"


def test_pydantic_v2_empty_field_render_plan_is_shared_for_builtin_fields() -> None:
    """Built-in fields without Field() syntax share one immutable empty plan."""
    first = PydanticV2DataModelField(name="first", data_type=DataType(type="str"), required=True)
    second = PydanticV2DataModelField(name="second", data_type=DataType(type="int"), required=True)
    BaseModel(
        fields=[first, second],
        reference=Reference(path="Model", name="Model"),
    )

    first_plan = first._get_field_render_plan()

    assert first_plan is second._get_field_render_plan()
    assert not first_plan.rendered
    assert first_plan.assignment is None
    assert first_plan.arguments == ()
    assert first_plan.default_factory is None
    assert first.imports == ()


def test_pydantic_v2_empty_field_render_plan_falls_back_for_extensions_and_field_syntax() -> None:
    """Custom templates, subclasses, and Field() features retain the conventional plan."""
    plain = PydanticV2DataModelField(name="plain", data_type=DataType(type="str"), required=True)
    BaseModel(fields=[plain], reference=Reference(path="Plain", name="Plain"))
    shared_plan = plain._get_field_render_plan()

    hook_calls = 0

    class CustomField(PydanticV2DataModelField):
        def _get_field_render_plan(self) -> Any:
            nonlocal hook_calls
            hook_calls += 1
            return super()._get_field_render_plan()

    custom_field = CustomField(name="custom", data_type=DataType(type="str"), required=True)
    custom_template_field = PydanticV2DataModelField(
        name="custom_template",
        data_type=DataType(type="str"),
        required=True,
    )
    constrained_field = PydanticV2DataModelField(
        name="constrained",
        data_type=DataType(type="str"),
        required=False,
        constraints=PydanticV2Constraints(minLength=1),
    )
    alias_field = PydanticV2DataModelField(
        name="alias",
        data_type=DataType(type="str"),
        required=False,
        alias="alias-value",
    )
    for field, model in (
        (
            custom_field,
            BaseModel(fields=[custom_field], reference=Reference(path="Custom", name="Custom")),
        ),
        (
            custom_template_field,
            BaseModel(
                fields=[custom_template_field],
                reference=Reference(path="CustomTemplate", name="CustomTemplate"),
                custom_template_dir=Path(__file__).parents[1] / "data" / "templates_extensions",
            ),
        ),
        (
            constrained_field,
            BaseModel(fields=[constrained_field], reference=Reference(path="Constrained", name="Constrained")),
        ),
        (
            alias_field,
            BaseModel(fields=[alias_field], reference=Reference(path="Alias", name="Alias")),
        ),
    ):
        assert field.parent is model
        assert field._get_field_render_plan() is not shared_plan

    assert hook_calls == 1
    assert str(constrained_field) == "Field(None, min_length=1)"
    assert str(alias_field) == "Field(None, alias='alias-value')"


def test_pydantic_field_render_plan_preserves_required_nullable_marker() -> None:
    """A required nullable field uses the shared single-argument Field() plan."""
    field = PydanticV2DataModelField(
        name="value",
        data_type=DataType(type="str"),
        required=True,
        nullable=True,
    )

    assert str(field) == "Field(...)"
    assert field.field == "Field(...)"


def test_pydantic_field_render_plan_preserves_string_discriminator() -> None:
    """A string discriminator remains structured Field() metadata."""
    field = PydanticBaseDataModelField(
        name="value",
        data_type=DataType(type="str"),
        required=True,
        extras={"discriminator": "kind"},
    )

    assert str(field) == "Field(..., discriminator='kind')"


def test_pydantic_class_var_factory_state_is_access_order_independent() -> None:
    """ClassVar field rendering stays stateless across compatibility accessors."""
    field = PydanticV2DataModelField(
        name="value",
        data_type=DataType(type="str"),
        required=False,
        extras={"x-is-classvar": True, "default_factory": "list"},
        use_annotated=True,
    )

    assert field.has_default_factory_in_field is False
    assert field._get_field_data_and_default_factory() == ({}, None)
    assert field._rendered_field_values() == (None, None)
    assert field.field is None
    assert field.has_default_factory_in_field is False


def test_pydantic_base_field_string_uses_structured_render_plan() -> None:
    """The shared Pydantic field class renders through the structured path."""
    field = PydanticBaseDataModelField(
        name="value",
        data_type=DataType(type="str"),
        default="value",
        required=False,
        has_default=True,
        extras={"description": "Field("},
    )

    assert str(field) == "Field('value', description='Field(')"


@pytest.mark.parametrize(
    ("field_kwargs", "expected_field"),
    [
        pytest.param({"default": "lambda: convert"}, "'lambda: convert'", id="default"),
        pytest.param(
            {"default": "value", "alias": "lambda: convert"},
            "field(name='lambda: convert', default='value')",
            id="alias",
        ),
    ],
)
def test_msgspec_convert_import_ignores_user_string_false_positives(
    field_kwargs: dict[str, object],
    expected_field: str,
) -> None:
    """User values containing converter syntax do not create a converter import."""
    field = MsgspecDataModelField(
        name="value",
        data_type=DataType(type="str"),
        required=True,
        has_default=True,
        use_default_with_required=True,
        **field_kwargs,
    )

    assert str(field) == expected_field
    assert IMPORT_MSGSPEC_CONVERT not in field.imports


def test_msgspec_struct_default_render_plan_requires_convert_import() -> None:
    """A generated Struct conversion factory retains both syntax-owned imports."""
    nested_reference = Reference(path="#/Nested", name="Nested")
    MsgspecStruct(reference=nested_reference, fields=[])
    field = MsgspecDataModelField(
        name="nested",
        data_type=DataType(reference=nested_reference),
        default={"value": "Field("},
        required=False,
        has_default=True,
    )

    assert str(field) == "field(default_factory=lambda: convert({'value': 'Field('},  type=Nested))"
    assert field._get_field_data() == {"default_factory": "lambda: convert({'value': 'Field('},  type=Nested)"}
    assert IMPORT_MSGSPEC_FIELD in field.imports
    assert IMPORT_MSGSPEC_CONVERT in field.imports


def test_msgspec_empty_struct_default_uses_conversion_factory() -> None:
    """An empty object for a Struct still creates the declared model."""
    nested_reference = Reference(path="#/Nested", name="Nested")
    MsgspecStruct(reference=nested_reference, fields=[])
    field = MsgspecDataModelField(
        name="nested",
        data_type=DataType(reference=nested_reference),
        default={},
        required=False,
        has_default=True,
    )
    MsgspecStruct(reference=Reference(path="#/Owner", name="Owner"), fields=[field])

    assert str(field) == "field(default_factory=lambda: convert({},  type=Nested))"
    assert IMPORT_MSGSPEC_FIELD in field.imports
    assert IMPORT_MSGSPEC_CONVERT in field.imports
    assert IMPORT_MSGSPEC_UNSET not in field.imports


@pytest.mark.parametrize(
    ("container_type", "default", "expected"),
    [
        pytest.param(
            DataType(is_list=True),
            [],
            "field(default_factory=list)",
            id="list",
        ),
        pytest.param(
            DataType(is_dict=True),
            {},
            "field(default_factory=dict)",
            id="dict",
        ),
        pytest.param(
            DataType(is_set=True),
            set(),
            "field(default_factory=set)",
            id="set",
        ),
    ],
)
def test_msgspec_empty_struct_container_defaults_use_builtin_factory(
    container_type: DataType,
    default: dict[object, object] | list[object] | set[object],
    expected: str,
) -> None:
    """Empty Struct containers keep the allocation-only builtin factory."""
    nested_reference = Reference(path="#/Nested", name="Nested")
    MsgspecStruct(reference=nested_reference, fields=[])
    container_type.data_types = [DataType(reference=nested_reference)]
    field = MsgspecDataModelField(
        name="nested",
        data_type=DataType(data_types=[container_type]),
        default=default,
        required=False,
        has_default=True,
    )
    MsgspecStruct(reference=Reference(path="#/Owner", name="Owner"), fields=[field])

    assert str(field) == expected
    assert IMPORT_MSGSPEC_FIELD in field.imports
    assert IMPORT_MSGSPEC_CONVERT not in field.imports
    assert IMPORT_MSGSPEC_UNSET not in field.imports


def test_msgspec_optional_nested_factory_does_not_import_unused_unset() -> None:
    """A rendered nested factory owns field syntax without an UNSET value."""
    nested_reference = Reference(path="#/Nested", name="Nested")
    MsgspecStruct(reference=nested_reference, fields=[])
    field = MsgspecDataModelField(
        name="nested",
        data_type=DataType(reference=nested_reference),
        default=None,
        required=False,
        use_default_factory_for_optional_nested_models=True,
    )
    MsgspecStruct(reference=Reference(path="#/Owner", name="Owner"), fields=[field])

    assert str(field) == "field(default_factory=Nested)"
    assert IMPORT_MSGSPEC_FIELD in field.imports
    assert IMPORT_MSGSPEC_UNSET not in field.imports


def test_msgspec_import_extender_preserves_generic_field_imports() -> None:
    """The reusable import decorator leaves non-msgspec field imports unchanged."""

    @import_extender
    class GenericField(DataModelFieldBase):
        pass

    field = GenericField(
        name="value",
        data_type=DataType.from_import(IMPORT_DECIMAL),
        required=True,
    )

    assert field.imports == (IMPORT_DECIMAL,)


def test_msgspec_class_var_imports_ignore_instance_field_syntax() -> None:
    """Class variables do not inherit imports from instance-field rendering."""
    field = MsgspecDataModelField(
        name="value",
        data_type=DataType.from_import(IMPORT_DECIMAL),
        default=None,
        required=False,
        extras={"is_classvar": True, "default_factory": "list"},
    )

    assert field.imports == ()


def test_rendered_pydantic_v2_class_var_field_values_are_none() -> None:
    """Test class variable fields do not render Field or Annotated values."""
    field = PydanticV2DataModelField(
        name="name",
        data_type=DataType(type="str"),
        required=True,
        extras={"x-is-classvar": True},
        use_annotated=True,
    )
    rendered_field = _RenderedDataModelField(field, "")

    assert field.field is None
    assert rendered_field.field is None
    assert rendered_field.annotated is None


def test_field_semantic_policy_api() -> None:
    """Expose parser-facing field semantics without backend extras knowledge."""
    base_field = DataModelFieldBase(name="value", data_type=DataType(type="str"))
    msgspec_field = MsgspecDataModelField(
        name="constant",
        data_type=DataType(type="str"),
        extras={"is_classvar": True},
    )
    pydantic_field = PydanticV2DataModelField(
        name="constant",
        data_type=DataType(type="str"),
        required=True,
        extras={"x-is-classvar": True},
    )

    assert base_field.constructor_keyword_only is None
    base_field.mark_as_keyword_only()
    assert base_field.constructor_keyword_only is True
    assert base_field.is_class_var is False
    assert msgspec_field.is_class_var is True
    assert pydantic_field.is_class_var is True
    assert pydantic_field.type_hint == "ClassVar[str]"
    assert base_field.enable_structured_default_validation() is False
    assert pydantic_field.enable_structured_default_validation() is True
    assert pydantic_field.enable_structured_default_validation() is False
    assert pydantic_field.extras["validate_default"] is True


def test_rendered_data_model_field_caches_delegated_attributes() -> None:
    """Test delegated field attributes are stored directly on the rendered proxy."""

    @dataclass
    class DelegatedField:
        """Test field with counted attribute access."""

        calls: int = 0

        @property
        def value(self) -> str:
            """Return a counted delegated value."""
            self.calls += 1
            return "cached value"

    field = DelegatedField()
    rendered_field = _RenderedDataModelField(field, "docstring")

    assert rendered_field.docstring == "docstring"
    assert rendered_field.value == "cached value"
    assert rendered_field.value == "cached value"
    assert field.calls == 1
    assert rendered_field.__dict__["value"] == "cached value"


def test_rendered_data_model_field_preserves_missing_attribute_errors() -> None:
    """Test missing delegated field attributes still raise AttributeError."""
    rendered_field = _RenderedDataModelField(object(), "")

    with pytest.raises(AttributeError):
        _ = rendered_field.missing


def test_rendered_data_model_field_batches_field_and_annotated_values() -> None:
    """Test field and annotated template values share one lazy render call."""

    @dataclass
    class RenderValuesField:
        """Test field that batches rendered field values."""

        calls: int = 0

        def _rendered_field_values(self) -> tuple[str, str]:
            """Return counted rendered values."""
            self.calls += 1
            return "field value", "annotated value"

    field = RenderValuesField()
    rendered_field = _RenderedDataModelField(field, "")

    assert rendered_field.annotated == "annotated value"
    assert rendered_field.field == "field value"
    assert field.calls == 1
    assert rendered_field.__dict__["field"] == "field value"
    assert rendered_field.__dict__["annotated"] == "annotated value"


def test_jinja_environment_auto_reload_only_for_custom_templates(tmp_path: Path) -> None:
    """Test built-in templates disable Jinja auto-reload while custom templates keep it."""
    _get_environment.cache_clear()
    _get_environment_with_absolute_path.cache_clear()

    builtin_environment = _get_environment(Path(), None)
    custom_environment = _get_environment(Path(), tmp_path)
    missing_custom_subdir_environment = _get_environment(Path("pydantic_v2"), tmp_path)
    absolute_path_environment = _get_environment_with_absolute_path(tmp_path, Path())

    assert builtin_environment.auto_reload is False
    assert custom_environment.auto_reload is True
    assert missing_custom_subdir_environment.auto_reload is False
    assert absolute_path_environment.auto_reload is True


def test_pydantic_base_class_var_imports_do_not_require_field() -> None:
    """Test common Pydantic ClassVar fields skip Field() without render state."""
    field = PydanticBaseDataModelField(
        name="name",
        data_type=DataType(type="str"),
        required=True,
        extras={"x-is-classvar": True},
    )
    assert IMPORT_FIELD not in field.imports
    assert field.field is None


def test_pydantic_forced_required_assignment_imports_field() -> None:
    """A required override rendered as Field(...) must contribute its only import."""
    field = PydanticDataclassField(
        name="name",
        data_type=DataType(type="str"),
        required=True,
    )

    field.force_field_assignment()

    assert str(field) == "Field(...)"
    assert IMPORT_FIELD in field.imports


@pytest.mark.parametrize(
    ("use_default_with_required", "expected"),
    [
        pytest.param(False, "", id="required"),
        pytest.param(True, "Field(default_factory=list)", id="required-with-default"),
    ],
)
def test_pydantic_required_default_factory_respects_default_policy(
    *,
    use_default_with_required: bool,
    expected: str,
) -> None:
    """A required schema factory is used only when required defaults are enabled."""
    field = PydanticV2DataModelField(
        name="items",
        data_type=DataType(type="str", is_list=True),
        required=True,
        has_default=True,
        extras={"default_factory": "list"},
        use_default_with_required=use_default_with_required,
    )

    assert str(field) == expected


@pytest.mark.parametrize(
    ("default", "extras", "use_default_kwarg", "expected"),
    [
        pytest.param("value", {"kw_only": True}, False, "Field('value', kw_only=True)", id="scalar"),
        pytest.param(
            "value",
            {"kw_only": True},
            True,
            "Field(default='value', kw_only=True)",
            id="scalar-default-keyword",
        ),
        pytest.param(
            UNDEFINED,
            {"default_factory": "list"},
            False,
            "Field(default_factory=list)",
            id="factory",
        ),
    ],
)
def test_pydantic_annotated_dataclass_field_preserves_constructor_default(
    default: object,
    extras: dict[str, object],
    use_default_kwarg: bool,
    expected: str,
) -> None:
    """Annotated metadata must keep scalar and factory defaults on the dataclass RHS."""
    field = PydanticDataclassField(
        name="items",
        data_type=DataType(type="str"),
        default=default,
        required=False,
        has_default=True,
        extras=extras,
        use_annotated=True,
        use_default_kwarg=use_default_kwarg,
    )

    assert field.dataclass_field == expected


@pytest.mark.parametrize("use_default_kwarg", [False, True])
def test_pydantic_annotated_dataclass_field_omits_undefined_default(*, use_default_kwarg: bool) -> None:
    """Dataclass-only metadata must not turn the unresolved sentinel into Python source."""
    field = PydanticDataclassField(
        name="item",
        data_type=DataType(type="str"),
        default=UNDEFINED,
        required=False,
        has_default=False,
        extras={"kw_only": True},
        use_annotated=True,
        use_default_kwarg=use_default_kwarg,
    )

    assert field.dataclass_field == "Field(kw_only=True)"
    assert field.field == "Field(kw_only=True)"


def test_pydantic_dataclass_optional_nested_model_uses_default_factory() -> None:
    """The optional-nested-model flag recognizes Pydantic dataclass references."""
    nested_reference = Reference(path="#/Nested", name="Nested")
    PydanticDataclassModel(reference=nested_reference, fields=[])
    field = PydanticDataclassField(
        name="nested",
        data_type=DataType(reference=nested_reference),
        default=None,
        required=False,
        has_default=False,
        use_annotated=True,
        use_default_factory_for_optional_nested_models=True,
    )

    assert field.dataclass_field == "Field(default_factory=Nested)"
    assert field.field == "Field(default_factory=Nested)"
    assert field.has_default_factory_in_field is True
    assert field._get_constructor_default_info() == (True, False)
    assert field.dataclass_field == "Field(default_factory=Nested)"


def test_pydantic_dataclass_render_computes_plan_once() -> None:
    """Built-in dataclass rendering shares one local plan across policy and output."""
    plan_calls = 0

    class CountingField(PydanticDataclassField):
        def _get_field_render_plan(self) -> Any:
            nonlocal plan_calls
            plan_calls += 1
            return super()._get_field_render_plan()

    field = CountingField(
        name="value",
        data_type=DataType(type="str"),
        required=True,
        extras={"repr": False},
        use_annotated=True,
    )

    assert field._rendered_field_values() == ("Field(repr=False)", None)
    assert plan_calls == 1


def test_pydantic_missing_sentinel_checks_optional_factory_once() -> None:
    """The missing-sentinel policy reuses the effective factory decision."""
    factory_calls = 0

    class CountingField(PydanticV2DataModelField):
        def _get_default_factory_for_optional_nested_model(self) -> str | None:
            nonlocal factory_calls
            factory_calls += 1
            return super()._get_default_factory_for_optional_nested_model()

    field = CountingField(
        name="value",
        data_type=DataType(type="str"),
        required=False,
        use_missing_sentinel=True,
        use_default_factory_for_optional_nested_models=True,
    )

    assert field.use_missing_sentinel_default is True
    assert factory_calls == 1


@pytest.mark.parametrize("use_annotated", [False, True])
def test_pydantic_dataclass_field_rebuilds_from_structured_arguments(*, use_annotated: bool) -> None:
    """Dataclass defaults are inserted without parsing Field-like user metadata."""
    field = PydanticDataclassField(
        name="value",
        data_type=DataType(type="str"),
        default="value",
        required=False,
        has_default=True,
        extras={"title": "Field(", "examples": ["Field("], "kw_only": True},
        use_annotated=use_annotated,
        use_default_kwarg=True,
    )

    expected = "Field(default='value', examples=['Field('], kw_only=True, title='Field(')"
    assert field.dataclass_field == expected
    assert field.field == expected
    assert field.annotated is None


def test_pydantic_annotated_dataclass_field_skips_empty_assignment() -> None:
    """A plain required Annotated field does not synthesize an empty RHS."""
    field = PydanticDataclassField(
        name="item",
        data_type=DataType(type="str"),
        required=True,
        use_annotated=True,
    )

    assert field.dataclass_field is None


def test_pydantic_dataclass_annotated_assignment_uses_existing_template_contract() -> None:
    """Dataclass-only metadata should use the legacy field/annotated template branches."""
    assigned_field = PydanticDataclassField(
        name="assigned",
        data_type=DataType(type="str"),
        required=True,
        extras={"repr": False},
        use_annotated=True,
    )
    annotated_field = PydanticDataclassField(
        name="annotated",
        data_type=DataType(type="str"),
        required=True,
        extras={"title": "Annotated"},
        use_annotated=True,
    )

    assert assigned_field.annotated is None
    assert assigned_field.requires_dataclass_field_assignment is True
    assert assigned_field.field == "Field(repr=False)"
    assert assigned_field._rendered_field_values() == ("Field(repr=False)", None)
    assert IMPORT_ANNOTATED not in assigned_field.imports
    assert annotated_field.annotated == "Annotated[str, Field(title='Annotated')]"
    assert annotated_field._rendered_field_values() == (
        "Field(title='Annotated')",
        "Annotated[str, Field(title='Annotated')]",
    )
    assert IMPORT_ANNOTATED in annotated_field.imports


def test_pydantic_v2_leaf_field_imports_skip_discriminator_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test leaf fields do not walk all nested data types for impossible discriminators."""
    field = PydanticV2DataModelField(
        name="name",
        data_type=DataType(type="str"),
        required=True,
    )

    def fail_all_data_types(_self: DataType) -> tuple[DataType, ...]:
        message = "unexpected discriminator scan"
        raise AssertionError(message)

    monkeypatch.setattr(DataType, "all_data_types", property(fail_all_data_types))

    assert IMPORT_FIELD not in field.imports
    with pytest.raises(AssertionError, match="unexpected discriminator scan"):
        tuple(DataType(type="str").all_data_types)


def test_pydantic_v2_nested_discriminator_still_imports_field() -> None:
    """Test discriminator imports still work for nested data types."""
    field = PydanticV2DataModelField(
        name="item",
        data_type=DataType(data_types=[DataType(type="Pet", discriminator="pet_type")]),
        required=True,
    )

    assert IMPORT_FIELD in field.imports


class _MsgspecFieldKwargs(TypedDict, total=False):
    nullable: bool
    default: str
    has_default: bool
    type_has_null: bool
    extras: dict[str, int]
    use_annotated: bool


def _msgspec_field(
    data_type: DataType,
    *,
    required: bool = False,
    **kwargs: Unpack[_MsgspecFieldKwargs],
) -> MsgspecDataModelField:
    field = MsgspecDataModelField(name="value", data_type=data_type, required=required, **kwargs)
    MsgspecStruct(fields=[field], reference=Reference(path="Model", original_name="Model", name="Model"))
    return field


class _FallbackMsgspecField(MsgspecDataModelField):
    pass


def test_msgspec_unset_type_hint_uses_structured_ordered_union() -> None:
    """Test msgspec UnsetType is added through an ordered DataType union."""
    data_type = DataType(data_types=[DataType(type="str"), DataType(type=NONE)])
    field = _msgspec_field(data_type)

    assert field.type_hint == "Union[str, None, UnsetType]"
    assert data_type.preserve_union_member_order is False
    assert field.imports == (IMPORT_MSGSPEC_UNSETTYPE, IMPORT_UNION, IMPORT_MSGSPEC_UNSET)


def test_msgspec_unset_type_hint_handles_empty_and_simple_types() -> None:
    """Test msgspec UnsetType structural unions cover empty and simple field types."""
    assert _msgspec_field(DataType()).type_hint == "UnsetType"
    assert _msgspec_field(DataType(type="str")).type_hint == "Union[str, UnsetType]"
    assert _msgspec_field(DataType(type="str", is_optional=True)).type_hint == "Union[str, None, UnsetType]"
    assert _msgspec_field(DataType(data_types=[DataType(type="str"), DataType(type="int")])).type_hint == (
        "Union[str, int, UnsetType]"
    )
    none_field = _msgspec_field(DataType(is_optional=True))
    assert none_field.type_hint == "Union[None, UnsetType]"
    assert none_field.imports == (IMPORT_MSGSPEC_UNSETTYPE, IMPORT_UNION, IMPORT_MSGSPEC_UNSET)
    raw_none_field = _msgspec_field(DataType(type=NONE))
    assert raw_none_field.type_hint == "Union[None, UnsetType]"
    assert raw_none_field.imports == (IMPORT_MSGSPEC_UNSETTYPE, IMPORT_UNION, IMPORT_MSGSPEC_UNSET)


@pytest.mark.parametrize(
    "python_version",
    list(PythonVersion),
)
@pytest.mark.parametrize("use_union_operator", [False, True], ids=["typing-union", "union-operator"])
@pytest.mark.parametrize(
    ("field_kwargs", "has_forward_reference"),
    [
        pytest.param({}, False, id="implicit-nullability"),
        pytest.param({"nullable": False}, False, id="explicit-non-null"),
        pytest.param({"default": "value", "has_default": True}, False, id="schema-default"),
        pytest.param({}, True, id="forward-reference"),
    ],
)
def test_msgspec_simple_unset_fast_path_matches_graph_fallback(
    python_version: PythonVersion,
    use_union_operator: bool,
    field_kwargs: _MsgspecFieldKwargs,
    has_forward_reference: bool,
) -> None:
    """CI compares every supported target with the graph fallback as the source of truth."""
    manager = MsgspecDataTypeManager(
        python_version=python_version,
        use_standard_collections=True,
        use_generic_container_types=True,
        use_union_operator=use_union_operator,
        treat_dot_as_module=True,
        use_serialize_as_any=True,
    )
    for data_type in (
        manager.data_type(type="str"),
        manager.data_type(type="int"),
        manager.data_type.from_import(IMPORT_DECIMAL),
    ):
        fast_field = _msgspec_field(deepcopy(data_type), **field_kwargs)
        assert fast_field.parent is not None
        fast_field.parent.has_forward_reference = has_forward_reference
        fallback_field = _FallbackMsgspecField(
            name="value",
            data_type=deepcopy(data_type),
            required=False,
            **field_kwargs,
        )
        fallback_model = MsgspecStruct(
            fields=[fallback_field],
            reference=Reference(path="FallbackModel", name="FallbackModel"),
        )
        fallback_model.has_forward_reference = has_forward_reference

        assert fast_field._get_simple_unset_type_hint() is not None
        assert fallback_field._get_simple_unset_type_hint() is None
        assert fast_field.type_hint == fallback_field.type_hint
        assert fast_field.imports == fallback_field.imports
        assert len(fast_field.imports) == len(set(fast_field.imports))


@pytest.mark.parametrize(
    ("import_", "expected_imports"),
    [
        pytest.param(
            IMPORT_MSGSPEC_UNSETTYPE,
            (IMPORT_MSGSPEC_UNSETTYPE, IMPORT_UNION, IMPORT_MSGSPEC_UNSET),
            id="existing-unset",
        ),
        pytest.param(
            IMPORT_UNION,
            (IMPORT_UNION, IMPORT_MSGSPEC_UNSETTYPE, IMPORT_MSGSPEC_UNSET),
            id="existing-union",
        ),
    ],
)
def test_msgspec_simple_unset_fast_path_deduplicates_trailing_imports(
    import_: Import,
    expected_imports: tuple[Import, ...],
) -> None:
    """Direct rendering retains ordered imports already supplied by the leaf."""
    field = _msgspec_field(DataType(type="str", import_=import_))

    assert field._get_simple_unset_type_hint() == "Union[str, UnsetType]"
    assert field.imports == expected_imports


@pytest.mark.parametrize(
    "data_type",
    [
        pytest.param(DataType(), id="empty"),
        pytest.param(DataType(type=NONE), id="none"),
        pytest.param(DataType(type="str", is_optional=True), id="optional"),
        pytest.param(DataType(data_types=[DataType(type="str"), DataType(type="int")]), id="union"),
        pytest.param(DataType(data_types=[DataType(type="str")]), id="single-child"),
        *[
            pytest.param(DataType(**{flag: True}), id=f"bare-{flag.removeprefix('is_').replace('_', '-')}")
            for flag in ("is_list", "is_dict", "is_set", "is_frozen_set", "is_mapping", "is_sequence", "is_tuple")
        ],
        pytest.param(DataType(type="str", is_list=True), id="typed-list"),
        pytest.param(DataType(type="str", is_mapping=True), id="typed-mapping"),
        pytest.param(DataType(is_list=True, data_types=[DataType(type="str")]), id="nested-container"),
        pytest.param(
            DataType(data_types=[DataType(type="str"), DataType(type=NONE)]),
            id="nested-optional-union",
        ),
        pytest.param(DataType(reference=Reference(path="#/Referenced", name="Referenced")), id="reference"),
        pytest.param(DataType(type="Custom", is_custom_type=True), id="custom-type"),
        pytest.param(DataType(type="constr", is_func=True, kwargs={"min_length": 1}), id="function-kwargs"),
        pytest.param(DataType(type="str", alias="Alias"), id="alias"),
        pytest.param(DataType(literals=["value"]), id="literal"),
        pytest.param(DataType(enum_member_literals=[("Status", "ready")]), id="enum-literal"),
        pytest.param(DataType(type="str", discriminator="kind"), id="discriminator"),
        pytest.param(DataType.from_import(IMPORT_MSGSPEC_UNSETTYPE), id="already-unset"),
        pytest.param(
            DataType(
                is_dict=True, data_types=[DataType(type="str")], dict_key=DataType(type="Custom", is_custom_type=True)
            ),
            id="custom-dict-key",
        ),
        pytest.param(DataType(type="Annotated[str, Meta(gt=0)]"), id="raw-annotation"),
        pytest.param(DataType(type="Custom[str]"), id="raw-generic"),
        pytest.param(DataType(type="Optional"), id="typing-optional-identifier"),
        pytest.param(DataType(type="Annotated"), id="typing-annotated-identifier"),
        pytest.param(DataType(type="str", alias="Annotated[str, Meta(gt=0)]"), id="raw-alias"),
        pytest.param(
            DataType(is_list=True, data_types=[DataType(type="Annotated[str, Meta(gt=0)]")]),
            id="nested-raw-annotation",
        ),
    ],
)
def test_msgspec_simple_unset_fast_path_falls_back_for_complex_data_types(data_type: DataType) -> None:
    """Complex data types keep the exact graph-based hint and import behavior."""
    original = deepcopy(data_type)
    field = _msgspec_field(data_type)
    fallback_field = _FallbackMsgspecField(name="value", data_type=original, required=False)
    MsgspecStruct(fields=[fallback_field], reference=Reference(path="FallbackModel", name="FallbackModel"))

    assert field._get_simple_unset_type_hint() is None
    assert field.type_hint == fallback_field.type_hint
    assert field.imports == fallback_field.imports


@pytest.mark.parametrize(
    ("nested", "expected_type_hint"),
    [
        (False, "Union[Optional[NullableModel], UnsetType]"),
        (True, "Union[List[Optional[NullableModel]], UnsetType]"),
    ],
)
def test_msgspec_simple_unset_fast_path_does_not_mutate_nullable_reference(
    nested: bool,
    expected_type_hint: str,
) -> None:
    """Nullable references remain isolated in top-level and nested fallback copies."""
    reference = Reference(path="#/NullableModel", name="NullableModel")
    MsgspecStruct(reference=reference, fields=[], nullable=True)
    child = DataType(reference=reference)
    field = _msgspec_field(DataType(is_list=True, data_types=[child]) if nested else child)

    assert field._get_simple_unset_type_hint() is None
    assert field.type_hint == expected_type_hint
    assert IMPORT_OPTIONAL in field.imports
    assert child.is_optional is False


def test_data_type_deepcopy_keeps_the_source_of_a_nullable_reference_none() -> None:
    """Deep copies keep a dropped None dropped and a model's None coming back; no generation copies such a type."""
    reference = Reference(path="#/NullableModel", name="NullableModel")
    reference.source = ReferenceSource(nullable=True)
    data_type = DataType(reference=reference)
    assert data_type.type_hint == "Optional[NullableModel]"
    own, from_model = deepcopy(data_type), deepcopy(data_type)
    own._strip_null(own=True)
    from_model._strip_null(own=False)

    assert deepcopy(own).type_hint == "NullableModel"
    assert deepcopy(from_model).type_hint == "Optional[NullableModel]"


def test_msgspec_simple_unset_fast_path_falls_back_for_nested_optional_union() -> None:
    """A nested unordered None union does not mutate the caller-owned graph."""
    child = DataType(data_types=[DataType(type="str"), DataType(type=NONE)])
    field = _msgspec_field(DataType(is_list=True, data_types=[child]))

    assert child.is_optional is False
    assert field._get_simple_unset_type_hint() is None
    assert field.type_hint == "Union[List[Optional[str]], UnsetType]"
    assert IMPORT_OPTIONAL in field.imports
    assert child.is_optional is False


def test_msgspec_simple_unset_fast_path_falls_back_for_explicit_type_null() -> None:
    """A parser-declared null member remains in the graph-rendered annotation."""
    field = _msgspec_field(
        DataType(type="str"),
        nullable=False,
        type_has_null=True,
    )

    assert field._get_simple_unset_type_hint() is None
    assert field.type_hint == "Union[str, None, UnsetType]"
    assert field.imports == (IMPORT_MSGSPEC_UNSETTYPE, IMPORT_UNION, IMPORT_MSGSPEC_UNSET)


def test_msgspec_simple_unset_fast_path_falls_back_for_annotated_field() -> None:
    """Annotated msgspec fields retain metadata-aware graph rendering."""
    field = _msgspec_field(
        DataType(type="str"),
        extras={"max_length": 5},
        use_annotated=True,
    )

    assert field._get_simple_unset_type_hint() is None
    assert field.type_hint == "Union[str, UnsetType]"
    assert field.annotated == "Union[Annotated[str, Meta(max_length=5)], UnsetType]"


def test_msgspec_simple_unset_fast_path_supports_custom_template() -> None:
    """Custom templates observe the same field values without forcing a graph copy."""
    baseline = _msgspec_field(DataType(type="str"))
    field = MsgspecDataModelField(name="value", data_type=DataType(type="str"), required=False)
    MsgspecStruct(
        fields=[field],
        reference=Reference(path="Model", original_name="Model", name="Model"),
        custom_template_dir=Path("custom-templates"),
    )

    assert field._get_simple_unset_type_hint() == "Union[str, UnsetType]"
    assert (field.type_hint, field.imports, field.annotated, field.field, field.data_type.type_hint) == (
        baseline.type_hint,
        baseline.imports,
        baseline.annotated,
        baseline.field,
        baseline.data_type.type_hint,
    )


def test_msgspec_simple_unset_fast_path_falls_back_for_subclasses() -> None:
    """External field, model, and data type subclasses retain virtual behavior."""

    class CustomMsgspecField(MsgspecDataModelField):
        pass

    class CustomMsgspecStruct(MsgspecStruct):
        pass

    class CustomDataType(DataType):
        pass

    SpoofedContextDataType = type(
        "ContextDataType",
        (DataType,),
        {"__module__": DataType.__module__},
    )

    fields = [
        CustomMsgspecField(name="value", data_type=DataType(type="str"), required=False),
        MsgspecDataModelField(name="value", data_type=DataType(type="str"), required=False),
        _msgspec_field(CustomDataType(type="str")),
        _msgspec_field(SpoofedContextDataType(type="str")),
    ]
    MsgspecStruct(fields=fields[:1], reference=Reference(path="FieldModel", name="FieldModel"))
    CustomMsgspecStruct(
        fields=fields[1:2],
        reference=Reference(path="StructModel", name="StructModel"),
    )

    assert all(field._get_simple_unset_type_hint() is None for field in fields)
    assert {field.type_hint for field in fields} == {"Union[str, UnsetType]"}


def test_msgspec_unset_import_is_limited_to_struct_fields() -> None:
    """Test msgspec UNSET value imports match Struct-only field emission."""
    field = MsgspecDataModelField(name="value", data_type=DataType(type="str"), required=False)
    annotated_field = MsgspecDataModelField(
        name="value",
        data_type=DataType(type="str"),
        required=False,
        extras={"max_length": 5},
        use_annotated=True,
    )

    assert IMPORT_MSGSPEC_UNSET not in field.imports
    assert IMPORT_MSGSPEC_UNSET not in annotated_field.imports


def test_msgspec_data_type_copy_preserves_structural_children() -> None:
    """Test msgspec annotation copies preserve dict keys and kwargs without parent links."""
    field = _msgspec_field(DataType(type="str"))
    data_type = DataType(
        type="Value",
        is_dict=True,
        dict_key=DataType(type="Key"),
        kwargs={"strict": True},
    )

    copied = field._copy_data_type(data_type)

    assert copied is not data_type
    assert copied.parent is None
    assert copied.children == []
    assert copied.dict_key is not data_type.dict_key
    assert copied.dict_key.type == "Key"
    assert copied.kwargs == {"strict": True}


def test_msgspec_annotated_unset_keeps_none_outside_annotated() -> None:
    """Test msgspec Annotated fields keep None outside the Annotated branch."""
    field = _msgspec_field(
        DataType(type="str", is_optional=True),
        extras={"max_length": 5},
        use_annotated=True,
    )

    assert field.annotated == "Union[Annotated[str, Meta(max_length=5)], None, UnsetType]"
    assert field.needs_annotated_import is True
    assert field.imports == (
        IMPORT_MSGSPEC_UNSETTYPE,
        IMPORT_UNION,
        IMPORT_ANNOTATED,
        IMPORT_MSGSPEC_META,
        IMPORT_MSGSPEC_UNSET,
    )


def test_msgspec_required_annotated_nullable_keeps_none_outside_annotated() -> None:
    """Test required msgspec Annotated nullable fields keep None outside Annotated."""
    field = MsgspecDataModelField(
        name="value",
        data_type=DataType(type="str", is_optional=True),
        required=True,
        extras={"max_length": 5},
        use_annotated=True,
    )
    MsgspecStruct(fields=[field], reference=Reference(path="Model", original_name="Model", name="Model"))

    assert field.annotated == "Union[Annotated[str, Meta(max_length=5)], None]"
    assert field.imports == (
        IMPORT_UNION,
        IMPORT_ANNOTATED,
        IMPORT_MSGSPEC_META,
    )


def test_typed_dict_empty_original_name_uses_functional_syntax_and_key() -> None:
    """Preserve an empty source key instead of falling back to the generated field name."""
    field = TypedDictDataModelField(
        name="field_",
        original_name="",
        data_type=DataType(type="str"),
        required=True,
    )
    model = TypedDictModel(
        fields=[field],
        reference=Reference(path="EmptyField", original_name="EmptyField", name="EmptyField"),
    )

    rendered = model.render()
    assert model.is_functional_syntax
    assert not field.key
    assert "EmptyField = TypedDict('EmptyField', {" in rendered
    assert "    '': str," in rendered


@pytest.mark.parametrize("source_name", ["", "value"], ids=["empty", "regular"])
def test_typed_dict_functional_syntax_replaces_inherited_source_key(source_name: str) -> None:
    """Render only the child override when an inherited source key is repeated."""
    base_reference = Reference(path="Base", original_name="Base", name="Base")
    TypedDictModel(
        fields=[
            TypedDictDataModelField(
                name="base_value",
                original_name=source_name,
                data_type=DataType(type="str"),
                required=False,
            )
        ],
        reference=base_reference,
    )
    child = TypedDictModel(
        fields=[
            TypedDictDataModelField(
                name="child_value",
                original_name=source_name,
                data_type=DataType(type="int"),
                required=True,
            ),
            TypedDictDataModelField(
                name="trigger_key",
                original_name="trigger-key",
                data_type=DataType(type="bool"),
                required=True,
            ),
        ],
        base_classes=[base_reference],
        reference=Reference(path="Child", original_name="Child", name="Child"),
    )

    matching_fields = [field for field in child.all_fields if field.original_name == source_name]
    rendered = child.render()
    assert len(matching_fields) == 1
    assert matching_fields[0].name == "child_value"
    assert matching_fields[0].type_hint == "int"
    assert rendered.count(f"'{source_name}':") == 1
    assert f"'{source_name}': int," in rendered


def test_ordered_union_type_hint_handles_empty_and_discriminator() -> None:
    """Test ordered union rendering keeps explicit order without Optional normalization."""
    empty_union = DataType(data_types=[DataType(), DataType()], preserve_union_member_order=True)
    deduplicated_union = DataType(
        data_types=[DataType(type="str"), DataType(type="str")],
        preserve_union_member_order=True,
    )
    discriminated_union = DataType(
        data_types=[DataType(type="str"), DataType(type="int")],
        discriminator="kind",
        preserve_union_member_order=True,
    )

    assert empty_union.type_hint == ANY
    assert deduplicated_union.type_hint == "str"
    assert discriminated_union.type_hint == "Annotated[Union[str, int], Field(discriminator='kind')]"


def test_data_model_exception() -> None:
    """Test DataModel raises exception when TEMPLATE_FILE_PATH is undefined."""
    field = DataModelFieldBase(name="a", data_type=DataType(type="str"), default="abc", required=True)
    with pytest.raises(Exception, match="TEMPLATE_FILE_PATH is undefined"):
        C(
            fields=[field],
            reference=Reference(path="abc", original_name="abc", name="abc"),
        )


def test_replace_children_in_models_updates_matching_owner_references() -> None:
    """Test replacing reference children for only the selected owner models."""
    old_reference = Reference(path="Old", original_name="Old", name="Old")
    new_reference = Reference(path="Selected", original_name="Selected", name="Selected")
    target_model = BaseModel(fields=[], reference=old_reference)
    selected_type = DataType(data_types=[DataType(reference=old_reference)])
    selected_reference_type = selected_type.data_types[0]
    selected_reference_type.parent = selected_type
    selected_field = DataModelFieldBase(data_type=selected_type)
    selected_model = BaseModel(
        fields=[selected_field],
        reference=Reference(path="Selected", original_name="Selected", name="Selected"),
    )
    other_type = DataType(reference=old_reference)
    BaseModel(
        fields=[DataModelFieldBase(data_type=other_type)],
        reference=Reference(path="Other", original_name="Other", name="Other"),
    )

    assert not selected_field.self_reference()

    target_model.replace_children_in_models([selected_model], new_reference)

    assert selected_field.self_reference()
    assert selected_reference_type.reference is new_reference
    assert other_type.reference is old_reference
    assert [child is selected_reference_type for child in old_reference.children] == [False]
    assert [child is selected_reference_type for child in new_reference.children] == [True]


def test_replace_children_in_models_invalidates_direct_model_owner() -> None:
    """A redirected base-class reference refreshes its owning model's render identity."""
    old_reference = Reference(path="Old", original_name="Old", name="Old")
    new_reference = Reference(path="New", original_name="New", name="New")
    target_model = BaseModel(fields=[], reference=old_reference)
    selected_model = BaseModel(
        fields=[],
        base_classes=[old_reference],
        reference=Reference(path="Selected", original_name="Selected", name="Selected"),
    )
    base_class = selected_model.base_classes[0]
    shared_owner_model = BaseModel(
        fields=[],
        reference=Reference(path="SharedOwner", original_name="SharedOwner", name="SharedOwner"),
    )
    shared_owner_model.base_classes = [base_class]
    ignored_owner_model = BaseModel(
        fields=[],
        base_classes=[old_reference],
        reference=Reference(path="IgnoredOwner", original_name="IgnoredOwner", name="IgnoredOwner"),
    )
    ignored_base_class = ignored_owner_model.base_classes[0]
    original_key = selected_model.get_dedup_key()
    shared_owner_key = shared_owner_model.get_dedup_key()
    ignored_owner_key = ignored_owner_model.get_dedup_key()

    target_model.replace_children_in_models([selected_model, shared_owner_model], new_reference)

    assert base_class.reference is new_reference
    assert ignored_base_class.reference is old_reference
    assert selected_model.get_dedup_key() != original_key
    assert shared_owner_model.get_dedup_key() != shared_owner_key
    assert ignored_owner_model.get_dedup_key() == ignored_owner_key


def test_data_field() -> None:
    """Test DataModelFieldBase type hint generation for various configurations."""
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(is_list=True),
        required=True,
        is_list=True,
        is_union=True,
    )
    assert field.type_hint == "List"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(is_list=True),
        required=True,
        is_list=True,
        is_union=False,
    )
    assert field.type_hint == "List"
    field = DataModelFieldBase(name="a", data_type=DataType(), required=False)
    assert field.type_hint == "None"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(is_list=True),
        required=False,
        is_list=True,
        is_union=True,
    )
    assert field.type_hint == "Optional[List]"
    field = DataModelFieldBase(name="a", data_type=DataType(), required=False, is_list=False, is_union=True)
    assert field.type_hint == "None"
    field = DataModelFieldBase(name="a", data_type=DataType(), required=False, is_list=False, is_union=False)
    assert field.type_hint == "None"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(is_list=True),
        required=False,
        is_list=True,
        is_union=False,
    )
    assert field.type_hint == "Optional[List]"
    field = DataModelFieldBase(name="a", data_type=DataType(type="str"), required=True)
    assert field.type_hint == "str"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(type="str", is_list=True),
        required=True,
    )
    assert field.type_hint == "List[str]"
    field = DataModelFieldBase(name="a", data_type=DataType(type="str"), required=True)
    assert field.type_hint == "str"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(type="str"),
        required=True,
    )
    assert field.type_hint == "str"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(type="str", is_list=True),
        required=True,
    )
    assert field.type_hint == "List[str]"
    field = DataModelFieldBase(name="a", data_type=DataType(type="str"), required=False)
    assert field.type_hint == "Optional[str]"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(
            type="str",
            is_list=True,
        ),
        required=False,
    )
    assert field.type_hint == "Optional[List[str]]"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(type="str"),
        required=False,
    )
    assert field.type_hint == "Optional[str]"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(type="str"),
        required=False,
    )
    assert field.type_hint == "Optional[str]"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(
            type="str",
            is_list=True,
        ),
        required=False,
    )
    assert field.type_hint == "Optional[List[str]]"

    field = DataModelFieldBase(
        name="a",
        data_type=DataType(data_types=[DataType(type="str"), DataType(type="int")]),
        required=True,
    )
    assert field.type_hint == "Union[str, int]"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(
            data_types=[DataType(type="str"), DataType(type="int")],
            is_list=True,
        ),
        required=True,
    )
    assert field.type_hint == "List[Union[str, int]]"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(data_types=[DataType(type="str"), DataType(type="int")]),
        required=True,
    )
    assert field.type_hint == "Union[str, int]"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(data_types=[DataType(type="str"), DataType(type="int")]),
        required=True,
    )
    assert field.type_hint == "Union[str, int]"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(data_types=[DataType(type="str"), DataType(type="int")], is_list=True),
        required=True,
    )
    assert field.type_hint == "List[Union[str, int]]"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(data_types=[DataType(type="str"), DataType(type="int")]),
        required=False,
    )
    assert field.type_hint == "Optional[Union[str, int]]"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(
            data_types=[DataType(type="str"), DataType(type="int")],
            is_list=True,
        ),
        required=False,
    )
    assert field.type_hint == "Optional[List[Union[str, int]]]"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(data_types=[DataType(type="str"), DataType(type="int")]),
        required=False,
    )
    assert field.type_hint == "Optional[Union[str, int]]"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(data_types=[DataType(type="str"), DataType(type="int")]),
        required=False,
    )
    assert field.type_hint == "Optional[Union[str, int]]"
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(data_types=[DataType(type="str"), DataType(type="int")], is_list=True),
        required=False,
    )
    assert field.type_hint == "Optional[List[Union[str, int]]]"

    field = DataModelFieldBase(name="a", data_type=DataType(is_list=True), required=False)
    assert field.type_hint == "Optional[List]"


@pytest.mark.parametrize(
    "data_type",
    [
        DataType(data_types=[DataType(type="str")]),
        DataType(is_dict=True, dict_key=DataType(type="str")),
        DataType(literals=["value"]),
        DataType(enum_member_literals=[("EnumModel", "VALUE")]),
        DataType(type=ANY, is_optional=True),
    ],
)
def test_field_import_fast_path_skips_complex_data_types(data_type: DataType) -> None:
    """Test that complex DataTypes keep the side-effect-preserving import path."""
    field = DataModelFieldBase(name="a", data_type=data_type, required=True)

    assert field._can_collect_imports_without_type_hint(needs_annotated=False) is False


def test_field_import_fast_path_skips_annotated_fields() -> None:
    """Test that annotated fields keep the rendered type-hint import path."""
    field = DataModelFieldBase(name="a", data_type=DataType(type="str"), required=True)

    assert field._can_collect_imports_without_type_hint(needs_annotated=True) is False


def test_field_import_fast_path_skips_forward_reference_parent() -> None:
    """Test fields in forward-reference models keep rendered type-hint import detection."""
    field = DataModelFieldBase(name="a", data_type=DataType(type="str"), required=False)
    model = BaseModel(
        fields=[field],
        reference=Reference(path="Model", original_name="Model", name="Model"),
    )
    model.has_forward_reference = True

    assert field._can_collect_imports_without_type_hint(needs_annotated=False) is False


@pytest.mark.parametrize("type_name", ["Optional[str]", "Union[str, int]"])
def test_field_import_fast_path_skips_explicit_typing_names(type_name: str) -> None:
    """Test explicit typing names keep legacy import detection."""
    field = DataModelFieldBase(name="a", data_type=DataType(type=type_name), required=True)

    assert field._can_collect_imports_without_type_hint(needs_annotated=False) is False


@pytest.mark.parametrize(
    ("field", "expected_imports"),
    [
        (DataModelFieldBase(name="a", data_type=DataType(type="str"), required=False), (IMPORT_OPTIONAL,)),
        (
            DataModelFieldBase(
                name="a",
                data_type=DataType(type="str"),
                required=True,
                nullable=True,
            ),
            (IMPORT_OPTIONAL,),
        ),
        (
            DataModelFieldBase(
                name="a",
                data_type=DataType(type="str"),
                required=False,
                nullable=False,
            ),
            (),
        ),
        (
            DataModelFieldBase(
                name="a",
                data_type=DataType(type="str"),
                required=True,
                type_has_null=True,
            ),
            (IMPORT_OPTIONAL,),
        ),
        (
            DataModelFieldBase(
                name="a",
                data_type=DataType(type="str", is_optional=True),
                required=False,
            ),
            (IMPORT_OPTIONAL,),
        ),
        (
            DataModelFieldBase(
                name="a",
                data_type=DataType(type="str", use_union_operator=True),
                required=False,
            ),
            (),
        ),
        (
            DataModelFieldBase(
                name="a",
                data_type=DataType(type="Decimal", import_=IMPORT_DECIMAL),
                required=False,
            ),
            (IMPORT_DECIMAL, IMPORT_OPTIONAL),
        ),
        (DataModelFieldBase(name="a", data_type=DataType(), required=False), ()),
    ],
)
def test_field_import_fast_path_collects_simple_data_type_imports(
    field: DataModelFieldBase,
    expected_imports: tuple[Import, ...],
) -> None:
    """Test simple DataTypes collect imports without rendering type hints."""
    assert field.imports == expected_imports


def test_field_import_fast_path_collects_nullable_reference_import() -> None:
    """Test reference source nullability contributes Optional without rendering type hints."""
    reference = Reference(path="#/definitions/User", name="User")
    reference.source = ReferenceSource(nullable=True)
    field = DataModelFieldBase(name="user", data_type=DataType(reference=reference), required=True)

    assert field.imports == (IMPORT_OPTIONAL,)
    assert field.data_type.is_optional is False


def test_field_import_fast_path_ignores_nullable_alias_reference() -> None:
    """Test nullable aliases do not make the referencing field import Optional."""
    reference = Reference(path="#/definitions/UserAlias", name="UserAlias")
    reference.source = ReferenceSource(nullable=True, is_alias=True)
    field = DataModelFieldBase(name="user", data_type=DataType(reference=reference), required=True)

    assert field.imports == ()


def test_field_import_fallback_collects_annotated_import() -> None:
    """Test the fallback path includes Annotated when requested."""
    field = DataModelFieldBase(name="a", data_type=DataType(type="str"), required=True)

    assert field._collect_field_imports(needs_annotated=True) == (IMPORT_ANNOTATED,)


@pytest.mark.parametrize(
    ("type_name", "expected_imports"),
    [
        ("Optional[str]", (IMPORT_OPTIONAL,)),
        ("Union[str, int]", (IMPORT_UNION,)),
        ("Annotated[str, Field()]", (IMPORT_ANNOTATED,)),
    ],
)
def test_field_import_fallback_collects_explicit_typing_names(
    type_name: str,
    expected_imports: tuple[Import, ...],
) -> None:
    """Test explicit typing names still collect Optional and Union imports."""
    field = DataModelFieldBase(name="a", data_type=DataType(type=type_name), required=True)

    assert field.imports == expected_imports


def test_field_import_fallback_uses_structured_union_requirements(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test complex unions collect imports without scanning rendered type-hint text."""
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(data_types=[DataType(type="str"), DataType(type="int"), DataType(type=NONE)]),
        required=True,
    )

    def fail_type_hint(_self: DataModelFieldBase) -> str:  # pragma: no cover
        msg = "unexpected type_hint render"
        raise AssertionError(msg)

    monkeypatch.setattr(DataModelFieldBase, "type_hint", property(fail_type_hint))

    assert field.imports == (IMPORT_OPTIONAL, IMPORT_UNION)


@pytest.mark.parametrize(
    ("data_type", "expected_imports"),
    [
        (DataType(data_types=[DataType(type="str"), DataType(type=NONE)]), (IMPORT_OPTIONAL,)),
        (DataType(data_types=[DataType(type="str"), DataType(type="str")]), (IMPORT_OPTIONAL,)),
        (DataType(data_types=[DataType(type=NONE)]), ()),
        (DataType(data_types=[DataType(type=NONE), DataType(type=NONE)]), (IMPORT_ANY, IMPORT_OPTIONAL)),
        (DataType(data_types=[DataType(type="str")]), (IMPORT_OPTIONAL,)),
        (DataType(data_types=[DataType(type="str", discriminator="kind")]), (IMPORT_ANNOTATED, IMPORT_OPTIONAL)),
        (DataType(data_types=[DataType(type="str")], is_optional=True), (IMPORT_OPTIONAL,)),
    ],
)
def test_field_import_fallback_handles_structured_optional_edges(
    data_type: DataType,
    expected_imports: tuple[Import, ...],
) -> None:
    """Test structured import detection preserves Optional edge cases."""
    field = DataModelFieldBase(name="a", data_type=data_type, required=False)

    assert field.imports == expected_imports


def test_field_import_fallback_collects_explicit_typing_names_with_ast() -> None:
    """Test explicit type strings are parsed structurally for typing imports."""
    field = DataModelFieldBase(name="a", data_type=DataType(type="list[Union[str, int]]"), required=True)

    assert field.imports == (IMPORT_UNION,)


def test_field_import_fallback_collects_dict_key_typing_names() -> None:
    """Test dict key annotations contribute structured typing imports."""
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(is_dict=True, dict_key=DataType(type="Optional[str]")),
        required=True,
    )

    assert field.imports == (IMPORT_DICT, IMPORT_OPTIONAL)


def test_field_import_fallback_collects_nullable_reference_in_union() -> None:
    """Test nullable references inside complex types contribute Optional structurally."""
    reference = Reference(path="#/definitions/User", name="User")
    reference.source = ReferenceSource(nullable=True)
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(data_types=[DataType(reference=reference), DataType(type="int")]),
        required=True,
    )

    assert field.imports == (IMPORT_OPTIONAL, IMPORT_UNION)


def test_field_import_cache_normalizes_union_on_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test cached import lookup still applies union Optional side effects."""
    DataModelFieldBase._field_imports_cache.clear()
    try:
        primed_field = DataModelFieldBase(
            name="a",
            data_type=DataType(data_types=[DataType(type="str"), DataType(type=NONE)]),
            required=True,
        )

        assert primed_field.imports == (IMPORT_OPTIONAL,)
        assert primed_field.data_type.is_optional is True

        uncached = Mock(side_effect=AssertionError("unexpected uncached import collection"))
        monkeypatch.setattr(DataModelFieldBase, "_collect_field_imports_uncached", uncached)
        cached_field = DataModelFieldBase(
            name="a",
            data_type=DataType(data_types=[DataType(type="str"), DataType(type=NONE)]),
            required=True,
        )

        assert cached_field.data_type.is_optional is False
        assert cached_field.imports == (IMPORT_OPTIONAL,)
        assert cached_field.data_type.is_optional is True
        uncached.assert_not_called()
    finally:
        DataModelFieldBase._field_imports_cache.clear()


def test_field_import_cache_distinguishes_fixed_tuple_item_count() -> None:
    """Fixed-length empty and Any tuples need distinct cached imports."""
    DataModelFieldBase._field_imports_cache.clear()
    try:
        empty_tuple = DataModelFieldBase(
            name="empty",
            data_type=DataType(is_tuple=True, tuple_item_count=0),
            required=True,
        )
        any_tuple = DataModelFieldBase(
            name="values",
            data_type=DataType(is_tuple=True, tuple_item_count=2),
            required=True,
        )

        assert empty_tuple.imports == (Import.from_full_path("typing.Tuple"),)
        assert any_tuple.imports == (IMPORT_ANY, Import.from_full_path("typing.Tuple"))
    finally:
        DataModelFieldBase._field_imports_cache.clear()


def test_field_import_cache_normalizes_nullable_reference_on_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test cached import lookup still applies reference nullable side effects."""
    DataModelFieldBase._field_imports_cache.clear()
    try:
        primed_reference = Reference(path="#/definitions/User", name="User")
        primed_reference.source = ReferenceSource(nullable=True)
        primed_field = DataModelFieldBase(
            name="user",
            data_type=DataType(data_types=[DataType(reference=primed_reference), DataType(type="int")]),
            required=True,
        )

        assert primed_field.imports == (IMPORT_OPTIONAL, IMPORT_UNION)
        assert primed_field.data_type.data_types[0].is_optional is True

        uncached = Mock(side_effect=AssertionError("unexpected uncached import collection"))
        monkeypatch.setattr(DataModelFieldBase, "_collect_field_imports_uncached", uncached)
        cached_reference = Reference(path="#/definitions/User", name="User")
        cached_reference.source = ReferenceSource(nullable=True)
        cached_field = DataModelFieldBase(
            name="user",
            data_type=DataType(data_types=[DataType(reference=cached_reference), DataType(type="int")]),
            required=True,
        )

        assert cached_field.data_type.data_types[0].is_optional is False
        assert cached_field.imports == (IMPORT_OPTIONAL, IMPORT_UNION)
        assert cached_field.data_type.data_types[0].is_optional is True
        uncached.assert_not_called()
    finally:
        DataModelFieldBase._field_imports_cache.clear()


def test_field_import_cache_evicts_oldest_entry() -> None:
    """Test the bounded field imports cache evicts the oldest entry."""
    DataModelFieldBase._field_imports_cache.clear()
    original_max_size = DataModelFieldBase._FIELD_IMPORTS_CACHE_MAX_SIZE
    try:
        DataModelFieldBase._FIELD_IMPORTS_CACHE_MAX_SIZE = 1
        field = DataModelFieldBase(name="a", data_type=DataType(type="str"), required=True)
        first_key: tuple[Any, ...] = ("first",)
        second_key: tuple[Any, ...] = ("second",)

        field._set_cached_field_imports(first_key, ())
        field._set_cached_field_imports(second_key, (IMPORT_OPTIONAL,))

        assert first_key not in DataModelFieldBase._field_imports_cache
        assert DataModelFieldBase._field_imports_cache[second_key] == (IMPORT_OPTIONAL,)
    finally:
        DataModelFieldBase._FIELD_IMPORTS_CACHE_MAX_SIZE = original_max_size
        DataModelFieldBase._field_imports_cache.clear()


@pytest.mark.parametrize(
    ("field", "expected_imports"),
    [
        (
            DataModelFieldBase(
                name="a",
                data_type=DataType(data_types=[DataType(type="str")]),
                required=True,
                nullable=True,
            ),
            (IMPORT_OPTIONAL,),
        ),
        (
            DataModelFieldBase(
                name="a",
                data_type=DataType(data_types=[DataType(type="str")]),
                required=True,
                nullable=False,
            ),
            (),
        ),
        (
            DataModelFieldBase(
                name="a",
                data_type=DataType(data_types=[DataType(type="str")]),
                required=True,
                type_has_null=True,
            ),
            (IMPORT_OPTIONAL,),
        ),
        (
            DataModelFieldBase(
                name="a",
                data_type=DataType(data_types=[DataType(type="str")], use_union_operator=True),
                required=False,
            ),
            (),
        ),
        (
            DataModelFieldBase(
                name="a",
                data_type=DataType(data_types=[DataType(type="str")]),
                required=False,
                extras={"default_factory": "list"},
            ),
            (),
        ),
    ],
)
def test_field_import_fallback_handles_field_optional_structure(
    field: DataModelFieldBase,
    expected_imports: tuple[Import, ...],
) -> None:
    """Test field-level optional import decisions use field structure."""
    assert field.imports == expected_imports


def test_field_import_fallback_handles_unexpected_nullable_value() -> None:
    """Test defensive nullable matching keeps non-bool values from adding imports."""
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(data_types=[DataType(type="str")]),
        required=False,
    )
    field.nullable = object()  # type: ignore[assignment]

    assert field.imports == ()


def test_field_import_fallback_respects_union_operator_for_none_branch() -> None:
    """Test union operator mode does not add typing imports for None branches."""
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(
            data_types=[DataType(type="str"), DataType(type=NONE)],
            use_union_operator=True,
        ),
        required=True,
    )

    assert field.imports == ()


def test_field_import_fallback_keeps_union_operator_optional_in_forward_ref_model() -> None:
    """Test DataType-level union operator option still controls existing optional hints."""
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(data_types=[DataType(type="str")], is_optional=True, use_union_operator=True),
        required=False,
    )
    model = BaseModel(
        fields=[field],
        reference=Reference(path="Model", original_name="Model", name="Model"),
    )
    model.has_forward_reference = True

    assert field.imports == ()


def test_typing_import_requirements_ignore_unknown_import_name() -> None:
    """Test unknown annotation names leave structured import requirements unchanged."""
    requirements = _TypingImportRequirements(optional=True)

    assert requirements.with_import_name("Literal") is requirements


def test_annotation_typing_import_names_ignores_invalid_annotation() -> None:
    """Test invalid explicit type strings do not fall back to ad hoc string matching."""
    assert _annotation_typing_import_names("List[") == frozenset()


@pytest.mark.parametrize(
    ("annotation", "expected_names"),
    [
        ("str", frozenset()),
        ("Optional", frozenset({IMPORT_OPTIONAL.import_})),
    ],
)
def test_annotation_typing_import_names_handles_identifier_fast_path(
    annotation: str,
    expected_names: frozenset[str],
) -> None:
    """Test identifier annotations skip AST parsing unless they are typing imports."""
    assert _annotation_typing_import_names(annotation) == expected_names


@pytest.mark.parametrize(
    ("name", "expected_true", "expected_false"),
    [
        ("array-commons.schema", "array_commons.schema", "array_commons_schema"),
        ("123filename", "_123filename", "_123filename"),
        ("normal_filename", "normal_filename", "normal_filename"),
        ("file!name", "file_name", "file_name"),
        ("", "", ""),
    ],
)
@pytest.mark.parametrize("treat_dot_as_module", [True, False])
def test_sanitize_module_name(name: str, expected_true: str, expected_false: str, treat_dot_as_module: bool) -> None:
    """Test module name sanitization with different characters and options."""
    expected = expected_true if treat_dot_as_module else expected_false
    assert sanitize_module_name(name, treat_dot_as_module=treat_dot_as_module) == expected


@pytest.mark.parametrize(
    ("treat_dot_as_module", "expected"),
    [
        (True, ["inputs", "array_commons.schema", "array-commons"]),
        (False, ["inputs", "array_commons_schema"]),
    ],
)
def test_get_module_path_with_file_path(treat_dot_as_module: bool, expected: list[str]) -> None:
    """Test module path generation with a file path."""
    file_path = Path("inputs/array-commons.schema.json")
    result = get_module_path("array-commons.schema", file_path, treat_dot_as_module=treat_dot_as_module)
    assert result == expected


def test_get_module_path_without_file_path_treat_dot_true() -> None:
    """Test module path generation without a file path with treat_dot_as_module=True."""
    result = get_module_path("my_module.submodule", None, treat_dot_as_module=True)
    expected = ["my_module"]
    assert result == expected


def test_get_module_path_without_file_path_treat_dot_false() -> None:
    """Test module path generation without a file path with treat_dot_as_module=False."""
    result = get_module_path("my_module.submodule", None, treat_dot_as_module=False)
    expected: list[str] = []
    assert result == expected


@pytest.mark.parametrize(
    ("treat_dot_as_module", "name", "expected"),
    [
        (True, "a.b.c", ["a", "b"]),
        (True, "simple", []),
        (True, "with.dot", ["with"]),
        (False, "a.b.c", []),
        (False, "simple", []),
        (False, "with.dot", []),
    ],
)
def test_get_module_path_without_file_path_parametrized(
    treat_dot_as_module: bool, name: str, expected: list[str]
) -> None:
    """Test module path generation without file path for various module names."""
    result = get_module_path(name, None, treat_dot_as_module=treat_dot_as_module)
    assert result == expected


def test_copy_deep_with_dict_key() -> None:
    """Test that copy_deep properly copies dict key and value types."""
    dict_key_type = DataType(type="str")
    dict_value_type = DataType(type="str")
    data_type = DataType(data_types=[dict_value_type], is_dict=True, dict_key=dict_key_type)
    field = DataModelFieldBase(name="a", data_type=data_type, required=True)

    copied = field.copy_deep()

    assert copied.data_type.dict_key is not None
    assert copied.data_type.dict_key is not field.data_type.dict_key
    assert copied.data_type.dict_key.type == "str"
    assert copied.data_type.data_types[0] is not dict_value_type
    copied.data_type.data_types[0].type = "int"
    assert dict_value_type.type == "str"


def test_copy_deep_with_extras() -> None:
    """Test that copy_deep properly deep copies extras."""
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(type="str"),
        required=True,
        extras={"key": "value", "nested": {"inner": 1}},
    )

    copied = field.copy_deep()

    assert copied.extras is not field.extras
    assert copied.extras == {"key": "value", "nested": {"inner": 1}}
    copied.extras["key"] = "modified"
    assert field.extras["key"] == "value"


@pytest.mark.parametrize(
    ("input_value", "expected"),
    [
        (None, None),
        ("", ""),
        ("no special chars", "no special chars"),
        # Backslash escaping
        (r"backslash \ here", r"backslash \\ here"),
        (r"path C:\Users\name", r"path C:\\Users\\name"),
        (r"escape \n sequence", r"escape \\n sequence"),
        # Triple quote escaping
        ('"""', r"\"\"\""),
        ('contains """quotes"""', r"contains \"\"\"quotes\"\"\""),
        # Both backslash and triple quotes
        (r'both \ and """', r"both \\ and \"\"\""),
        (r'path C:\"""file"""', r"path C:\\\"\"\"file\"\"\""),
    ],
)
def test_escape_docstring(input_value: str | None, expected: str | None) -> None:
    """Test escape_docstring properly escapes special characters.

    This tests issue #1808 where backslashes and triple quotes in docstrings
    were not escaped, causing Python syntax errors and type checker warnings.
    """
    assert escape_docstring(input_value) == expected


@pytest.mark.parametrize(
    ("input_value", "expected"),
    [
        (None, None),
        ("", ""),
        ("plain text", "plain text"),
        # LF is already handled by the union templates.
        ("line one\nline two", "line one\nline two"),
        ("a\nb\nc", "a\nb\nc"),
        # CR and CRLF must be normalized before template rendering.
        ("line one\rline two", "line one\nline two"),
        ("line one\r\nline two", "line one\nline two"),
        ("a\r\nb\nc\rd", "a\nb\nc\nd"),
        (
            "Color union\rprint('PWNED')",
            "Color union\nprint('PWNED')",
        ),
    ],
)
def test_comment_safe(input_value: str | None, expected: str | None) -> None:
    """Test comment_safe line ending normalization."""
    assert comment_safe(input_value) == expected


@pytest.mark.parametrize(
    ("input_value", "expected"),
    [
        (None, None),
        ("", ""),
        ("plain text", "plain text"),
        ("line one\nline two", "line one\n# line two"),
        ("line one\rline two", "line one\n# line two"),
        ("line one\r\nline two", "line one\n# line two"),
        ("line one\vline two", "line one\n# line two"),
        ("line one\fline two", "line one\n# line two"),
        ("a\r\nb\nc\rd\ve\ff", "a\n# b\n# c\n# d\n# e\n# f"),
    ],
)
def test_inline_comment_safe(input_value: str | None, expected: str | None) -> None:
    """Test inline comment escaping."""
    assert inline_comment_safe(input_value) == expected


def test_format_docstring_uses_multiline_format_by_default() -> None:
    """Test format_docstring preserves historical multi-line formatting by default."""
    assert format_docstring("Description", 4) == '"""\n    Description\n    """'


@pytest.mark.parametrize("empty_value", [None, "", "   "])
def test_format_docstring_returns_empty_string_for_empty_values(empty_value: str | None) -> None:
    """Test format_docstring returns an empty string for empty values."""
    assert not format_docstring(empty_value, 4)


def test_format_docstring_uses_single_line_when_enabled() -> None:
    """Test format_docstring emits one-line docstrings when enabled."""
    assert format_docstring("Description", 4, use_single_line_docstring=True) == '"""Description"""'


def test_format_docstring_escapes_trailing_quote_without_changing_docstring() -> None:
    """Test one-line docstrings ending with a quote preserve their value."""
    assert format_docstring('Description"', 4, use_single_line_docstring=True) == r'"""Description\""""'


def test_format_docstring_escapes_single_quote_docstring() -> None:
    """Test a docstring consisting only of a quote is escaped."""
    assert format_docstring('"', 4, use_single_line_docstring=True) == r'"""\""""'


def test_format_docstring_keeps_escaped_triple_quotes_without_extra_escape() -> None:
    """Test escaped triple quotes at the end are not double-escaped."""
    assert format_docstring('Description """', 4, use_single_line_docstring=True) == r'"""Description \"\"\""""'


def test_inline_field_docstring_escapes_special_chars() -> None:
    """Test inline_field_docstring property escapes special characters."""
    field = DataModelFieldBase(
        name="test_field",
        data_type=DataType(type="str"),
        required=True,
        extras={"description": r"Path like C:\Users\name"},
        use_inline_field_description=True,
    )
    assert field.inline_field_docstring == r'"""Path like C:\\Users\\name"""'


def test_inline_field_docstring_escapes_triple_quotes() -> None:
    """Test inline_field_docstring property escapes triple quotes."""
    field = DataModelFieldBase(
        name="test_field",
        data_type=DataType(type="str"),
        required=True,
        extras={"description": 'Contains """quotes"""'},
        use_inline_field_description=True,
    )
    assert field.inline_field_docstring == r'"""Contains \"\"\"quotes\"\"\""""'


def test_data_type_manager_unknown_type_raises_error() -> None:
    """Test DataTypeManager raises NotImplementedError for unknown types."""
    from datamodel_code_generator.model.types import DataTypeManager

    manager = DataTypeManager()
    del manager.type_map[Types.path]

    with pytest.raises(NotImplementedError, match="Type mapping for 'path' not implemented"):
        manager.get_data_type(Types.path)


def test_data_type_manager_has_all_types() -> None:
    """Test DataTypeManager has mappings for all Types enum members."""
    from datamodel_code_generator.model.types import DataTypeManager

    manager = DataTypeManager()
    missing_types = [t for t in Types if t not in manager.type_map]
    assert not missing_types, f"Missing type mappings: {[t.name for t in missing_types]}"


def test_data_type_manager_returns_copied_type_map_entries() -> None:
    """Type map entries are reusable prototypes, not caller-owned objects."""
    from datamodel_code_generator.model.types import DataTypeManager

    manager = DataTypeManager()

    integer_type = manager.get_data_type(Types.integer)
    int64_type = manager.get_data_type(Types.int64)
    integer_type.alias = "CustomInt"

    assert integer_type is not int64_type
    assert int64_type.alias is None


def test_data_type_manager_returns_copied_nested_type_map_entries() -> None:
    """Nested data types from map prototypes should not be shared between callers."""
    from datamodel_code_generator.model.types import DataTypeManager

    manager = DataTypeManager()

    array_type = manager.get_data_type(Types.array)
    another_array_type = manager.get_data_type(Types.array)
    array_type.data_types[0].alias = "CustomItem"

    assert array_type is not another_array_type
    assert array_type.data_types[0] is not another_array_type.data_types[0]
    assert another_array_type.data_types[0].alias is None
