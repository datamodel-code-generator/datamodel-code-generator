"""Tests for type manipulation utilities."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import pytest
from pydantic import TypeAdapter

from datamodel_code_generator.imports import IMPORT_ANY, IMPORT_DECIMAL, IMPORT_TUPLE, Import
from datamodel_code_generator.python_literal import (
    PythonCode,
    _normalize_string,
    is_safe_public_type_name,
    represent_python_value,
    represent_untrusted_public_type_name,
    represent_untrusted_python_value,
)
from datamodel_code_generator.reference import Reference
from datamodel_code_generator.types import (
    DataType,
    UnionIntFloat,
    _contains_decimal,
    _remove_none_from_union,
    chain_as_tuple,
    extract_qualified_names,
    get_optional_type,
    get_subscript_args,
    get_type_base_name,
    is_data_model_field,
    normalize_integer_constraint,
)
from tests.conftest import assert_output

if TYPE_CHECKING:
    from collections.abc import Callable


def test_union_int_float_conversion_after_json_round_trip() -> None:
    """Retain explicit float conversion without rounding the serialized integer operands."""
    data_path = Path(__file__).parent / "data"
    adapter = TypeAdapter(list[UnionIntFloat])
    values = adapter.validate_json((data_path / "payloads" / "union_int_float.json").read_text(encoding="utf-8"))
    round_trip = adapter.validate_json(adapter.dump_json(values))
    result = {
        "round_trip": json.loads(adapter.dump_json(round_trip)),
        "floats": [float(value) for value in round_trip],
    }
    assert_output(
        json.dumps(result, indent=2) + "\n",
        data_path / "expected" / "types" / "union_int_float.txt",
    )


def test_is_data_model_field_uses_structural_contract() -> None:
    """Recognize only parents exposing the writable data_type contract."""
    from types import SimpleNamespace

    data_type = DataType(type="str")

    assert is_data_model_field(SimpleNamespace(data_type=data_type))
    assert not is_data_model_field(SimpleNamespace(data_type=object()))
    assert not is_data_model_field(data_type)


def test_data_type_rejects_non_binding_python_type_context() -> None:
    """Parser/model objects cannot cross the structured annotation boundary."""
    with pytest.raises(ValueError, match="python_type must be a BoundPythonType"):
        DataType(type="str", python_type=object())


def test_data_type_accepts_explicit_empty_python_type_context() -> None:
    """An explicitly empty binding context keeps the ordinary fast path."""
    data_type = DataType(type="str", python_type=None)

    assert data_type.python_type is None


@pytest.mark.parametrize(
    ("input_", "use_union_operator", "expected"),
    [
        ("List[str]", False, "Optional[List[str]]"),
        ("List[str, int, float]", False, "Optional[List[str, int, float]]"),
        ("List[str, int, None]", False, "Optional[List[str, int, None]]"),
        ("Union[str]", False, "Optional[str]"),
        ("Union[str, int, float]", False, "Optional[Union[str, int, float]]"),
        ("Union[str, int, None]", False, "Optional[Union[str, int]]"),
        ("Union[str, int, None, None]", False, "Optional[Union[str, int]]"),
        (
            "Union[str, int, List[str, int, None], None]",
            False,
            "Optional[Union[str, int, List[str, int, None]]]",
        ),
        (
            "Union[str, int, List[str, Dict[int, str | None]], None]",
            False,
            "Optional[Union[str, int, List[str, Dict[int, str | None]]]]",
        ),
        ("List[str]", True, "List[str] | None"),
        ("List[str | int | float]", True, "List[str | int | float] | None"),
        ("List[str | int | None]", True, "List[str | int | None] | None"),
        ("str", True, "str | None"),
        ("str | int | float", True, "str | int | float | None"),
        ("str | int | None", True, "str | int | None"),
        ("str | int | None | None", True, "str | int | None"),
        (
            "str | int | List[str | Dict[int | Union[str | None]]] | None",
            True,
            "str | int | List[str | Dict[int | Union[str | None]]] | None",
        ),
    ],
)
def test_get_optional_type(input_: str, use_union_operator: bool, expected: str) -> None:
    """Test get_optional_type function with various type strings."""
    assert get_optional_type(input_, use_union_operator) == expected


def test_get_optional_type_cache_clear_preserves_value() -> None:
    """Clearing the bounded cache must not change get_optional_type results."""
    optional_type = get_optional_type("str | None", True)

    get_optional_type.cache_clear()

    assert get_optional_type("str | None", True) == optional_type


def test_chain_as_tuple_chains_multiple_iterables() -> None:
    """Test chain_as_tuple handles the general path for more than two iterables."""
    assert chain_as_tuple((1,), (2,), (3,)) == (1, 2, 3)


def test_is_data_model_field_matches_structural_type_contract() -> None:
    """The runtime predicate must accept exactly field-like DataType owners."""

    class FieldLike:
        data_type = DataType(type="str")

    class InvalidFieldLike:
        data_type = object()

    candidate: object = FieldLike()

    assert is_data_model_field(candidate)
    assert candidate.data_type.type == "str"
    assert not is_data_model_field(InvalidFieldLike())
    assert not is_data_model_field(object())


@pytest.mark.parametrize(
    ("type_str", "use_union_operator", "expected"),
    [
        # Traditional Union syntax
        ("Union[str, None]", False, "str"),
        ("Union[str, int, None]", False, "Union[str, int]"),
        ("Union[None, str]", False, "str"),
        ("Union[None]", False, "None"),
        ("Union[None, None]", False, "None"),
        ("Union[Union[str, None], int]", False, "Union[str, int]"),
        # Union for constraint strings with pattern or regex
        (
            "Union[constr(pattern=r'^a,b$'), None]",
            False,
            "constr(pattern=r'^a,b$')",
        ),
        (
            "Union[constr(regex=r'^a,b$'), None]",
            False,
            "constr(regex=r'^a,b$')",
        ),
        (
            "Union[constr(pattern=r'^\\d+,\\w+$'), None]",
            False,
            "constr(pattern=r'^\\d+,\\w+$')",
        ),
        (
            "Union[constr(regex=r'^\\d+,\\w+$'), None]",
            False,
            "constr(regex=r'^\\d+,\\w+$')",
        ),
        # Union operator syntax
        ("str | None", True, "str"),
        ("int | str | None", True, "int | str"),
        ("None | str", True, "str"),
        ("None | None", True, "None"),
        ("constr(pattern='0|1') | None", True, "constr(pattern='0|1')"),
        ("constr(pattern='0  |1') | int | None", True, "constr(pattern='0  |1') | int"),
        # Complex nested types - traditional syntax
        ("Union[str, int] | None", True, "Union[str, int]"),
        (
            "Optional[List[Dict[str, Any]]] | None",
            True,
            "Optional[List[Dict[str, Any]]]",
        ),
        # Union for constraint strings with pattern or regex on nested types
        (
            "Union[constr(pattern=r'\\['), Union[str, None], int]",
            False,
            "Union[constr(pattern=r'\\['), str, int]",
        ),
        (
            "Union[constr(regex=r'\\['), Union[str, None], int]",
            False,
            "Union[constr(regex=r'\\['), str, int]",
        ),
        # Complex nested types - union operator syntax
        ("List[str | None] | None", True, "List[str | None]"),
        (
            "List[constr(pattern='0|1') | None] | None",
            True,
            "List[constr(pattern='0|1') | None]",
        ),
        (
            "List[constr(pattern='0 | 1') | None] | None",
            True,
            "List[constr(pattern='0 | 1') | None]",
        ),
        (
            "List[constr(pattern='0  | 1') | None] | None",
            True,
            "List[constr(pattern='0  | 1') | None]",
        ),
        ("Dict[str, int] | None | List[str]", True, "Dict[str, int] | List[str]"),
        # Edge cases that test the fixed regex pattern issue
        ("List[str] | None", True, "List[str]"),
        ("Dict[str, int] | None", True, "Dict[str, int]"),
        ("Tuple[int, ...] | None", True, "Tuple[int, ...]"),
        ("Callable[[int], str] | None", True, "Callable[[int], str]"),
        # Non-union types (should be returned as-is)
        ("str", False, "str"),
        ("List[str]", False, "List[str]"),
    ],
)
def test_remove_none_from_union(type_str: str, use_union_operator: bool, expected: str) -> None:
    """Test _remove_none_from_union function with various type strings."""
    assert _remove_none_from_union(type_str, use_union_operator=use_union_operator) == expected


@pytest.mark.parametrize(
    ("type_str", "use_union_operator", "expected"),
    [
        ("(", False, "("),
        (")", False, ")"),
        ("()", False, "()"),
        ("a(", False, "a("),
        ("constr()", False, "constr()"),
        ("constr(pattern=')')", False, "constr(pattern=')')"),
        ("Union[constr()]", False, "constr()"),
        ("a | b", True, "a | b"),
        ("(a)", True, "(a)"),
    ],
)
def test_remove_none_from_union_short_strings(type_str: str, use_union_operator: bool, expected: str) -> None:
    """Test _remove_none_from_union with short strings to verify index bounds safety."""
    assert _remove_none_from_union(type_str, use_union_operator=use_union_operator) == expected


def test_datatype_deepcopy_with_circular_references() -> None:
    """Test that DataType.__deepcopy__ handles circular references via parent/children.

    This test verifies the fix for the recursion error that occurred when deepcopying
    DataType objects with circular references through parent and children fields.
    """
    from copy import deepcopy

    # Import DataModelFieldBase first to trigger model_rebuild
    from datamodel_code_generator.model.base import DataModelFieldBase  # noqa: F401
    from datamodel_code_generator.types import DataType

    # Create parent and child DataTypes with circular references
    parent = DataType(type="ParentType")
    child1 = DataType(type="ChildType1", parent=parent)
    child2 = DataType(type="ChildType2", parent=parent)
    parent.children = [child1, child2]

    # This should not cause infinite recursion
    copied_parent = deepcopy(parent)

    # Verify the copy was successful
    assert copied_parent.type == "ParentType"
    # parent and children should be None in the copy (excluded from deepcopy)
    assert copied_parent.parent is None
    assert copied_parent.children is None


def test_datatype_remove_reference_detaches_compatibility_child() -> None:
    """Test removing a reference keeps the reference children list in sync."""
    from datamodel_code_generator.model.base import DataModelFieldBase  # noqa: F401

    reference = Reference(path="Model", original_name="Model", name="Model")
    data_type = DataType(reference=reference)

    data_type.remove_reference()

    assert data_type.reference is None
    assert [child is data_type for child in reference.children] == []


def test_hostname_regex_aliases_canonical_data_type_manager() -> None:
    """Test model modules reuse the canonical hostname regex."""
    from datamodel_code_generator.model import types as model_types
    from datamodel_code_generator.model.pydantic_v2 import types as pydantic_v2_types
    from datamodel_code_generator.types import DataTypeManager as BaseDataTypeManager

    assert model_types.HOSTNAME_REGEX is BaseDataTypeManager.HOSTNAME_REGEX
    assert model_types.DataTypeManager.HOSTNAME_REGEX is BaseDataTypeManager.HOSTNAME_REGEX
    assert pydantic_v2_types.HOSTNAME_REGEX is BaseDataTypeManager.HOSTNAME_REGEX
    assert pydantic_v2_types._PydanticDataTypeManager.HOSTNAME_REGEX is BaseDataTypeManager.HOSTNAME_REGEX
    assert pydantic_v2_types.DataTypeManager.HOSTNAME_REGEX is BaseDataTypeManager.HOSTNAME_REGEX


def test_common_data_type_manager_declares_only_decimal_value_semantics() -> None:
    """Common Python model outputs classify Decimal without knowing Pydantic helpers."""
    from datamodel_code_generator.model.types import DataTypeManager as CommonDataTypeManager
    from datamodel_code_generator.types import DECIMAL_DEFAULT_VALUE_DESCRIPTOR

    data_type_manager = CommonDataTypeManager()

    assert (
        data_type_manager.get_default_value_descriptor(DataType(import_=IMPORT_DECIMAL))
        is DECIMAL_DEFAULT_VALUE_DESCRIPTOR
    )
    assert data_type_manager.get_default_value_descriptor(DataType()) is None
    assert data_type_manager.get_default_value_descriptor(DataType(import_=Import("condecimal", "pydantic"))) is None


def test_python_literal_helpers_render_code_and_tuple_values() -> None:
    """Test Python literal rendering for raw code and tuple containers."""
    raw = PythonCode("datetime_module.date.fromisoformat('2026-01-01')", "2026-01-01")

    assert repr(raw) == "datetime_module.date.fromisoformat('2026-01-01')"
    assert represent_python_value((raw,)) == "(datetime_module.date.fromisoformat('2026-01-01'),)"
    assert represent_python_value((1, "two")) == "(1, 'two')"
    assert represent_python_value(set()) == "set()"
    assert represent_untrusted_python_value(raw) == "'2026-01-01'"
    assert represent_untrusted_python_value({"items": [raw], "single": (raw,)}) == (
        "{'items': ['2026-01-01'], 'single': ('2026-01-01',)}"
    )
    assert represent_untrusted_python_value(None) == "None"
    assert represent_untrusted_python_value(1.5) == "1.5"
    assert represent_python_value(float("nan")) == "float('nan')"
    assert represent_untrusted_python_value({"values": {"a", "b"}, "empty": set()}) == (
        "{'values': {'a', 'b'}, 'empty': set()}"
    )

    class StringOnly:
        def __str__(self) -> str:
            return "not code"

    assert represent_untrusted_python_value(StringOnly()) == "'not code'"
    assert is_safe_public_type_name("datetime.date")
    assert not is_safe_public_type_name("list[str] | None")
    assert not is_safe_public_type_name("__import__('os')")
    assert not is_safe_public_type_name("class")
    assert not is_safe_public_type_name(object())
    assert represent_untrusted_public_type_name("datetime.date") == "datetime.date"
    assert represent_untrusted_public_type_name(PythonCode("__import__('os').system('id')")) == (
        "\"__import__('os').system('id')\""
    )

    class HostileTypeName(str):  # noqa: FURB189, SLOT000 - intentionally exercises hostile string subclasses
        def split(
            self, *_: object, **__: object
        ) -> list[str]:  # pragma: no cover - the serializer must bypass this override
            return ["str"]

        def __str__(self) -> str:  # pragma: no cover - the serializer must bypass this override
            return "__import__('os').system('marker') or str"

    hostile_type_name = HostileTypeName("__import__('os').system('marker') or str")
    safe_type_name = HostileTypeName("str")
    assert type(_normalize_string(safe_type_name)) is str
    assert not is_safe_public_type_name(hostile_type_name)
    assert represent_untrusted_public_type_name(hostile_type_name) == "\"__import__('os').system('marker') or str\""

    class EvilInt(int):
        def __repr__(self) -> str:  # pragma: no cover - the serializer must bypass this override
            return "__import__('os').system('marker')"

    class EvilFloat(float):
        def __repr__(self) -> str:  # pragma: no cover - the serializer must bypass this override
            return "__import__('os').system('marker')"

    class EvilList(list[object]):  # noqa: FURB189 - intentionally exercises hostile container subclasses
        def __repr__(self) -> str:  # pragma: no cover - the serializer must bypass this override
            return "__import__('os').system('marker')"

    class EvilTuple(tuple[object, ...]):  # noqa: SLOT001 - intentionally exercises hostile container subclasses
        def __repr__(self) -> str:  # pragma: no cover - the serializer must bypass this override
            return "__import__('os').system('marker')"

    class EvilDict(dict[object, object]):  # noqa: FURB189 - intentionally exercises hostile container subclasses
        def __repr__(self) -> str:  # pragma: no cover - the serializer must bypass this override
            return "__import__('os').system('marker')"

    class EvilSet(set[object]):
        def __repr__(self) -> str:  # pragma: no cover - the serializer must bypass this override
            return "__import__('os').system('marker')"

    assert represent_untrusted_python_value(EvilInt(1)) == "1"
    assert represent_untrusted_python_value(EvilFloat(float("nan"))) == "float('nan')"
    assert represent_untrusted_python_value(EvilList([EvilInt(1)])) == "[1]"
    assert represent_untrusted_python_value(EvilTuple((EvilInt(1),))) == "(1,)"
    assert represent_untrusted_python_value(EvilDict({"value": EvilInt(1)})) == "{'value': 1}"
    assert represent_untrusted_python_value(EvilSet({EvilInt(1)})) == "{1}"


def test_decimal_detection_and_integer_constraint_edges() -> None:
    """Test Decimal detection and integer constraint normalization edge cases."""
    sentinel = object()

    assert _contains_decimal([Decimal(1)])
    assert normalize_integer_constraint("ge", sentinel) == ("ge", sentinel)
    assert normalize_integer_constraint("le", 1.5) == ("le", 1)
    assert normalize_integer_constraint("lt", 1.5) == ("le", 1)
    assert normalize_integer_constraint("unknown", 1.5) == ("unknown", 1.5)


def test_datatype_deepcopy_with_nested_data_types() -> None:
    """Test that DataType.__deepcopy__ properly copies nested data_types."""
    from copy import deepcopy

    # Import DataModelFieldBase first to trigger model_rebuild
    from datamodel_code_generator.model.base import DataModelFieldBase  # noqa: F401
    from datamodel_code_generator.types import DataType

    # Create nested DataTypes
    inner = DataType(type="InnerType", is_optional=True)
    outer = DataType(type="OuterType", data_types=[inner], is_list=True)

    # Deepcopy should work and create independent copies
    copied_outer = deepcopy(outer)

    # Verify the structure is preserved
    assert copied_outer.type == "OuterType"
    assert copied_outer.is_list is True
    assert len(copied_outer.data_types) == 1
    assert copied_outer.data_types[0].type == "InnerType"
    assert copied_outer.data_types[0].is_optional is True

    # Verify it's a deep copy (modifying original doesn't affect copy)
    inner.type = "ModifiedInnerType"
    assert copied_outer.data_types[0].type == "InnerType"


def test_datatype_deepcopy_memo_prevents_duplicate_copies() -> None:
    """Test that the memo dictionary prevents duplicate copies of the same object."""
    from copy import deepcopy

    # Import DataModelFieldBase first to trigger model_rebuild
    from datamodel_code_generator.model.base import DataModelFieldBase  # noqa: F401
    from datamodel_code_generator.types import DataType

    # Create a shared DataType referenced by multiple parents
    shared = DataType(type="SharedType")
    container1 = DataType(type="Container1", data_types=[shared])
    container2 = DataType(type="Container2", data_types=[shared])
    root = DataType(type="Root", data_types=[container1, container2])

    # Deepcopy should handle the shared reference
    copied_root = deepcopy(root)

    # Verify structure is correct
    assert copied_root.type == "Root"
    assert len(copied_root.data_types) == 2
    assert copied_root.data_types[0].type == "Container1"
    assert copied_root.data_types[1].type == "Container2"

    # Both containers should have copies of the shared type
    assert copied_root.data_types[0].data_types[0].type == "SharedType"
    assert copied_root.data_types[1].data_types[0].type == "SharedType"

    # Verify that the same object is returned from memo (memoization behavior)
    assert copied_root.data_types[0].data_types[0] is copied_root.data_types[1].data_types[0]


def test_datatype_deepcopy_with_none_memo() -> None:
    """Test __deepcopy__ when called with memo=None (covers memo initialization)."""
    # Import DataModelFieldBase first to trigger model_rebuild
    from datamodel_code_generator.model.base import DataModelFieldBase  # noqa: F401
    from datamodel_code_generator.types import DataType

    data_type = DataType(type="TestType", is_optional=True)

    # Call __deepcopy__ directly with None memo to cover the `if memo is None` branch
    copied = data_type.__deepcopy__(None)  # noqa: PLC2801

    assert copied.type == "TestType"
    assert copied.is_optional is True
    assert copied is not data_type


def test_datatype_type_hint_container_precedence_matches_base_type_hint() -> None:
    """Pin asymmetric container precedence for type_hint and base_type_hint."""
    from datamodel_code_generator.model.base import DataModelFieldBase  # noqa: F401

    cases = [
        (DataType(type="str", is_list=True, is_set=True), "Set[str]", "List[str]"),
        (DataType(type="str", is_frozen_set=True, is_list=True), "FrozenSet[str]", "List[str]"),
        (DataType(type="str", is_sequence=True), "Sequence[str]", "str"),
        (DataType(type="str", is_mapping=True), "Mapping[str, str]", "str"),
    ]

    for data_type, expected_type_hint, expected_base_type_hint in cases:
        assert data_type.type_hint == expected_type_hint
        assert data_type.base_type_hint == expected_base_type_hint


@pytest.mark.parametrize(
    ("data_types", "tuple_item_count", "expected", "expected_imports"),
    [
        ([], 0, "Tuple[()]", (IMPORT_TUPLE,)),
        ([], 2, "Tuple[Any, Any]", (IMPORT_ANY, IMPORT_TUPLE)),
        ([DataType()], 2, "Tuple[Any, Any]", (IMPORT_ANY, IMPORT_TUPLE)),
        ([DataType(type="str")], 3, "Tuple[str, str, str]", (IMPORT_TUPLE,)),
    ],
)
def test_datatype_fixed_length_tuple_renders_without_repeated_data_types(
    data_types: list[DataType], tuple_item_count: int, expected: str, expected_imports: tuple[object, ...]
) -> None:
    """Render homogeneous tuples from one item type without expanding the type tree."""
    data_type = DataType(data_types=data_types, is_tuple=True, tuple_item_count=tuple_item_count)

    assert data_type.type_hint == expected
    assert data_type.base_type_hint == expected
    assert tuple(data_type.all_imports) == expected_imports


def test_external_datatype_subclass_keeps_legacy_rendering_contract() -> None:
    """External DataType subclasses retain discriminator and base-hint behavior."""
    from datamodel_code_generator.model.base import DataModelFieldBase  # noqa: F401

    class ExternalDataType(DataType):
        _CONSTRAINED_TYPE_TO_BASE: ClassVar[dict[str, str]] = {
            **DataType._CONSTRAINED_TYPE_TO_BASE,
            "custom_constr": "bytes",
        }

    data_type = ExternalDataType(
        data_types=[
            ExternalDataType(type="custom_constr", is_func=True, kwargs={"limit": 1}),
            ExternalDataType(type="int"),
        ],
        discriminator="kind",
    )

    assert data_type.type_hint == "Annotated[Union[custom_constr(limit=1), int], Field(discriminator='kind')]"
    assert data_type.base_type_hint == "Union[bytes, int]"


def test_datatype_type_hint_uses_dict_key_render_selector() -> None:
    """Pin dict key rendering for constrained key types."""
    from datamodel_code_generator.model.base import DataModelFieldBase  # noqa: F401

    data_type = DataType(
        type="str",
        is_dict=True,
        dict_key=DataType(type="constr", is_func=True, kwargs={"pattern": "^a$"}),
    )

    assert data_type.type_hint == "Dict[constr(pattern='^a$'), str]"
    assert data_type.base_type_hint == "Dict[str, str]"


def test_datatype_type_hint_keeps_bare_dict_without_inner_type() -> None:
    """Pin bare dict rendering when no key or value type is available."""
    from datamodel_code_generator.model.base import DataModelFieldBase  # noqa: F401

    data_type = DataType(is_dict=True)

    assert data_type.type_hint == "Dict"
    assert data_type.base_type_hint == "Dict"


def test_datatype_type_hint_without_container_flag_returns_inner_type() -> None:
    """Pin the fallback path when no configured container flag matches."""
    from datamodel_code_generator.model.base import DataModelFieldBase  # noqa: F401

    data_type = DataType(data_types=[DataType(type="str")])

    assert data_type.type_hint == "str"
    data_type._materialize_render_state("reference-null")
    assert data_type.is_optional is False


def test_datatype_module_name_reads_reference_source_attribute() -> None:
    """Pin module-name lookup through the reference source."""
    from datamodel_code_generator.model.base import DataModelFieldBase  # noqa: F401

    class ModuleReferenceSource:
        reference = None
        module_name = "pkg.models"

    reference = Reference(
        path="Model",
        name="Model",
        source=ModuleReferenceSource(),
    )
    data_type = DataType(reference=reference)

    assert data_type.module_name == "pkg.models"


def test_datatype_base_type_hint_applies_reference_nullability() -> None:
    """Pin nullable reference propagation for base type hints."""
    from datamodel_code_generator.model.base import DataModelFieldBase  # noqa: F401

    class NullableReferenceSource:
        reference = None
        nullable = True
        is_alias = False

    reference = Reference(
        path="Model",
        name="Model",
        source=NullableReferenceSource(),
    )
    data_type = DataType(reference=reference)

    assert data_type.base_type_hint == "Optional[Model]"


def test_datatype_nullable_reference_keeps_alias_non_optional() -> None:
    """Pin alias guard before applying reference nullability."""
    from datamodel_code_generator.model.base import DataModelFieldBase  # noqa: F401

    class AliasNullableReferenceSource:
        reference = None
        nullable = True
        is_alias = True

    reference = Reference(
        path="Model",
        name="Model",
        source=AliasNullableReferenceSource(),
    )
    data_type = DataType(reference=reference)

    data_type._materialize_render_state("reference-null")

    assert data_type.is_optional is False


@pytest.mark.parametrize(
    ("data_types", "expected_type_hint", "expected_base_type_hint"),
    [
        (
            lambda: [DataType(type="str"), DataType(type="str", is_optional=True)],
            "Union[str, Optional[str]]",
            "Union[str, Optional[str]]",
        ),
        (
            lambda: [DataType(type="str", is_optional=True), DataType(type="str")],
            "Union[Optional[str], str]",
            "Union[Optional[str], str]",
        ),
        (
            lambda: [
                DataType(type="constr", is_func=True, kwargs={"pattern": "^a$"}),
                DataType(type="constr", is_func=True, kwargs={"pattern": "^a$"}, is_optional=True),
            ],
            "Union[constr(pattern='^a$'), Optional[constr]]",
            "Union[str, Optional[str]]",
        ),
        (
            lambda: [
                DataType(type="constr", is_func=True, kwargs={"pattern": "^a$"}, is_optional=True),
                DataType(type="constr", is_func=True, kwargs={"pattern": "^a$"}),
            ],
            "Union[Optional[constr], constr(pattern='^a$')]",
            "Union[Optional[str], str]",
        ),
    ],
)
def test_datatype_union_rendering_preserves_order_and_base_selector(
    data_types: Callable[[], list[DataType]],
    expected_type_hint: str,
    expected_base_type_hint: str,
) -> None:
    """Pin order-sensitive union rendering and base hint recursion."""
    from datamodel_code_generator.model.base import DataModelFieldBase  # noqa: F401

    data_type = DataType(data_types=data_types())

    assert data_type.type_hint == expected_type_hint
    assert data_type.base_type_hint == expected_base_type_hint


@pytest.mark.parametrize(
    ("first_hint", "expected_first", "expected_second"),
    [
        ("type_hint", "Optional[str]", "Optional[str]"),
        ("base_type_hint", "Optional[str]", "Optional[str]"),
    ],
)
def test_datatype_repeated_property_access_writes_no_nullability(
    first_hint: str,
    expected_first: str,
    expected_second: str,
) -> None:
    """Pin repeated hint rendering: a union renders its members' None without writing it."""
    from datamodel_code_generator.model.base import DataModelFieldBase  # noqa: F401

    child = DataType(data_types=[DataType(type="str"), DataType(type="None")])
    data_type = DataType(data_types=[child])

    if first_hint == "type_hint":
        assert data_type.type_hint == expected_first
        assert data_type.base_type_hint == expected_second
    else:
        assert data_type.base_type_hint == expected_first
        assert data_type.type_hint == expected_second
    assert data_type.is_optional is False
    assert child.is_optional is False


