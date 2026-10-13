"""Tests for preset documentation generation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import black
import pytest
from packaging import version

from datamodel_code_generator._format_types import PythonVersion
from datamodel_code_generator.enums import DataModelType, DefaultValueType, ExtraFields, InputFileType
from datamodel_code_generator.preset import (
    PresetConfig,
    PresetConfigItem,
    PresetContext,
    PresetOptionGroup,
    get_latest_preset_name,
    get_preset_infos,
    render_presets,
    resolve_preset_config_updates,
)
from scripts import build_preset_docs
from tests.conftest import assert_output

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_preset_docs.py"
EXPECTED_PRESET_DOCS_PATH = Path(__file__).resolve().parent / "data" / "expected" / "preset_docs"
BLACK_LT_233 = version.parse("23.3.0") > version.parse(black.__version__)


@pytest.mark.skipif(BLACK_LT_233, reason="Installed black doesn't support the Python 3.12 quick-start target")
def test_build_preset_docs_check_is_up_to_date() -> None:
    """Generated preset docs and quick-start examples are committed."""
    subprocess.run([sys.executable, str(SCRIPT), "--check"], check=True)


def test_build_preset_docs_json_format() -> None:
    """The docs script can print preset metadata as JSON."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert_output(result.stdout, EXPECTED_PRESET_DOCS_PATH / "presets_json.txt")


def test_build_preset_docs_check_help_mentions_all_generated_targets() -> None:
    """The --check help text covers all generated preset docs outputs."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert_output(result.stdout, EXPECTED_PRESET_DOCS_PATH / "build_preset_docs_help.txt")


def test_preset_metadata_renderers() -> None:
    """Preset metadata renderers expose the committed preset reference."""
    assert_output(render_presets("markdown"), EXPECTED_PRESET_DOCS_PATH / "presets_markdown.txt")
    assert_output(render_presets("json"), EXPECTED_PRESET_DOCS_PATH / "presets_json.txt")
    assert_output(
        f"{get_latest_preset_name()}\n{get_preset_infos()[0].name.value}\n",
        EXPECTED_PRESET_DOCS_PATH / "preset_names.txt",
    )


def test_readme_supported_sections_are_rendered_from_enums() -> None:
    """README supported input/output lists are synchronized from public enums."""
    assert_output(
        build_preset_docs._render_readme_supported_sections(),
        EXPECTED_PRESET_DOCS_PATH / "readme_supported_sections.txt",
    )


def test_marked_section_replacement_uses_end_marker_after_begin() -> None:
    """Marked section replacement ignores matching text before the begin marker."""
    markdown = "Earlier literal <!-- END -->\n<!-- BEGIN -->\nstale\n<!-- END -->\nAfter\n"

    assert_output(
        build_preset_docs._replace_marked_section(markdown, "<!-- BEGIN -->", "<!-- END -->", "Generated"),
        EXPECTED_PRESET_DOCS_PATH / "marked_section_replacement.txt",
    )


def test_marked_section_replacement_indents_as_the_begin_marker() -> None:
    """A marked section inside a content tab keeps the generated block and its end marker in the tab."""
    markdown = '=== "Python"\n\n    <!-- BEGIN -->\n    stale\n    <!-- END -->\n\nAfter\n'

    assert_output(
        build_preset_docs._replace_marked_section(
            markdown, "<!-- BEGIN -->", "<!-- END -->", "```python\nx = 1\n\ny = 2\n```\n"
        ),
        EXPECTED_PRESET_DOCS_PATH / "marked_section_replacement_indented.txt",
    )


def test_preset_config_supports_enum_backed_string_and_sequence_options() -> None:
    """Preset config keeps string-backed enum and sequence options typed after validation."""
    enum_item = PresetConfig(extra_fields=ExtraFields.Forbid).items()[0]
    string_item = PresetConfig(extra_fields="allow").items()[0]
    sequence_item = PresetConfig(deserialize_default_values=(DefaultValueType.Decimal, DefaultValueType.Enum)).items()[
        0
    ]
    enabled_group = PresetOptionGroup(
        title="enabled",
        config=PresetConfig(deserialize_default_values=(DefaultValueType.Decimal, DefaultValueType.Enum)),
        description="",
    )
    disabled_group = PresetOptionGroup(
        title="disabled",
        config=PresetConfig(deserialize_default_values=()),
        description="",
    )
    output = "\n".join((
        _render_preset_config_item(enum_item),
        _render_preset_config_item(string_item),
        _render_preset_config_item(sequence_item),
        f"enabled_cli: {enabled_group.options[0]}",
        f"disabled_cli: {disabled_group.options[0]}",
        "",
    ))

    assert_output(output, EXPECTED_PRESET_DOCS_PATH / "preset_config_enum_values.txt")


def test_dated_presets_keep_default_deserialization_immutable() -> None:
    """Only the new preset family enables default deserialization, and explicit values override it."""
    context = PresetContext(
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        target_python_version=PythonVersion.PY_310,
    )
    cases = (
        ("standard-py310-20260619", set()),
        ("standard-py310-20260826", set()),
        ("practical-py310-20260826", set()),
        ("standard-py310-20260826", {"deserialize_default_values"}),
        ("practical-py310-20260826", {"deserialize_default_values"}),
    )
    lines: list[str] = []
    for preset_name, explicit_fields in cases:
        resolved = resolve_preset_config_updates(
            preset_name,
            context=context,
            use_annotated=False,
            explicit_fields=explicit_fields,
        )
        values = next(
            (item.applied_value for item in resolved.items if item.field_name == "deserialize_default_values"),
            (),
        )
        if not isinstance(values, tuple):  # pragma: no cover
            msg = f"Expected a tuple preset value, got {values!r}"
            raise TypeError(msg)
        legacy = next(
            (item.applied_value for item in resolved.items if item.field_name == "set_default_enum_member"),
            False,
        )
        label = f"{preset_name} explicit" if explicit_fields else preset_name
        lines.append(f"{label}: {[value.value for value in values]}, legacy={legacy}")

    assert_output("\n".join((*lines, "")), EXPECTED_PRESET_DOCS_PATH / "preset_decimal_defaults.txt")


def _render_preset_config_item(item: PresetConfigItem) -> str:
    match item.value, item.applied_value:
        case ExtraFields() as value, ExtraFields() as applied:
            return f"{item.field_name}: value={value.value}, applied={applied.value}, pyproject={item.pyproject_value}"
        case (DefaultValueType(), *_) as values, (DefaultValueType(), *_) as applied_values:
            return (
                f"{item.field_name}: value=[{', '.join(value.value for value in values)}], "
                f"applied=[{', '.join(value.value for value in applied_values)}], pyproject={item.pyproject_value}"
            )
    msg = f"Expected extra_fields preset config item, got {item!r}"  # pragma: no cover
    raise TypeError(msg)  # pragma: no cover
