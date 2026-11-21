"""End-to-end coverage for enum member lookups: the two rows that differ from JSON equality, and a set default."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from datamodel_code_generator import InputFileType, generate
from datamodel_code_generator.format import Formatter
from tests.conftest import assert_output


class Color(StrEnum):
    """The members a schema built in Python lists as the values of an enum."""

    RED = "red"
    BLUE = "blue"


def _generate_with_enum_member_defaults(schema: dict[str, Any], **options: Any) -> str:
    return generate(
        schema,
        input_file_type=InputFileType.JsonSchema,
        disable_timestamp=True,
        set_default_enum_member=True,
        formatters=[Formatter.BUILTIN],
        **options,
    )


def test_enum_member_lookup_compares_arrays_and_objects_as_python_does() -> None:
    """Find the member of an array or object default by Python equality: ``True`` finds the member that holds ``1``.

    ``2.0`` finds the one that holds ``2``, in an object, in an array, and in an array inside an object inside it.
    """
    schema = {
        "type": "object",
        "properties": {
            "flag": {"$ref": "#/$defs/Flags", "default": {"a": True}},
            "nested": {"$ref": "#/$defs/Nested", "default": {"a": True, "b": {"c": [2.0]}}},
            "arrays": {"type": "array", "items": {"$ref": "#/$defs/Arrays"}, "default": [[True], [2.0], [3]]},
        },
        "$defs": {
            "Flags": {"enum": [{"a": 1}, {"a": 2}]},
            "Nested": {"enum": [{"b": {"c": [2]}, "a": 1}, {"a": 2}]},
            "Arrays": {"enum": [[1], [2]]},
        },
    }
    assert_output(_generate_with_enum_member_defaults(schema))


def test_enum_member_lookup_tells_a_str_subclass_from_the_equal_str() -> None:
    """Keep a text default beside an enum that lists the members of a ``StrEnum``; a member as the default is found."""
    schema = {
        "type": "object",
        "properties": {
            "text": {"$ref": "#/$defs/Colors", "default": "red"},
            "member": {"$ref": "#/$defs/Colors", "default": Color.RED},
        },
        "$defs": {"Colors": {"enum": list(Color)}},
    }
    assert_output(_generate_with_enum_member_defaults(schema))


def test_enum_member_lookup_of_a_default_written_as_a_set_finds_no_member() -> None:
    """Keep the default of a unique-items array of an enum that ``--use-unique-items-as-set`` writes as a set."""
    schema = {
        "type": "object",
        "properties": {
            "tags": {"type": "array", "uniqueItems": True, "items": {"$ref": "#/$defs/Tag"}, "default": ["a", "b"]},
        },
        "$defs": {"Tag": {"enum": ["a", "b", "c"]}},
    }
    assert_output(_generate_with_enum_member_defaults(schema, use_unique_items_as_set=True))
