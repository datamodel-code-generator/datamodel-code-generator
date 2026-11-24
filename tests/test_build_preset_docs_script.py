"""Tests for preset documentation generation."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import black
import pytest
import tomllib
from packaging import version

from datamodel_code_generator._format_types import PythonVersion
from datamodel_code_generator.enums import DataModelType, DefaultValueType, ExtraFields, InputFileType
from datamodel_code_generator.preset import (
    PresetConfig,
    PresetConfigItem,
    PresetContext,
    PresetError,
    PresetOptionGroup,
    get_latest_preset_name,
    get_preset_infos,
    get_preset_names,
    render_preset_history,
    render_presets,
    resolve_preset_config_updates,
)
from scripts import build_preset_docs
from tests.conftest import assert_output

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_preset_docs.py"
BLACK_LT_233 = version.parse("23.3.0") > version.parse(black.__version__)
RELEASED_DATES = frozenset(
    tomllib.loads((ROOT / "docs/data/migration/derived.toml").read_text(encoding="utf-8"))["presets"]
)
"""The preset dates a release ships; the stored renderings pin these only, as the unreleased date's pages are built at
deploy."""
UNRELEASED_DATES = frozenset(name.rpartition("-")[2] for name in get_preset_names()) - RELEASED_DATES


def _released_blocks(markdown: str) -> str:
    """Return the parts of a preset page that name no unreleased date.

    The page is cut at its level 1 to 3 headings and at each date's paragraph of what it adds, with its list.
    """
    blocks = re.split(r"(?m)^(?=#{1,3} |The \d{8} presets keep )", markdown)
    return "".join(block for block in blocks if UNRELEASED_DATES.isdisjoint(re.findall(r"\b\d{8}\b", block)))


def _released_presets_json(text: str) -> str:
    """Return the JSON preset metadata of the presets of released dates."""
    presets = [preset for preset in json.loads(text) if preset["name"].rpartition("-")[2] in RELEASED_DATES]
    return json.dumps(presets, indent=2, ensure_ascii=False) + "\n"


@pytest.mark.skipif(BLACK_LT_233, reason="Installed black doesn't support the Python 3.12 quick-start target")
def test_build_preset_docs_check_is_up_to_date() -> None:
    """Generated preset names and quick-start examples are committed; the preset pages are built at deploy."""
    subprocess.run([sys.executable, str(SCRIPT), "--check"], check=True)


def test_build_preset_docs_pages_writes_the_preset_pages(tmp_path: Path) -> None:
    """The pages mode writes the two preset pages into the directory it names, as the renderers render them."""
    subprocess.run([sys.executable, str(SCRIPT), "--pages", str(tmp_path / "docs")], check=True)
    written = {path.name: path.read_text(encoding="utf-8") for path in sorted((tmp_path / "docs").iterdir())}
    rendered = {"preset-history.md": render_preset_history(), "presets.md": render_presets("markdown")}
    assert_output(f"written {sorted(written)}, as rendered: {written == rendered}\n")


def test_build_preset_docs_json_format() -> None:
    """The docs script can print preset metadata as JSON; the stored output keeps the presets of released dates."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert_output(_released_presets_json(result.stdout))


