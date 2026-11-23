"""Mapping keys of a discriminator, read as the values of the scalar type that writes the tag property."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import OPEN_API_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "args",
    [
        pytest.param([], id="released"),
        pytest.param(["--strict-types", "int", "float", "bool"], id="strict_types"),
        pytest.param(["--use-decimal-for-multiple-of"], id="decimal_for_multiple_of"),
        pytest.param(["--type-mappings", "number+double=decimal"], id="decimal_type_mapping"),
        pytest.param(["--use-non-positive-negative-number-constrained-types"], id="non_positive_negative_types"),
    ],
)
def test_scalar_mapping_keys_of_a_tag_property(args: list[str], output_file: Path) -> None:
    """Read mapping keys as the integers and booleans of the scalar type that writes the tag property.

    Each union maps the text keys ``"1"`` and ``"2"`` (``"true"`` and ``"false"``, ``"1.5"`` and ``"2.5"``) to variants
    whose ``kind`` is an integer, a bounded integer, a number, a bounded number, a double, a multiple of 0.5 or a
    boolean. The tags are ``Literal[1]``, not ``Literal['1']``, also where ``--strict-types`` writes ``StrictInt``,
    ``StrictFloat`` or ``StrictBool``, ``--use-decimal-for-multiple-of`` writes ``condecimal`` and
    ``--type-mappings number+double=decimal`` writes ``Decimal``; the keys of a number that no ``Literal`` holds leave
    its variants untagged. The integers and numbers bounded by 0 write ``PositiveInt``, ``NegativeInt`` and the float
    counterparts, and under ``--use-non-positive-negative-number-constrained-types`` ``NonNegativeInt`` and
    ``NonPositiveInt`` too.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "variant_tag_values" / "scalar_kind_mapped.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", "--disable-timestamp", *args],
    )