@pytest.mark.parametrize(
    ("data_type_factory", "expected_type_hint", "expected_base_type_hint"),
    [
        (
            lambda: DataType(type="constr", is_func=True, kwargs={"pattern": "^a$"}, is_optional=True),
            "Optional[constr]",
            "Optional[str]",
        ),
        (
            lambda: DataType(type="conint", is_func=True, kwargs={"gt": 0}, is_optional=True),
            "Optional[conint]",
            "Optional[conint]",
        ),
    ],
)
def test_datatype_optional_func_type_hint_order(
    data_type_factory: Callable[[], DataType],
    expected_type_hint: str,
    expected_base_type_hint: str,
) -> None:
    """Pin optional wrapping before function kwargs rendering."""
    from datamodel_code_generator.model.base import DataModelFieldBase  # noqa: F401

    data_type = data_type_factory()

    assert data_type.type_hint == expected_type_hint
    assert data_type.base_type_hint == expected_base_type_hint


def test_datatype_deepcopy_memo_cache_hit() -> None:
    """Test that memo cache returns the same object for repeated references."""
    # Import DataModelFieldBase first to trigger model_rebuild
    from datamodel_code_generator.model.base import DataModelFieldBase  # noqa: F401
    from datamodel_code_generator.types import DataType

    data_type = DataType(type="TestType")
    memo: dict[int, DataType] = {}

    # First call - should create new object and store in memo
    copied1 = data_type.__deepcopy__(memo)  # noqa: PLC2801
    assert copied1 is not data_type
    assert id(data_type) in memo

    # Second call with same memo - should return cached object (covers memo hit branch)
    copied2 = data_type.__deepcopy__(memo)  # noqa: PLC2801
    assert copied2 is copied1  # Same object from memo


