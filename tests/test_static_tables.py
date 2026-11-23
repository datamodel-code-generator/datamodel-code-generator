"""The static tables of the package keep the invariants it checked once as it was imported."""

from __future__ import annotations

import re
from collections import Counter
from itertools import pairwise

import pytest

from datamodel_code_generator.cli_options import CLI_OPTION_META, OPTION_TOPIC_ALLOWED_GROUPS, OPTIONS
from datamodel_code_generator.enums import DataModelType, InputFileType
from datamodel_code_generator.preset import PresetContext, PresetInfo, PresetName, get_preset_infos


def _preset_name_problems() -> list[str]:
    names = [info.name for info in get_preset_infos()]
    return [
        *(f"{name.value} has no info" for name in PresetName if name not in names),
        *(f"{name.value} is defined {count} times" for name, count in Counter(names).items() if count > 1),
        *(f"{name.value} has no date" for name in PresetName if not re.search(r"-\d+$", name.value)),
    ]


def _preset_join_problems() -> list[str]:
    titles = {
        info.name.value.rpartition("-")[2]: {group.title for group in info.option_groups}
        for info in get_preset_infos()
        if info.name.value.startswith("standard-py312-")
    }
    added = {date: titles[date] - titles.get(previous, set()) for previous, date in pairwise(["", *sorted(titles)])}
    joins = [(option, option.preset) for option in OPTIONS if option.preset is not None]
    return [
        *(
            f"{option.flags[0]} joins the presets of {join.date}, which do not add its group {join.title!r}"
            for option, join in joins
            if join.title not in added.get(join.date, set())
        ),
        *(
            f"{option.flags[0]} joins with the config {join.config}, which is not its own field {option.dest} alone"
            for option, join in joins
            if [*join.config] != [option.dest]
        ),
    ]


def _preset_group_problems(info: PresetInfo) -> list[str]:
    settings = Counter(
        (output_model_type, item.field_name)
        for group in info.option_groups
        for output_model_type in group.output_model_types or (None,)
        for item in group.config.items()
    )
    return [
        *(
            [f"{info.name.value} lacks its Python target"]
            if info.target_python_version.value.replace(".", "") not in info.name.value
            else []
        ),
        *(
            f"{info.name.value}: {group.title} defines no config fields"
            for group in info.option_groups
            if not group.config
        ),
        *(f"{info.name.value} sets {key} in {count} groups" for key, count in settings.items() if count > 1),
    ]


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


@pytest.mark.allow_direct_assert("states invariants of static tables that no generation output shows")
def test_static_tables_keep_their_invariants() -> None:
    """Check the preset tables and the option topics and groups."""
    problems = [*_preset_name_problems(), *_preset_join_problems()]
    for info in get_preset_infos():
        problems.extend(_preset_group_problems(info))
        for output_model_type in DataModelType:
            for input_file_type in InputFileType:
                problems.extend(_preset_resolution_problems(info, output_model_type, input_file_type))
    problems.extend(_option_topic_problems())

    assert problems == []