def test_build_preset_docs_check_help_mentions_all_generated_targets() -> None:
    """The --check help text covers all generated preset docs outputs."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert_output(result.stdout)


def test_preset_metadata_renderers() -> None:
    """Preset metadata renderers expose the preset reference; the stored outputs keep what names released dates only.

    The pages of the unreleased date are built at deploy, so the sections and presets that name it are left out here;
    the stamp of a release adds its date.
    """
    assert_output(_released_blocks(render_presets("markdown")), name="presets_markdown")
    assert_output(_released_blocks(render_preset_history()), name="preset_history_markdown")
    assert_output(_released_presets_json(render_presets("json")), name="presets_json")
    assert_output(
        f"{get_latest_preset_name()}\n{get_preset_infos()[0].name.value}\n",
        name="preset_names",
    )


def test_readme_supported_sections_are_rendered_from_enums() -> None:
    """README supported input/output lists are synchronized from public enums."""
    assert_output(
        build_preset_docs._render_readme_supported_sections(),
    )


def test_marked_section_replacement_uses_end_marker_after_begin() -> None:
    """Marked section replacement ignores matching text before the begin marker."""
    markdown = "Earlier literal <!-- END -->\n<!-- BEGIN -->\nstale\n<!-- END -->\nAfter\n"

    assert_output(
        build_preset_docs._replace_marked_section(markdown, "<!-- BEGIN -->", "<!-- END -->", "Generated"),
    )


def test_marked_section_replacement_indents_as_the_begin_marker() -> None:
    """A marked section inside a content tab keeps the generated block and its end marker in the tab."""
    markdown = '=== "Python"\n\n    <!-- BEGIN -->\n    stale\n    <!-- END -->\n\nAfter\n'

    assert_output(
        build_preset_docs._replace_marked_section(
            markdown, "<!-- BEGIN -->", "<!-- END -->", "```python\nx = 1\n\ny = 2\n```\n"
        ),
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

    assert_output(output)


def test_preset_api_refuses_an_unknown_format_and_an_unknown_field() -> None:
    """The preset API names the format or the config field it does not know."""
    unknown_format: object = "xml"
    unknown_field: dict[str, object] = {"no_such_field": True}
    with pytest.raises(PresetError, match="Unsupported preset docs format: 'xml'"):
        render_presets(unknown_format)
    with pytest.raises(PresetError, match="Preset field 'no_such_field' is not a BaseGenerateConfig field"):
        PresetConfig(**unknown_field)


@pytest.mark.parametrize(
    ("build", "message"),
    [
        pytest.param(
            lambda: PresetConfig(extra_fields="bogus"),
            "Unsupported extra_fields preset value: 'bogus'",
            id="extra-fields-value",
        ),
        pytest.param(
            lambda: PresetConfig(wrap_string_literal=None),
            "Preset field 'wrap_string_literal' cannot be rendered as a preset CLI option",
            id="value-without-a-rendering",
        ),
        pytest.param(
            lambda: PresetOptionGroup(title="order", config=PresetConfig(keep_model_order=True), description=""),
            "has no --no- form to turn it off",
            id="enabled-option-without-a-negative",
        ),
        pytest.param(
            lambda: (
                PresetOptionGroup(title="dot", config=PresetConfig(treat_dot_as_module=True), description="").options
            ),
            "Preset field 'treat_dot_as_module' does not map to a documented CLI option",
            id="field-without-an-option",
        ),
        pytest.param(
            lambda: (
                PresetOptionGroup(title="order", config=PresetConfig(keep_model_order=False), description="").options
            ),
            "Preset field 'keep_model_order' does not map to a documented negative CLI option",
            id="disabled-option-without-a-negative",
        ),
    ],
)
def test_preset_config_refuses_what_the_command_line_cannot_write(build: Callable[[], object], message: str) -> None:
    """A config or option group that no CLI option spells is refused, naming the field."""
    with pytest.raises(PresetError, match=re.escape(message)):
        build()


@pytest.mark.allow_direct_assert("states which contexts a preset option group applies to, which writes no output")
def test_preset_option_group_applies_to_its_input_file_types() -> None:
    """A group that names input file types applies to those only."""
    group = PresetOptionGroup(
        title="openapi only",
        config=PresetConfig(),
        description="",
        input_file_types=frozenset({InputFileType.OpenAPI}),
    )
    context = PresetContext(
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        target_python_version=PythonVersion.PY_310,
    )

    assert group.applies_to(context) is False
    assert group.applies_to(PresetContext(InputFileType.OpenAPI, context.output_model_type, PythonVersion.PY_310))


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

    assert_output("\n".join((*lines, "")))


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