@pytest.mark.parametrize(
    ("type_str", "expected"),
    [
        # Simple types
        ("str", "str"),
        ("int", "int"),
        ("List", "List"),
        # Subscripted types
        ("List[str]", "List"),
        ("Dict[str, int]", "Dict"),
        ("Optional[int]", "Optional"),
        ("Union[str, int]", "Union"),
        # Qualified names
        ("foo.bar.Baz", "Baz"),
        ("datamodel_code_generator.model.base.DataModel", "DataModel"),
        # Subscripted with qualified names
        ("type[foo.bar.Baz]", "type"),
        ("List[foo.Bar]", "List"),
        # Preserve the legacy first-generic fallback for a union root
        ("my.custom.Iterable[str] | None", "Iterable"),
        # Invalid syntax (fallback to string parsing)
        ("List[", "List"),
        ("[invalid", ""),  # splits on "[" giving empty string
    ],
)
def test_get_type_base_name(type_str: str, expected: str) -> None:
    """Test get_type_base_name extracts base type correctly."""
    assert get_type_base_name(type_str) == expected


@pytest.mark.parametrize(
    ("type_str", "expected"),
    [
        # Simple types (no subscript)
        ("str", []),
        ("int", []),
        # Single argument
        ("List[str]", ["str"]),
        ("Optional[int]", ["int"]),
        ("type[Foo]", ["Foo"]),
        # Multiple arguments
        ("Dict[str, int]", ["str", "int"]),
        ("Union[str, int, None]", ["str", "int", "None"]),
        ("Tuple[int, str, float]", ["int", "str", "float"]),
        # Union operator syntax
        ("str | int", ["str", "int"]),
        ("str | int | None", ["str", "int", "None"]),
        ("List[str] | None", ["List[str]", "None"]),
        ("tuple[()] | None", ["tuple[()]", "None"]),
        # Complex nested types
        ("Dict[str, List[int]]", ["str", "List[int]"]),
        ("Union[List[str], Dict[str, int]]", ["List[str]", "Dict[str, int]"]),
        # Qualified names in arguments
        ("type[foo.bar.Baz]", ["foo.bar.Baz"]),
        ("Dict[a.B, c.D]", ["a.B", "c.D"]),
        # Variadics and canonicalized non-finite numeric literals
        ("tuple[*Ts]", ["*Ts"]),
        ("Literal[1e309, -1e309]", ["1e309", "-1e309"]),
        # Invalid syntax
        ("List[", []),
        ("[invalid", []),
    ],
)
def test_get_subscript_args(type_str: str, expected: list[str]) -> None:
    """Test get_subscript_args extracts type arguments correctly."""
    assert get_subscript_args(type_str) == expected


