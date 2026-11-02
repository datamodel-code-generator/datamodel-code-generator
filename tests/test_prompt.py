"""Tests for LLM prompt generation helpers."""

from __future__ import annotations

import json
from argparse import SUPPRESS, ArgumentParser, Namespace
from enum import Enum
from pathlib import Path

import pytest

from datamodel_code_generator.cli_options import OptionCategory
from datamodel_code_generator.prompt import (
    _actions_by_dest,
    _canonical_option,
    _format_current_options,
    _negative_option,
    _option_metadata,
    _positive_option,
    generate_prompt,
)
from tests.conftest import assert_output

EXPECTED_PROMPT_PATH = Path(__file__).parent / "data" / "expected" / "prompt"


class SerializableChoice(Enum):
    """Enum value used to verify JSON serialization."""

    VALUE = "enum-value"


class FallbackValue:
    """Object serialized through its string representation."""

    def __str__(self) -> str:
        """Return a stable string for assertions."""
        return "fallback-value"


class CallableType:
    """Callable without __name__ used as an argparse type."""

    def __call__(self, value: str) -> str:
        """Return the argparse value unchanged."""
        return value

    def __str__(self) -> str:
        """Return a stable type display name."""
        return "callable-type"


@pytest.mark.allow_direct_assert
def test_generate_prompt_json_serializes_fallback_current_options() -> None:
    """Serialize current options even when argparse has no matching action."""
    args = Namespace(
        output_format="json",
        generate_prompt="",
        config={"choice": SerializableChoice.VALUE},
        items=(Path("item.json"), SerializableChoice.VALUE),
        path=Path("schema.json"),
        raw=FallbackValue(),
    )

    payload = json.loads(generate_prompt(args, "\x1b[31mhelp\x1b[0m", ArgumentParser()))
    current_options = {option["name"]: option for option in payload["current_options"]}

    assert payload["help_text"] == "help"
    assert current_options["--config"]["value"] == {"choice": "enum-value"}
    assert current_options["--items"]["value"] == ["item.json", "enum-value"]
    assert current_options["--path"]["value"] == "schema.json"
    assert current_options["--raw"]["flags"] == ["--raw"]
    assert current_options["--raw"]["value"] == "fallback-value"


@pytest.mark.allow_direct_assert
def test_prompt_option_metadata_fallbacks() -> None:
    """Cover fallback metadata paths for non-standard argparse actions."""
    parser = ArgumentParser()
    callable_type = CallableType()
    short_action = parser.add_argument("-x", dest="x")
    custom_action = parser.add_argument("--custom", default=SUPPRESS, help=SUPPRESS, type=callable_type)

    assert _canonical_option(short_action) == "-x"
    assert _positive_option(short_action, "--x") == "--x"
    assert _negative_option(short_action, "--no-x") == "--no-x"
    assert callable_type("value") == "value"

    metadata = _option_metadata(custom_action)
    assert metadata.category == OptionCategory.GENERAL.value
    assert metadata.default is None
    assert not metadata.description
    assert metadata.type == "callable-type"


@pytest.mark.allow_direct_assert
def test_format_current_options_without_parser() -> None:
    """Format current options when no parser metadata is available."""
    assert _actions_by_dest(None) == {}
    assert _format_current_options(Namespace(disabled=False), None) == "(No options specified)"




@pytest.mark.allow_direct_assert
def test_format_current_options_with_empty_store_const() -> None:
    """Format an empty store_const value using its negative option."""
    parser = ArgumentParser()
    parser.add_argument(
        "--deserialize-default-values",
        nargs="+",
        default=None,
    )
    parser.add_argument(
        "--no-deserialize-default-values",
        dest="deserialize_default_values",
        action="store_const",
        const=(),
        default=None,
    )
    args = parser.parse_args(["--no-deserialize-default-values"])

    assert _format_current_options(args, parser) == "--no-deserialize-default-values"


@pytest.mark.allow_direct_assert
def test_format_current_options_with_dictionary() -> None:
    """Format dictionary options as valid JSON."""
    parser = ArgumentParser()
    parser.add_argument("--model-name-map", type=json.loads)
    args = parser.parse_args(["--model-name-map", '{"A": "B"}'])

    assert _format_current_options(args, parser) == '--model-name-map \'{"A": "B"}\''




def test_generate_prompt_uses_default_parser_for_text_output() -> None:
    """Use the shared parser when generate_prompt is called without one."""
    prompt = generate_prompt(Namespace(output_format="text", generate_prompt="", input="schema.json"), "HELP")

    assert_output(prompt, EXPECTED_PROMPT_PATH / "default_parser_text.txt")
