"""Chains of aliases whose keywords merge with their target, which the cycle check walks once for all of them."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path


def test_main_jsonschema_ref_alias_chain(output_file: Path) -> None:
    """Merge the aliases of a long chain with their targets, as when each was checked for a cycle on its own (#6094).

    ``Chain0`` to ``Chain120`` each hold a ``$ref`` beside ``minLength``, so every alias asks whether its target reaches
    a cycle; the walk from ``Chain1`` answers for the whole chain, where it used to repeat once per alias. ``Side``
    names the middle of the chain through ``Mid``, ``Entry`` names a cycle that ``Loop1`` and ``Loop2`` close, and
    ``Open0`` ends in a schema without keywords: a reference is circular when it lies on a cycle or reaches such a
    schema, and not when it only leads into a cycle.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "ref_alias_chain.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", "--formatters", "builtin"],
        force_exec_validation=True,
    )


@pytest.mark.filterwarnings("ignore:msgspec.Meta does not take min_length on Open:UserWarning")
def test_main_jsonschema_ref_alias_chain_msgspec(output_file: Path) -> None:
    """Keep the msgspec aliases of a ``$ref`` loop whose links carry keywords: they are no loop of bare names.

    ``Loop1`` and ``Loop2`` name each other inside ``Annotated`` with their ``Meta``, so each writes more than the next
    one's name; msgspec is warned about as for any alias that names itself through no Struct, and nothing is rejected.
    """
    with pytest.warns(UserWarning, match=r"msgspec cannot decode the type aliases Loop1, Loop2, or a type that uses"):
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "ref_alias_chain.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--output-model-type", "msgspec.Struct", "--disable-timestamp", "--formatters", "builtin"],
        )