@pytest.mark.parametrize(
    ("type_str", "expected"),
    [
        # No qualified names
        ("str", []),
        ("List[str]", []),
        ("Union[str, int]", []),
        # Single qualified name
        ("foo.Bar", ["foo.Bar"]),
        ("foo.bar.Baz", ["foo.bar.Baz"]),
        ("datamodel_code_generator.model.base.DataModel", ["datamodel_code_generator.model.base.DataModel"]),
        # Qualified names in subscript
        ("type[foo.bar.Baz]", ["foo.bar.Baz"]),
        ("List[foo.Bar]", ["foo.Bar"]),
        ("Optional[a.b.C]", ["a.b.C"]),
        # Multiple qualified names
        ("Dict[a.B, c.D]", ["a.B", "c.D"]),
        ("Union[foo.Bar, baz.Qux]", ["foo.Bar", "baz.Qux"]),
        # Mixed with simple types
        ("Dict[str, foo.Bar]", ["foo.Bar"]),
        ("Union[int, a.B, None]", ["a.B"]),
        # Union operator syntax
        ("foo.Bar | None", ["foo.Bar"]),
        ("a.B | c.D", ["a.B", "c.D"]),
        # Complex nested
        ("Dict[str, List[foo.Bar]]", ["foo.Bar"]),
        ("type[datamodel_code_generator.types.DataTypeManager]", ["datamodel_code_generator.types.DataTypeManager"]),
        # Attribute on non-Name (function call result) - should not extract
        ("foo().bar", []),
        ("func().attr.name", []),
        # Invalid syntax
        ("foo.Bar[", []),
    ],
)
def test_extract_qualified_names(type_str: str, expected: list[str]) -> None:
    """Test extract_qualified_names finds all fully qualified names."""
    assert extract_qualified_names(type_str) == expected
