"""Tests for Pydantic v2 config generation helpers."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from datamodel_code_generator import AliasGenerator
from datamodel_code_generator.imports import Imports
from datamodel_code_generator.model import DataModelFieldBase, pydantic_v2
from datamodel_code_generator.model.msgspec import Constraints as MsgspecConstraints
from datamodel_code_generator.model.pydantic_base import PatternConstraints
from datamodel_code_generator.model.pydantic_v2.base_model import (
    BaseModel,
    DataModelField,
    _alias_generator_name,
    _config_dict_items,
    _generate_alias,
)
from datamodel_code_generator.model.pydantic_v2.base_model import Constraints as PydanticV2Constraints
from datamodel_code_generator.model.pydantic_v2.dataclass import DataClass
from datamodel_code_generator.reference import Reference
from datamodel_code_generator.types import DataType
from tests.conftest import assert_output

EXPECTED_PYDANTIC_V2_MODEL_PATH = Path(__file__).parents[2] / "data" / "expected" / "model" / "pydantic_v2"


def _extra_template_data() -> defaultdict[str, dict[str, Any]]:
    return defaultdict(
        dict,
        {
            "Model": {
                "additionalProperties": False,
                "allow_population_by_field_name": True,
                "use_attribute_docstrings": True,
            }
        },
    )


def _field() -> DataModelFieldBase:
    return DataModelFieldBase(name="a", data_type=DataType(type="str"), required=True)


def _reference() -> Reference:
    return Reference(name="Model", path="Model")


def _render_model_with_imports(model: Any) -> str:
    imports = Imports()
    imports.append(model.imports)
    import_block = imports.dump()
    rendered = model.render()
    return f"{import_block}\n\n\n{rendered}\n".lstrip("\n")


def test_config_dict_reexport_preserves_public_surface() -> None:
    """ConfigDict remains importable from the package for compatibility."""
    assert_output(
        (
            f"ConfigDict module: {pydantic_v2.ConfigDict.__module__}\n"
            f"ConfigDict in __all__: {'ConfigDict' in pydantic_v2.__all__}\n"
        ),
        EXPECTED_PYDANTIC_V2_MODEL_PATH / "config_dict_reexport_public_surface.txt",
    )


def test_base_model_config_key_order_with_multiple_shared_parameters() -> None:
    """BaseModel config generation keeps deterministic key ordering."""
    model = BaseModel(
        fields=[_field()],
        reference=_reference(),
        extra_template_data=_extra_template_data(),
    )

    config = model.extra_template_data["config"]
    assert_output(
        (
            f"config type: {type(config).__name__}\n"
            f"config keys: {list(config.dict(exclude_unset=True))!r}\n"
            f"template config items: {_config_dict_items(config)!r}\n\n"
            f"{_render_model_with_imports(model)}"
        ),
        EXPECTED_PYDANTIC_V2_MODEL_PATH / "base_model_config_shared_parameters.txt",
    )


def test_base_model_config_alias_generator_order_and_import() -> None:
    """BaseModel config places alias_generator after populate_by_name."""
    extra_template_data = defaultdict(
        dict,
        {
            "Model": {
                "allow_population_by_field_name": True,
                "alias_generator": "to_camel",
            }
        },
    )
    model = BaseModel(
        fields=[_field()],
        reference=_reference(),
        extra_template_data=extra_template_data,
    )

    config = model.extra_template_data["config"]
    assert_output(
        (
            f"config type: {type(config).__name__}\n"
            f"config keys: {list(config.dict(exclude_unset=True))!r}\n\n"
            f"{_render_model_with_imports(model)}"
        ),
        EXPECTED_PYDANTIC_V2_MODEL_PATH / "base_model_config_alias_generator.txt",
    )


def test_model_config_target_pydantic_version_uses_validate_by_name_for_later_versions() -> None:
    """Pydantic model config treats target Pydantic versions later than 2.11 as 2.11+."""
    extra_template_data = _extra_template_data()
    extra_template_data["Model"]["target_pydantic_version"] = "2.13"
    base_model = BaseModel(fields=[_field()], reference=_reference(), extra_template_data=extra_template_data)

    extra_template_data = _extra_template_data()
    extra_template_data["Model"]["target_pydantic_version"] = "2.13"
    dataclass_model = DataClass(fields=[_field()], reference=_reference(), extra_template_data=extra_template_data)

    base_model_config = base_model.extra_template_data["config"].model_dump(exclude_unset=True)
    dataclass_config = dataclass_model.extra_template_data["config"]
    assert_output(
        (
            f"base model config keys: {list(base_model_config)!r}\n"
            f"base model config: {base_model_config!r}\n"
            f"dataclass config keys: {list(dataclass_config)!r}\n"
            f"dataclass config: {dataclass_config!r}\n"
        ),
        EXPECTED_PYDANTIC_V2_MODEL_PATH / "config_target_pydantic_future_version.txt",
    )


def test_base_model_alias_generator_adds_field_import_for_mismatch() -> None:
    """Alias generator mismatch fields still import Field."""
    extra_template_data = defaultdict(dict, {"Model": {"alias_generator": "to_camel"}})
    model = BaseModel(
        fields=[
            DataModelField(
                name="foo_bar",
                original_name="foo_bar",
                data_type=DataType(type="str"),
                required=True,
            )
        ],
        reference=_reference(),
        extra_template_data=extra_template_data,
    )

    assert_output(
        _render_model_with_imports(model),
        EXPECTED_PYDANTIC_V2_MODEL_PATH / "base_model_alias_generator_mismatch.py",
    )


def test_base_model_alias_generator_omits_matching_alias_field_import() -> None:
    """Alias generator matching fields do not import Field just for an alias."""
    extra_template_data = defaultdict(dict, {"Model": {"alias_generator": "to_camel"}})
    model = BaseModel(
        fields=[
            DataModelField(
                name="first_name",
                original_name="firstName",
                alias="firstName",
                data_type=DataType(type="str"),
                required=True,
            )
        ],
        reference=_reference(),
        extra_template_data=extra_template_data,
    )

    assert_output(
        _render_model_with_imports(model),
        EXPECTED_PYDANTIC_V2_MODEL_PATH / "base_model_alias_generator_matching_alias.py",
    )


def test_base_model_alias_generator_variants_omit_matching_aliases() -> None:
    """Alias generator variants omit matching per-field aliases."""
    cases = [
        ("to_pascal", "first_name", "FirstName"),
        ("to_snake", "firstName", "first_name"),
    ]
    output = ""
    for alias_generator, field_name, wire_name in cases:
        extra_template_data = defaultdict(dict, {"Model": {"alias_generator": alias_generator}})
        model = BaseModel(
            fields=[
                DataModelField(
                    name=field_name,
                    original_name=wire_name,
                    alias=wire_name,
                    data_type=DataType(type="str"),
                    required=True,
                )
            ],
            reference=_reference(),
            extra_template_data=extra_template_data,
        )

        output += f"# {alias_generator}\n\n{_render_model_with_imports(model)}\n"

    assert_output(output, EXPECTED_PYDANTIC_V2_MODEL_PATH / "base_model_alias_generator_variants.txt")


def test_base_model_alias_generator_keeps_non_alias_early_returns() -> None:
    """Alias generator field processing handles ClassVar and unnamed wire fields."""
    extra_template_data = defaultdict(dict, {"Model": {"alias_generator": "to_camel"}})
    model = BaseModel(
        fields=[
            DataModelField(
                name="class_var_field",
                data_type=DataType(type="str"),
                extras={"x-is-classvar": True},
                required=True,
            ),
            DataModelField(
                name="plain_field",
                original_name=None,
                data_type=DataType(type="str"),
                required=True,
            ),
        ],
        reference=_reference(),
        extra_template_data=extra_template_data,
    )

    assert_output(
        _render_model_with_imports(model),
        EXPECTED_PYDANTIC_V2_MODEL_PATH / "base_model_alias_generator_non_alias_fields.py",
    )


def test_alias_generator_helpers_handle_enum_and_unknown_values() -> None:
    """Alias generator helpers keep fallback behavior explicit."""
    field = DataModelField(name="field_name", data_type=DataType(type="str"), required=True)

    assert_output(
        (
            f"alias_generator_name_enum: {_alias_generator_name(AliasGenerator.ToCamel)}\n"
            f"alias_generator_name_unknown: {_alias_generator_name(object())}\n"
            f"generate_alias_unknown: {_generate_alias('custom_generator', 'field_name')}\n"
            "automatic_alias_disabled_without_parent: "
            f"{field._automatic_alias_disabled_for_alias_generator()}\n"
        ),
        EXPECTED_PYDANTIC_V2_MODEL_PATH / "alias_generator_helpers.txt",
    )


def test_config_dict_items_accepts_supported_config_shapes() -> None:
    """Config rendering accepts model, dict, legacy dict method, and empty values."""

    class LegacyConfig:
        def dict(self, **_kwargs: Any) -> dict[str, Any]:
            return {"frozen": True}

    config_dict_items = _config_dict_items(pydantic_v2.ConfigDict(extra="'allow'"))
    dict_items = _config_dict_items({"extra": "'forbid'"})
    legacy_dict_items = _config_dict_items(LegacyConfig())
    assert_output(
        (
            f"config_dict: {config_dict_items!r}\n"
            f"dict: {dict_items!r}\n"
            f"legacy_dict: {legacy_dict_items!r}\n"
            f"none: {_config_dict_items(None)!r}\n"
            f"object: {_config_dict_items(object())!r}\n"
        ),
        EXPECTED_PYDANTIC_V2_MODEL_PATH / "config_dict_items.txt",
    )


def test_dataclass_config_key_order_with_multiple_shared_parameters() -> None:
    """Dataclass config generation keeps deterministic key ordering."""
    model = DataClass(
        fields=[_field()],
        reference=_reference(),
        extra_template_data=_extra_template_data(),
    )

    config = model.extra_template_data["config"]
    assert_output(
        (f"config type: {type(config).__name__}\nconfig keys: {list(config)!r}\n\n{_render_model_with_imports(model)}"),
        EXPECTED_PYDANTIC_V2_MODEL_PATH / "dataclass_config_shared_parameters.txt",
    )


def test_pattern_constraints_keep_leaf_specific_behavior() -> None:
    """Renamed pydantic constraints remain leaf-model specific."""
    assert_output(
        (
            f"PatternConstraints leaf fields: {list(PatternConstraints.model_fields)[-2:]!r}\n"
            f"PydanticV2Constraints leaf fields: {list(PydanticV2Constraints.model_fields)[-2:]!r}\n"
            f"MsgspecConstraints leaf fields: {list(MsgspecConstraints.model_fields)[-2:]!r}\n"
            "PydanticV2 minItems dump: "
            f"{PydanticV2Constraints.model_validate({'minItems': 1}).model_dump(exclude_unset=True)!r}\n"
            "Msgspec minItems dump: "
            f"{MsgspecConstraints.model_validate({'minItems': 1}).model_dump(exclude_unset=True)!r}\n"
        ),
        EXPECTED_PYDANTIC_V2_MODEL_PATH / "pattern_constraints.txt",
    )
