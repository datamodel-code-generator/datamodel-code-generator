"""A msgspec struct that declares again a field its keyword-only parent declares again (#7478).

msgspec puts the keyword-only fields of each struct after its positional ones, and a struct that declares such a field
again without ``kw_only=True`` makes it positional where its parent put it, after the defaults it inherits. Such a
struct is written ``kw_only=True`` as its parent is; every other struct is written as before.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.capabilities import needs
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, OPEN_API_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUTS = JSON_SCHEMA_DATA_PATH / "msgspec_restated_keyword_only"
OPENAPI_INPUTS = OPEN_API_DATA_PATH / "msgspec_restated_keyword_only"
MSGSPEC = ("--output-model-type", "msgspec.Struct", "--formatters", "builtin")
OPTION_SETS = {
    "default": (),
    "use-annotated": ("--use-annotated",),
    "collapse-root-models": ("--collapse-root-models",),
    "preset": ("--preset", "standard-py314-20261125"),
    "final-names": ("--order-constructor-fields-by", "final-names"),
    "written-defaults": ("--order-constructor-fields-by", "written-defaults"),
}
SHAPES = {
    "narrowed-same": ("narrowed_same", "Leaf"),
    "narrowed-further": ("narrowed_further", "Leaf"),
    "restated-required": ("restated_required", "Leaf"),
    "restated-with-default": ("restated_with_default", "Leaf"),
    "mid-restates-unchanged": ("mid_restates_unchanged", "Leaf"),
    "deeper": ("deeper", "Leaf"),
    "root-typed": ("root_typed", "Leaf"),
    "own-optional-field": ("own_optional_field", "Leaf"),
    "use-default": ("use_default", "L"),
}
CASES = (
    *(("narrowed-same", option_set) for option_set in OPTION_SETS),
    *(("deeper", option_set) for option_set in OPTION_SETS if option_set != "collapse-root-models"),
    *(("root-typed", option_set) for option_set in ("default", "collapse-root-models", "preset")),
    *(
        (shape, "default")
        for shape in (
            "narrowed-further",
            "restated-required",
            "restated-with-default",
            "mid-restates-unchanged",
            "own-optional-field",
        )
    ),
)
STRUCT_OPTIONS = ("--extra-template-data", json.dumps({"mid.Mid": {"base_class_kwargs": {"kw_only": True}}}))


@pytest.mark.parametrize(
    ("shape", "option_set"),
    [
        pytest.param(
            shape,
            option_set,
            id=f"{shape}-{option_set}",
            marks=[needs("type-statements")] if option_set == "preset" else [],
        )
        for shape, option_set in CASES
    ],
)
def test_restated_field_below_keyword_only_struct(output_file: Path, shape: str, option_set: str) -> None:
    """Write ``kw_only=True`` on the struct that declares again a field its keyword-only parent moved last.

    The parent ``Mid`` declares the required ``a`` of ``Base`` again after the default of ``b``, so msgspec makes it
    keyword-only there; ``Leaf`` declares ``a`` again, a field of its own after its fields keep their order.
    """
    stem, model = SHAPES[shape]
    run_main_and_assert(
        input_path=INPUTS / f"{stem}.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*MSGSPEC, *OPTION_SETS[option_set]],
        importable_module_name=f"restated_keyword_only_{stem}",
        importable_module_attribute=model,
    )


def test_restated_field_below_keyword_only_struct_use_default(output_file: Path) -> None:
    """Under ``--use-default`` the field that ``L`` declares again with a default stays required, after one."""
    run_main_and_assert(
        input_path=INPUTS / "use_default.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*MSGSPEC, "--use-default"],
        importable_module_name="restated_keyword_only_use_default",
        importable_module_attribute="L",
    )


@pytest.mark.parametrize(
    "option_set",
    [
        pytest.param((), id="default"),
        pytest.param(("--preset", "standard-py314-20261125"), id="preset"),
    ],
)
def test_restated_field_below_keyword_only_struct_modules(output_dir: Path, option_set: tuple[str, ...]) -> None:
    """Each of the three structs in a module of its own: only the module of ``Leaf`` changes."""
    run_main_and_assert(
        input_path=OPENAPI_INPUTS / "modules.json",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[*MSGSPEC, *option_set],
    )


@pytest.mark.parametrize(
    "option_set",
    [
        pytest.param((), id="struct-options"),
        pytest.param(("--rename-fields-hiding-types-from-type-checkers",), id="rename-fields-hiding-types"),
    ],
)
def test_restated_field_below_keyword_only_struct_options(output_dir: Path, option_set: tuple[str, ...]) -> None:
    """A parent the user's struct options make keyword-only counts as one made keyword-only by the output.

    The fields named like a type keep their names in ``common`` and ``mid``: the renaming reads where the fields go as
    the modules are written, and a struct written ``kw_only=True`` assigns no field.
    """
    run_main_and_assert(
        input_path=OPENAPI_INPUTS / "struct_options.json",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[*MSGSPEC, *STRUCT_OPTIONS, *option_set],
    )


@pytest.mark.parametrize(
    ("stem", "option_set"),
    [
        pytest.param("restates_parent_own_field", (), id="restates-parent-own-field"),
        pytest.param("restates_optional", (), id="restates-optional"),
        pytest.param("plain_parent", (), id="plain-parent"),
        pytest.param("two_level", (), id="two-level"),
        pytest.param("own_required_field", (), id="own-required-field"),
        pytest.param("two_parents", (), id="two-parents"),
        pytest.param("two_parents_other_first", (), id="two-parents-other-first"),
        pytest.param(
            "use_default",
            ("--use-default", "--keep-defaults-of-inherited-required-fields"),
            id="keep-inherited-defaults",
        ),
        pytest.param(
            "inherited_copy",
            ("--use-default", "--keep-defaults-of-inherited-required-fields"),
            id="inherited-copy",
        ),
        pytest.param("narrowed_same", ("--keyword-only",), id="keyword-only"),
        pytest.param("tag_opt_out", ("--strip-default-none",), id="tag-opt-out"),
    ],
)
def test_restated_field_below_keyword_only_struct_keeps(
    output_file: Path, stem: str, option_set: tuple[str, ...]
) -> None:
    """Structs msgspec creates as they are written keep their form.

    ``L`` under ``--keep-defaults-of-inherited-required-fields`` stays ``kw_only=True`` as in every release since
    0.72.0, though msgspec would create it without (row ``legacy-struct-conflicts-at-collected-places``). ``Leaf``
    of ``inherited_copy`` declares the required ``c`` of ``Mid`` once as a keyword-only copy, which both placements
    ask for. ``Parrot`` of ``tag_opt_out`` opts out of the tag of ``Bird`` and stays ``kw_only=True`` as its tag field,
    which ``--strip-default-none`` gives no default for its place, follows ``wings``: only the tag opt-out reads it
    there, as ``Bird`` declares the tag field as a class variable.
    """
    run_main_and_assert(
        input_path=INPUTS / f"{stem}.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*MSGSPEC, *option_set],
    )


@pytest.mark.parametrize("output_model_type", ["dataclasses.dataclass", "pydantic_v2.dataclass"])
@pytest.mark.parametrize("stem", ["narrowed_same", "deeper"])
def test_restated_field_below_keyword_only_struct_other_backends(
    output_file: Path, output_model_type: str, stem: str
) -> None:
    """Dataclasses keep a field declared again where it was first declared, so their output stays as it was."""
    run_main_and_assert(
        input_path=INPUTS / f"{stem}.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", output_model_type, "--formatters", "builtin"],
        importable_module_name=f"restated_keyword_only_{stem}_dataclass",
    )


@pytest.mark.parametrize("output_model_type", ["dataclasses.dataclass", "pydantic_v2.dataclass"])
def test_restated_field_below_keyword_only_struct_other_backends_modules(
    output_dir: Path, output_model_type: str
) -> None:
    """The modules of dataclasses stay as they were."""
    run_main_and_assert(
        input_path=OPENAPI_INPUTS / "modules.json",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--output-model-type", output_model_type, "--formatters", "builtin"],
    )
