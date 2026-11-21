"""Required names that no class beside an OpenAPI schema declares, found in the schemas of the parents' parents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.main.conftest import OPEN_API_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path


def test_openapi_inherited_required_name_takes_the_type_of_a_distant_parent(output_file: Path) -> None:
    """Give a required name that the parent does not declare the type of the ``$ref`` sibling schema of its ancestors.

    The child is written before its parent, so the name is found through the schemas of the ancestors, not through a
    generated class.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "inherited_required_names" / "refsib.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin"],
    )
