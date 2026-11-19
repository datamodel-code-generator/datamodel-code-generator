"""The static tables of the package keep the invariants it checked once as it was imported."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from datamodel_code_generator._requirements import REQUIREMENTS
from datamodel_code_generator.cli_options import CLI_OPTION_META, OPTION_TOPIC_ALLOWED_GROUPS
from datamodel_code_generator.enums import DataModelType, InputFileType
from datamodel_code_generator.preset import PresetContext, PresetInfo, PresetName, get_preset_infos

SOURCE_PATH = Path(__file__).parents[1] / "src" / "datamodel_code_generator"
REQUIREMENT_PATH_CALL = re.compile(r"""\b(form|backport)_path\(\s*["']([^"']+)["']\s*\)""")


def _preset_name_problems() -> list[str]:
    infos = get_preset_infos()
    problems = [] if {info.name for info in infos} == set(PresetName) else ["a preset name has no info"]
    if len(infos) != len(PresetName):
        problems.append("a preset is defined more than once")
    return [*problems, *(f"{name.value} has no date" for name in PresetName if not re.search(r"-\d+$", name.value))]


def _preset_group_problems(info: PresetInfo) -> list[str]:
    problems = []
    if info.target_python_version.value.replace(".", "") not in info.name.value:
        problems.append(f"{info.name.value} lacks its Python target")
    output_model_groups: dict[tuple[DataModelType, str], str] = {}
    for group in info.option_groups:
        if not group.config.items():
            problems.append(f"{info.name.value}: {group.title} defines no config fields")
        for output_model_type in group.output_model_types:
            for item in group.config.items():
                key = (output_model_type, item.field_name)
                if key in output_model_groups:
                    problems.append(f"{info.name.value} sets {key} in {output_model_groups[key]} and {group.title}")
                output_model_groups[key] = group.title
    return problems


def _preset_resolution_problems(
    info: PresetInfo, output_model_type: DataModelType, input_file_type: InputFileType
) -> list[str]:
    context = PresetContext(input_file_type, output_model_type, info.target_python_version)
    applicable = [group for group in info.option_groups if group.applies_to(context)]
    where = f"{info.name.value} for {output_model_type.value}"
    problems = [] if any(group.output_model_types for group in applicable) else [f"{where} has no output model group"]
    output_model_fields = {
        item.field_name for group in applicable if group.output_model_types for item in group.config.items()
    }
    values: dict[str, object] = {}
    items = [
        item
        for group in applicable
        for item in group.config.items()
        if group.output_model_types or item.field_name not in output_model_fields
    ]
    conflicting = [item for item in items if values.setdefault(item.field_name, item.value) != item.value]
    problems.extend(f"{where} configures {item.field_name} with two values" for item in conflicting)
    return problems


def _option_topic_problems() -> list[str]:
    return [
        f"{option}: group {meta.group.value} is not allowed for topic {meta.topic.value}"
        for option, meta in CLI_OPTION_META.items()
        if meta.topic is not None
        and meta.group is not None
        and meta.group not in OPTION_TOPIC_ALLOWED_GROUPS.get(meta.topic, frozenset())
    ]


def _requirement_path_problems() -> list[str]:
    asked = {
        (kind, construct)
        for path in SOURCE_PATH.rglob("*.py")
        for kind, construct in REQUIREMENT_PATH_CALL.findall(path.read_text(encoding="utf-8"))
    }
    problems = [] if asked else ["no construct asks for an import path"]
    for kind, construct in sorted(asked):
        if (REQUIREMENTS[construct].form if kind == "form" else REQUIREMENTS[construct].backport) is None:
            problems.append(f"the requirement row {construct} names no such import")
    return problems


@pytest.mark.allow_direct_assert("states invariants of static tables that no generation output shows")
def test_static_tables_keep_their_invariants() -> None:
    """Check the preset tables, the option topics and groups, and the requirement rows the package reads at import."""
    problems = _preset_name_problems()
    for info in get_preset_infos():
        problems.extend(_preset_group_problems(info))
        for output_model_type in DataModelType:
            for input_file_type in InputFileType:
                problems.extend(_preset_resolution_problems(info, output_model_type, input_file_type))
    problems.extend(_option_topic_problems())
    problems.extend(_requirement_path_problems())

    assert problems == []
