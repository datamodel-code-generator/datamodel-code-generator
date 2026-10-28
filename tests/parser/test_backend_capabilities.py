"""Tests for output-owned parser capabilities."""

from __future__ import annotations

from pathlib import Path

from datamodel_code_generator.imports import Imports
from datamodel_code_generator.model.base import (
    DataModel,
    DataModelFieldBase,
    get_resolve_reference_action_capabilities,
)
from datamodel_code_generator.model.imports import IMPORT_TYPED_DICT_BACKPORT
from datamodel_code_generator.model.pydantic_v2 import BaseModel, dump_resolve_reference_action
from datamodel_code_generator.model.pydantic_v2 import DataModelField as PydanticDataModelField
from datamodel_code_generator.model.typed_dict import TypedDict
from datamodel_code_generator.parser.base import _is_pydantic_v2_dump_resolve_reference_action
from datamodel_code_generator.reference import Reference
from datamodel_code_generator.types import DataType
from tests.conftest import assert_output

EXPECTED_PATH = Path(__file__).parents[1] / "data" / "expected" / "parser" / "backend_capabilities.txt"


class _LegacyTypedExtraField(DataModelFieldBase):
    """Emulate an external field that exposed the former backend attribute."""

    @property
    def is_pydantic_extra_field(self) -> bool:
        """Return the legacy immediate-annotation marker."""
        return True


def _custom_resolve_reference_action(_: list[str]) -> str:
    return ""


def test_output_owned_parser_capabilities() -> None:
    """Keep capability defaults, compatibility, and built-in declarations explicit."""
    data_type = DataType(type="str")
    canonical_action = get_resolve_reference_action_capabilities(dump_resolve_reference_action)
    custom_action = get_resolve_reference_action_capabilities(_custom_resolve_reference_action)
    legacy_canonical = _is_pydantic_v2_dump_resolve_reference_action(dump_resolve_reference_action)
    legacy_custom = _is_pydantic_v2_dump_resolve_reference_action(_custom_resolve_reference_action)
    custom_action_output = _custom_resolve_reference_action([])
    typed_dict_model = TypedDict(reference=Reference(path="Model", name="Model"), fields=[])
    backport_only_imports = Imports()
    backport_only_imports.append(IMPORT_TYPED_DICT_BACKPORT)
    TypedDict.resolve_module_import_conflicts(
        [typed_dict_model],
        {typed_dict_model: (IMPORT_TYPED_DICT_BACKPORT,)},
        backport_only_imports,
    )
    neutral_immediate = DataModelFieldBase(data_type=data_type).requires_immediate_forward_reference_resolution
    legacy_immediate = _LegacyTypedExtraField(data_type=data_type).requires_immediate_forward_reference_resolution
    pydantic_immediate = PydanticDataModelField(
        name="__pydantic_extra__", data_type=data_type
    ).requires_immediate_forward_reference_resolution
    ordinary_immediate = PydanticDataModelField(
        name="value", data_type=data_type
    ).requires_immediate_forward_reference_resolution
    lines = [
        f"neutral immediate forward reference: {neutral_immediate}",
        f"legacy immediate forward reference: {legacy_immediate}",
        f"pydantic immediate forward reference: {pydantic_immediate}",
        f"ordinary pydantic forward reference: {ordinary_immediate}",
        f"neutral runtime validation: {DataModel.SUPPORTS_SCHEMA_RUNTIME_VALIDATION}",
        f"pydantic runtime validation: {BaseModel.SUPPORTS_SCHEMA_RUNTIME_VALIDATION}",
        f"canonical action filters forward references: {canonical_action.filter_forward_references}",
        f"canonical action is formatter safe: {canonical_action.generated_formatter_safe}",
        f"custom action filters forward references: {custom_action.filter_forward_references}",
        f"custom action is formatter safe: {custom_action.generated_formatter_safe}",
        f"legacy canonical action detection: {legacy_canonical}",
        f"legacy custom action detection: {legacy_custom}",
        f"custom action output: {custom_action_output!r}",
        f"backport-only imports: {backport_only_imports}",
    ]

    assert_output("\n".join(lines) + "\n", EXPECTED_PATH)
