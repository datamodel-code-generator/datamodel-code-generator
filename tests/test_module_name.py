"""Tests for dotted model-name interpretation."""

from __future__ import annotations

from pathlib import Path

from datamodel_code_generator.reference import get_inferred_module_name, split_module_name
from tests.conftest import assert_output

EXPECTED_PATH = Path(__file__).parent / "data" / "expected" / "module_name"


def test_split_module_name() -> None:
    """Apply strict validation only to automatic dotted-name inference."""
    cases = [
        ("models.Pet", None, False),
        ("SaveTrifectaV2.1", None, False),
        ("pkg.class.Model", None, False),
        ("models.Pet", None, True),
        ("pkg.日本語", None, True),
        ("match.case", None, True),
        ("pkg.1.Model", None, True),
        ("SaveTrifectaV2.1", None, True),
        ("pkg.class", None, True),
        ("pkg.user-name", None, True),
        (".Pet", None, True),
        ("pkg.", None, True),
        ("pkg..Pet", None, True),
        ("\N{KELVIN SIGN}.Model", None, True),
        ("pkg.\N{FULLWIDTH LATIN CAPITAL LETTER A}", None, True),
        ("Pet", None, True),
        ("models.Pet", False, True),
        ("SaveTrifectaV2.1", False, True),
        ("models.Pet", True, True),
        ("SaveTrifectaV2.1", True, True),
    ]
    result = "".join(
        f"{treat_dot_as_module!r}, {strict!r} | {name!r} -> "
        f"{split_module_name(name, treat_dot_as_module=treat_dot_as_module, strict_dotted_module_names=strict)!r}\n"
        for name, treat_dot_as_module, strict in cases
    )

    assert_output(result, EXPECTED_PATH / "split_module_name.txt")


def test_get_inferred_module_name() -> None:
    """Return a parent module only when the dotted name should be split."""
    cases = [
        ("models.Pet", None, False),
        ("Pet", None, True),
        ("SaveTrifectaV2.1", None, True),
        ("models.Pet", False, True),
        ("SaveTrifectaV2.1", True, True),
    ]
    infer = get_inferred_module_name
    result = "".join(
        f"{treat_dot_as_module!r}, {strict!r} | {name!r} -> "
        f"{infer(name, treat_dot_as_module=treat_dot_as_module, strict_dotted_module_names=strict)!r}\n"
        for name, treat_dot_as_module, strict in cases
    )

    assert_output(result, EXPECTED_PATH / "get_inferred_module_name.txt")
