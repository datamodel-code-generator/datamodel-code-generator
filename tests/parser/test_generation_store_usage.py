"""Tests for parser GenerationStore usage guard."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from inline_snapshot import snapshot

if TYPE_CHECKING:
    from types import ModuleType


def _load_checker() -> ModuleType:
    script_path = Path(__file__).parents[2] / "scripts" / "check_generation_store_usage.py"
    spec = importlib.util.spec_from_file_location("check_generation_store_usage", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parser_sources_use_generation_store_for_mutations() -> None:
    """Parser code should not bypass GenerationStore mutation helpers."""
    checker = _load_checker()

    violations = checker.check_paths([Path("src/datamodel_code_generator/parser")])

    assert violations == snapshot([])


def test_checker_uses_generation_store_api_surface() -> None:
    """Checker messages should stay aligned with the store mutation API."""
    checker = _load_checker()

    assert sorted(checker.GENERATION_STORE_MUTATION_METHODS) == snapshot(
        [
            "append_field",
            "collapse_root_data_type",
            "defer_refresh",
            "detach_data_type_ref",
            "detach_model_data_type_refs",
            "discard_derived_facts",
            "insert_field",
            "move_model",
            "note_changed_models",
            "redirect_model_reference_users",
            "redirect_reference_users",
            "register_model",
            "remove_field",
            "rename_model",
            "replace_data_type_ref",
            "replace_field_type",
            "replace_nested_data_type",
            "reset_base_classes",
            "set_base_classes",
            "set_fields",
            "set_nested_data_types",
            "update_model_reference",
        ],
    )


def test_generation_store_usage_checker_rejects_direct_mutations(tmp_path: Path) -> None:
    """The checker reports reference, field, and base-class mutations."""
    checker = _load_checker()
    bad_source = tmp_path / "bad_parser.py"
    bad_source.write_text(
        """
def mutate(model, field, data_type, ref):
    data_type.reference = ref
    model.reference.name = "Renamed"
    model.fields.append(field)
    self.results.append(model)
    model.base_classes = []
    model.reference.children
    field.replace_data_type(data_type)
""",
    )

    violations = checker.check_paths([bad_source])

    assert [(violation.line, violation.column, violation.message) for violation in violations] == snapshot(
        [
            (3, 5, "use GenerationStore API instead of assigning data_type.reference"),
            (4, 5, "use GenerationStore API instead of assigning reference.name"),
            (5, 5, "use GenerationStore.append_field() instead of mutating fields"),
            (6, 5, "use GenerationStore.register_model() instead of mutating results"),
            (7, 5, "use GenerationStore.set_base_classes() instead of assigning base_classes"),
            (8, 5, "use GenerationIndex reverse-edge queries instead of Reference.children"),
            (9, 5, "use GenerationStore API instead of replace_data_type()"),
        ],
    )
