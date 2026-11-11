"""Required names that no class beside a child declares, found in the schemas of the parents' parents."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

REQUIRED_NAMES_PATH = JSON_SCHEMA_DATA_PATH / "inherited_required_names"


CASES = {
    "refsib.json": (),
    "alias.json": ("--intersect-combined-schema-keywords",),
    "siblings.json": ("--intersect-combined-schema-keywords",),
    "cond.json": ("--generate-schema-validators",),
    "lists.json": ("--intersect-combined-schema-keywords",),
    "deep.json": ("--intersect-combined-schema-keywords",),
    "items.json": ("--intersect-combined-schema-keywords",),
    "inline.json": (),
}


@pytest.mark.parametrize("fixture", CASES)
def test_inherited_required_name_takes_the_type_of_a_distant_parent(output_file: Path, fixture: str) -> None:
    """Give a required name that the child's parent does not declare the type the schemas of its ancestors give.

    The child is written before its parent, so the name is found through the ``$ref`` siblings, the alias chain or the
    conditional properties of the ancestors, not through a generated class.
    """
    run_main_and_assert(
        input_path=REQUIRED_NAMES_PATH / fixture,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", *CASES[fixture]],
    )
