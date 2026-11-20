"""Options whose value implies the value of another option, and the relations between options, free of pydantic.

Every stage that applies option values applies what they imply: the command line to the arguments it merges (merging
copies only the given fields), the config models when they validate, ``generate()`` and the command line to the
settings a config leaves unset, and a preset to the updates it makes; the CLI reference lists the same relations.
The settings stage and the preset read every source option of the table. Each config validator applies one row:
``BaseGenerateConfig`` the ``use_type_alias_type`` row (a frozen ``PresetConfig`` sets the other source options) and
the command line's ``Config`` the ``use_annotated`` row (row ``validator-stage-rows``). ``get_data_model_types`` reads
the ``use_type_alias_type`` row through ``implied_options`` for direct callers of the public function. Where an
implied option was given off and is on, the resolution of the run's options warns
(``_option_stages.py:_run_option_stages``, through ``enabled_implied_options``): an enabled option of the table
replaced the value, or the command line's validator applied its row to the pyproject.toml values before an argument
turned the option off, which the stage ``implied-model`` undoes under ``--read-implied-options-from-merged-values``.

``OPTION_RELATIONS`` is every pair of options that a run refuses or warns of together, and what the CLI reference
documents for it (``cli_options.CLI_OPTION_META``). ``check_option_relations`` runs the rows of one stage in table
order, each stage where it sees its values:

- ``base-validator``: the model validators of ``BaseGenerateConfig``, one per row (``check_option_relation``), so
  every config, with the values it is validated with: on the command line the pyproject.toml values alone, then
  the arguments alone, never the merge.
- ``cli-validator``: the model validator of the command line's ``Config``, after its ``--output-datetime-class`` and
  ``--alias-generator`` checks, with the same two inputs and the table ``load_pyproject_config`` reads.
- ``cli-after-preset``: the command line's config after its ``implied-model`` stage, the merged values with the preset
  and the implied settings applied (``__main__.py:_resolve_cli_config``).
- ``run-options``: a command's model or target run, its config resolved and the record of where its values came from,
  after the run's other checks and warnings (``__main__.py:_apply_model_run_options``).
- ``sentinel``: the stage of ``generate()`` that gives ``--use-missing-sentinel`` its target pydantic version, which
  every command runs too (``_option_stages.py:_missing_sentinel_updates``).
- ``parser-options``: ``generate()`` as it prepares the options of its parsers
  (``_generation.py:_prepare_parser_common_options``).

``generate()`` checks the rows of ``base-validator``, ``sentinel`` and ``parser-options`` alone (row
``cli-only-refusals``). The options and the input kinds a server or client target requires
(``TARGET_REQUIRED_OPTIONS``, ``TARGET_INPUTS``) are refused by ``base_config.py:_target_options_error`` and stated
by the CLI reference.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias, cast

from datamodel_code_generator._requirements import REQUIREMENTS, python_target_meets
from datamodel_code_generator._shared_types import Error
from datamodel_code_generator.enums import (
    _PYDANTIC_MODEL_TYPES,
    AliasGenerator,
    AllExportsScope,
    DataModelType,
    InputFileType,
    ReuseScope,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from datamodel_code_generator._format_types import DatetimeClassType

IMPLIED_OPTIONS: Final[dict[str, str]] = {
    "use_annotated": "field_constraints",
    "use_type_alias_type": "use_type_alias",
    "intersect_combined_schema_keywords": "intersect_allof_keywords",
}
"""The option each option turns on when it is enabled, over any value given for that option."""

RelationStage: TypeAlias = Literal[
    "base-validator", "cli-validator", "cli-after-preset", "run-options", "sentinel", "parser-options"
]
_KW_ONLY = REQUIREMENTS["python.kw_only_dataclass"].minimum
_STRENUM = REQUIREMENTS["python.strenum"].minimum
TARGET_REQUIRED_OPTIONS: Final[tuple[str, ...]] = ("output", "package", "model_package")
"""The options every server or client target requires, after the prefix of its options."""
TARGET_INPUTS: Final[dict[str, tuple[InputFileType, ...]]] = {
    "generate_server": (InputFileType.OpenAPI,),
    "generate_client": (InputFileType.OpenAPI, InputFileType.AsyncAPI),
}
"""The input kinds each target reads operations from, by the field that selects it."""


def implied_options(values: Mapping[str, object]) -> dict[str, bool]:
    """Return the options that the enabled options of ``values`` turn on, each True, in the order of the table."""
    return {implied: True for option, implied in IMPLIED_OPTIONS.items() if values.get(option)}


def implied_option_sources(config: object) -> dict[str, object]:
    """Return the value of every source option of ``IMPLIED_OPTIONS`` on ``config``, in the order of the table."""
    return {option: getattr(config, option) for option in IMPLIED_OPTIONS}


def enabled_implied_options(config: object) -> dict[str, str]:
    """Return each implied option that is on in ``config``, with the option that turns it on, in table order."""
    return {implied: option for option, implied in IMPLIED_OPTIONS.items() if getattr(config, implied)}


def enable_implied_options(config: object, *options: str) -> None:
    """Turn on, on ``config``, the options that its enabled ``options`` imply."""
    for name, value in implied_options({option: getattr(config, option) for option in options}).items():
        setattr(config, name, value)


@dataclass(frozen=True)
class OptionRelation:
    """A relation of two options, as the CLI reference states it and, with a ``stage``, as a run checks it.

    ``pair`` reads as the reference does; its ``message``, ``value`` and ``when`` are the reference's, and a conflict
    is stated from the other option too, with ``other_message``. Where ``applies`` holds for the config of the stage
    and the run's record, the run shows ``text`` (else the message), formatted with the config: ``error`` raises the
    package's ``Error``, ``value-error`` a ``ValueError`` (pydantic's invalid configuration), ``refuse`` prints it and
    refuses the run, ``warn`` prints it and goes on.
    """

    pair: tuple[str, Literal["requires", "conflicts"], str]
    message: str | None = None
    value: Any = None
    when: Any = None
    other_message: str | None = None
    stage: RelationStage | None = None
    applies: Callable[[Any, Any], object] | None = None
    action: Literal["error", "value-error", "refuse", "warn"] = "error"
    text: str | None = None
    documented: bool = True


def _before_strenum(config: Any) -> bool:
    """Return whether the target Python version of a config predates ``StrEnum``."""
    return not python_target_meets("python.strenum", config.target_python_version.version_key)


OPTION_RELATIONS: Final[tuple[OptionRelation, ...]] = (
    OptionRelation(
        ("--update-lock", "conflicts", "--locked"),
        stage="base-validator",
        applies=lambda config, _: config.update_lock and config.locked,
        action="value-error",
        text="--update-lock and --locked cannot be used together",
    ),
    OptionRelation(
        ("--generate-server", "conflicts", "--generate-client"),
        "`--generate-server` can not be used with `--generate-client`.",
        other_message="`--generate-client` can not be used with `--generate-server`.",
        stage="base-validator",
        applies=lambda config, _: config.generate_server is not None and config.generate_client is not None,
        text="--generate-server and --generate-client cannot be used together",
    ),
    OptionRelation(
        ("--custom-file-header-path", "conflicts", "--custom-file-header"),
        "`--custom-file-header-path` can not be used with `--custom-file-header`.",
        other_message="`--custom-file-header` can not be used with `--custom-file-header-path`.",
        stage="cli-validator",
        applies=lambda config, _: config.custom_file_header is not None and config.custom_file_header_path is not None,
    ),
    OptionRelation(
        ("--keyword-only", "requires", "--target-python-version"),
        f"`--keyword-only` requires `--target-python-version` {_KW_ONLY} or higher for dataclasses.",
        f"{_KW_ONLY}+",
    ),
    OptionRelation(
        ("--use-validate-default-for-root-model-type-aliases", "requires", "--use-root-model-type-alias"),
        "`--use-validate-default-for-root-model-type-aliases` acts only with `--use-root-model-type-alias`.",
        value=True,
    ),
    OptionRelation(
        ("--all-exports-collision-strategy", "requires", "--all-exports-scope"),
        "`--all-exports-collision-strategy` can only be used with `--all-exports-scope=recursive`.",
        AllExportsScope.Recursive.value,
        stage="cli-validator",
        applies=lambda config, _: (
            config.all_exports_collision_strategy is not None and config.all_exports_scope != AllExportsScope.Recursive
        ),
    ),
    OptionRelation(
        ("--original-field-name-delimiter", "requires", "--snake-case-field"),
        "`--original-field-name-delimiter` can not be used without `--snake-case-field`.",
        value=True,
        stage="cli-after-preset",
        applies=lambda config, _: config.original_field_name_delimiter is not None and not config.snake_case_field,
    ),
    OptionRelation(
        ("--reuse-scope", "requires", "--reuse-model"),
        "`--reuse-scope=tree` has no effect without `--reuse-model`.",
        value=True,
        when=ReuseScope.Tree.value,
        stage="run-options",
        applies=lambda config, _: config.reuse_scope == ReuseScope.Tree and not config.reuse_model,
        action="warn",
        text="Warning: --reuse-scope=tree has no effect without --reuse-model",
    ),
    OptionRelation(
        ("--collapse-root-models-name-strategy", "requires", "--collapse-root-models"),
        "`--collapse-root-models-name-strategy` requires `--collapse-root-models`.",
        value=True,
        stage="run-options",
        applies=lambda config, _: config.collapse_root_models_name_strategy and not config.collapse_root_models,
        action="refuse",
        text="Error: --collapse-root-models-name-strategy requires --collapse-root-models",
    ),
    OptionRelation(
        ("--open-enum-base-class-map", "requires", "--open-enums"),
        "`--open-enum-base-class-map` requires `--open-enums extensible` or `--open-enums all`.",
        stage="run-options",
        applies=lambda config, _: (
            config.open_enum_base_class_map and (config.open_enums is None or config.open_enums.value == "none")
        ),
        action="refuse",
        text="Error: --open-enum-base-class-map requires --open-enums extensible or --open-enums all",
    ),
    OptionRelation(
        ("--open-enum-base-class-map", "requires", "--target-python-version"),
        stage="run-options",
        applies=lambda config, _: (
            (config.open_enum_base_class_map or {}).get("StrEnum") is not None and _before_strenum(config)
        ),
        action="refuse",
        text="Error: the StrEnum entry of --open-enum-base-class-map requires --target-python-version 3.11 or later.\n"
        "Current target version: {config.target_python_version.value}\n"
        "StrEnum is only available in Python 3.11+; string enums use the Enum entry there.",
        documented=False,
    ),
    # Row `specialized-enum-by-value`: the refusal reads the value the option was given, not whether it was given, so
    # a pyproject.toml value that is not the TOML boolean `true` is not refused.
    OptionRelation(
        ("--use-specialized-enum", "requires", "--target-python-version"),
        f"`--use-specialized-enum` requires `--target-python-version` {_STRENUM} or higher.",
        f"{_STRENUM}+",
        stage="run-options",
        applies=lambda config, record: (
            config.use_specialized_enum
            and record.given_value("use_specialized_enum") is True
            and _before_strenum(config)
        ),
        action="refuse",
        text="Error: --use-specialized-enum requires --target-python-version 3.11 or later.\n"
        "Current target version: {config.target_python_version.value}\n"
        "StrEnum is only available in Python 3.11+.",
    ),
    OptionRelation(
        ("--use-missing-sentinel", "requires", "--output-model-type"),
        "`--use-missing-sentinel` is only supported for `--output-model-type pydantic_v2.BaseModel` "
        "or `pydantic_v2.dataclass`.",
        stage="sentinel",
        applies=lambda config, _: config.use_missing_sentinel and config.output_model_type not in _PYDANTIC_MODEL_TYPES,
        text="`--use-missing-sentinel` is only supported for `--output-model-type pydantic_v2.BaseModel` "
        "or `pydantic_v2.dataclass`",
    ),
    OptionRelation(
        ("--union-mode", "requires", "--output-model-type"),
        "`--union-mode` is only supported for `--output-model-type pydantic_v2.BaseModel`.",
        DataModelType.PydanticV2BaseModel.value,
        stage="parser-options",
        applies=lambda config, _: (
            config.union_mode is not None and config.output_model_type != DataModelType.PydanticV2BaseModel
        ),
        text="union_mode is only supported for pydantic_v2.BaseModel",
    ),
)
"""The relations between options, each stated once for the CLI reference and for the run that checks it."""


def _check_relation(row: OptionRelation, config: Any, record: Any) -> bool:
    """Act on one row where it applies to the config and the record; return whether it printed its refusal."""
    if row.applies is None or not row.applies(config, record):
        return False
    text = cast("str", row.text or row.message).format(config=config)
    match row.action:
        case "value-error":
            raise ValueError(text)
        case "error":
            raise Error(text)
    print(text, file=sys.stderr)  # noqa: T201
    return row.action == "refuse"


def check_option_relations(stage: RelationStage, config: Any, record: Any = None) -> bool:
    """Check the rows of a stage on its config in table order; return whether one printed its refusal.

    A ``warn`` row prints its text and the check goes on; the first row that refuses ends it.
    """
    return any(_check_relation(row, config, record) for row in OPTION_RELATIONS if row.stage == stage)


def check_option_relation(pair: tuple[str, str, str], config: Any) -> None:
    """Check the one row stated by ``pair`` on a config.

    The released validators of ``BaseGenerateConfig`` (``validate_remote_lock_mode``, ``validate_target_selection``)
    keep their names: each checks its row of the stage ``base-validator``, in the order the table lists them.
    """
    _check_relation(next(row for row in OPTION_RELATIONS if row.pair == pair), config, None)


def _validate_output_datetime_class(
    output_model_type: DataModelType, output_datetime_class: DatetimeClassType | None
) -> None:
    if output_datetime_class is None:
        return

    from datamodel_code_generator._format_types import DatetimeClassType  # noqa: PLC0415

    if output_datetime_class is DatetimeClassType.Datetime:
        return
    if output_model_type in {DataModelType.DataclassesDataclass, DataModelType.TypingTypedDict}:
        msg = f'`--output-datetime-class` only allows "datetime" for `--output-model-type` {output_model_type.value}'
        raise Error(msg)


def _validate_alias_generator(output_model_type: DataModelType, alias_generator: AliasGenerator | None) -> None:
    if alias_generator is None:
        return
    if output_model_type is DataModelType.PydanticV2BaseModel:
        return
    msg = "`--alias-generator` is only supported for `--output-model-type pydantic_v2.BaseModel`"
    raise Error(msg)


__all__ = [
    "IMPLIED_OPTIONS",
    "OPTION_RELATIONS",
    "TARGET_INPUTS",
    "TARGET_REQUIRED_OPTIONS",
    "OptionRelation",
    "check_option_relation",
    "check_option_relations",
    "enable_implied_options",
    "enabled_implied_options",
    "implied_option_sources",
    "implied_options",
]
