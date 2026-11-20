"""The option stages of a run (``_run_option_stages``), the one resolution every entry point runs.

Each stage gives a config the values one source decides: a preset, a server or client target,
``--use-missing-sentinel`` and the options other options imply.
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Final,
    Literal,
    TypeAlias,
    TypeVar,
)

from datamodel_code_generator._shared_types import Error
from datamodel_code_generator._warning_control import warn_as_public
from datamodel_code_generator.enums import (
    DataModelType,
    TargetPydanticVersion,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping
    from collections.abc import Set as AbstractSet

    from datamodel_code_generator._run_record import RunOptions
    from datamodel_code_generator.base_config import BaseGenerateConfig
    from datamodel_code_generator.config import (
        GenerateConfig,
    )
    from datamodel_code_generator.preset import PresetName, ResolvedPresetConfig


if TYPE_CHECKING:
    _OptionStage: TypeAlias = Literal["preset", "implied-target", "sentinel", "implied-model"]

_RunConfigT = TypeVar("_RunConfigT", bound="BaseGenerateConfig")


def _resolve_preset(
    preset: PresetName | str, config: BaseGenerateConfig, given: AbstractSet[str]
) -> ResolvedPresetConfig:
    """Resolve a preset for a config, leaving out the fields that are `given`; one that does not fit is an `Error`.

    The one resolution of a preset: the command line, `generate()`, a server or client target, dynamic generation and
    the recommender read it, so each shows the same refusal.
    """
    from datamodel_code_generator.preset import PresetError, _resolve_config_preset  # noqa: PLC0415

    try:
        return _resolve_config_preset(preset, config, given)
    except PresetError as e:
        raise Error(str(e)) from e


def _preset_updates(config: BaseGenerateConfig, record: RunOptions) -> dict[str, Any]:
    """Return the values the preset of a config gives the fields that are not explicit to it."""
    return {} if config.preset is None else _resolve_preset(config.preset, config, record.preset_explicit).updates()


def _implied_target_updates(config: BaseGenerateConfig, record: RunOptions) -> dict[str, Any]:
    """Return the values a server or client target gives the settings nothing set, the preset included.

    It reads the fields that have a value from a source or from an earlier stage, not only the given ones.
    """
    from datamodel_code_generator.base_config import implied_target_settings  # noqa: PLC0415

    return implied_target_settings(config, record.set_fields)


def _missing_sentinel_updates(config: BaseGenerateConfig, _record: RunOptions) -> dict[str, Any]:
    """Return the target pydantic version `--use-missing-sentinel` needs where none is set, refusing an older one."""
    if not config.use_missing_sentinel:
        return {}

    from datamodel_code_generator._option_rules import check_option_relations  # noqa: PLC0415

    check_option_relations("sentinel", config)
    from datamodel_code_generator._requirements import REQUIREMENTS, pydantic_target_meets  # noqa: PLC0415

    minimum = TargetPydanticVersion(REQUIREMENTS["pydantic.experimental_missing_sentinel"].minimum)
    match target_version := config.target_pydantic_version:
        case None:
            return {"target_pydantic_version": minimum}
        case _ if pydantic_target_meets("pydantic.experimental_missing_sentinel", target_version):
            return {}
        case _:
            target_version_value = (
                target_version.value if isinstance(target_version, TargetPydanticVersion) else target_version
            )
            from datamodel_code_generator.python_literal import stable_repr  # noqa: PLC0415

            msg = (
                f"`--use-missing-sentinel` requires `--target-pydantic-version {minimum.value}` or later; got "
                f"{stable_repr(target_version_value)}"
            )
            raise Error(msg)


def _implied_model_updates(config: BaseGenerateConfig, record: RunOptions) -> dict[str, Any]:
    """Return the constraint settings the other settings of a config decide, read with the given fields as set.

    Under `--read-implied-options-from-merged-values` an option that was turned off and that only an option now off
    turned on (`_replaced_options`) is off again, as the merged values say.
    """
    from datamodel_code_generator.base_config import implied_model_settings  # noqa: PLC0415

    implied = implied_model_settings(config, record.given)
    if not config.read_implied_options_from_merged_values:
        return implied
    replaced = _replaced_options(config, record, record.staged_by("preset"))
    return {**{name: False for name, option in replaced.items() if not getattr(config, option)}, **implied}


_OPTION_STAGES: Final[dict[_OptionStage, Callable[[BaseGenerateConfig, RunOptions], dict[str, Any]]]] = {
    "preset": _preset_updates,
    "implied-target": _implied_target_updates,
    "sentinel": _missing_sentinel_updates,
    "implied-model": _implied_model_updates,
}


def _replaced_options(config: BaseGenerateConfig, record: RunOptions, preset_gave: Collection[str]) -> dict[str, str]:
    """Return each option of `IMPLIED_OPTIONS` that was turned off and is on, with the option that turns it on.

    An option was turned off where a source of the run gave it False and the preset did not replace that value: a
    preset that an argument selects replaces the values of pyproject.toml (row `preset-over-pyproject`), so a
    pyproject.toml value of an option the preset gave a value (`preset_gave`: the fields the `preset` stage of the
    call gave, its own items and what they imply) is the preset's to replace. The option that turns it on is on, as
    the table turns its option on over any value given, or off: then the command line's config validator turned it
    on from the pyproject.toml values alone, before an argument turned that option off (row `validator-stage-rows`).
    """
    from datamodel_code_generator._option_rules import enabled_implied_options  # noqa: PLC0415

    return {
        implied: option
        for implied, option in enabled_implied_options(config).items()
        if record.given_value(implied) is False and (implied in record.preset_explicit or implied not in preset_gave)
    }


def _warn_of_replaced_options(config: BaseGenerateConfig, record: RunOptions, preset_gave: Collection[str]) -> None:
    """Warn of each option that was turned off and stays on (`_replaced_options`).

    Where the option that turns it on is on, the warning names that option, with where it came from (the preset that
    set it, the output model type it was set for, or that it was given as well), and how to turn both off: the
    option's argument, or its key where pyproject.toml turns it on (an argument given as well is named by the next
    run). Where that option is off, it names the pyproject.toml value that turned it on and the argument that turned
    that one off, and how the option is off: the key, or `--read-implied-options-from-merged-values`.
    """
    replaced = _replaced_options(config, record, preset_gave)
    if not replaced:
        return
    from datamodel_code_generator._option_names import option_flag  # noqa: PLC0415
    from datamodel_code_generator.cli_options import _values_arguments  # noqa: PLC0415

    for implied, option in replaced.items():
        flag = option_flag(option)
        key = option.replace("_", "-")
        off = _values_arguments([(option, False)])
        if not getattr(config, option):
            cause = (
                f"{key} = true in pyproject.toml turns it on, which {off[0]} does not undo; {key} = false in "
                f"pyproject.toml or {option_flag('read_implied_options_from_merged_values')} turns it off"
            )
        else:
            remedy = f"{off[0]} turns both off" if off else f"both are off without {flag}"
            match record.staged.get(option):
                case "preset":
                    origin = f", set by --preset {config.preset},"
                case "implied-model":
                    origin = f", set for --output-model-type {DataModelType(config.output_model_type).value},"
                case _:
                    origin = ", given as well,"
                    if record.pyproject.get(option):
                        remedy = f"{key} = false in pyproject.toml turns both off"
            cause = f"{flag}{origin} turns it on; {remedy}"
        warn_as_public(f"{option_flag(implied)} was turned off but stays on: {cause}", UserWarning, stacklevel=2)


def _run_option_stages(
    config: _RunConfigT,
    record: RunOptions,
    stages: Collection[_OptionStage],
    checks: Mapping[_OptionStage, Callable[[_RunConfigT, Mapping[str, Any]], object]] | None = None,
) -> tuple[_RunConfigT, RunOptions]:
    """Return a config with the values its option stages give it, and the record of the run with what they gave.

    The one resolution of every entry point. The stages run in the order of `_OPTION_STAGES`, each on the config the
    stages before it left, and an entry point names the ones it runs. What an entry point knows about where its
    values came from is the record it brings (`_run_record.py:RunOptions`), which the stages read instead of asking
    who calls: the preset leaves the fields explicit to it alone, the implied target settings leave every field a
    source or an earlier stage gave a value, and the implied model settings read the given fields as they were before
    the preset (row `implied-reads-given-before-preset`). The values of a stage that differ from the config's are
    applied to a copy, so a config that already has them stays the same object, and the record that comes back names
    every field the stage gave a value: nothing reads the fields a config has set after it came in. A check of the
    entry point runs after its stage, with the config so far and the values that stage gave, so that what a user
    sees first stays where the entry point shows it.

    A call that ran `implied-model`, the stage that applies every row of `IMPLIED_OPTIONS` to the final values, ends
    with a warning for each option that was turned off and stays on (`_warn_of_replaced_options`, which reads the
    fields the `preset` stage of the call gave), after the checks, so a resolution that is refused reports none. A
    run shows it once: a later resolution of the same config reads the resolved value as the given one, and a call
    that runs other stages alone (the command line's late `implied-target`, the preset dynamic generation reads the
    formatters from) reports nothing.

    The entry points, each with its record, its stages and its own checks in order:

    - `__main__.py:_resolve_cli_config` (a command, each batch job): the arguments and the pyproject.toml values, as
      they are before validation; `preset`, `implied-model`, then the relation rows that only the merged values
      decide (`_option_rules.py`, stage `cli-after-preset`). Later, where a target is selected (a run after its
      option checks, `--recommend-options` after the target's option check): `implied-target`, with the record the
      config carries. No `sentinel` stage: the config
      goes to `generate()` without a preset, which runs it after the command's own refusals (row
      `cli-sentinel-in-generate`).
    - `_project_config.py:load_pyproject_config`: the pyproject.toml keys it keeps and its overrides; `implied-model`
      alone. The config it returns keeps its `preset`, which `generate()` resolves (row `loader-applies-no-preset`).
    - `_resolve_run_options`, which reads no record but the fields the config has set (row `given-is-fields-set`):
      `_prepare_generate_facade_config` (`generate()`, a trial of the recommender, the metadata run of dynamic
      generation): the facade's warnings; `preset`, `sentinel`, `implied-model`; the metadata path and the output
      encoding. `_api_generation.py:prepare_target` (a server or client target): the model output; every stage,
      with the Pydantic target of a sentinel and then the facade's warnings after `implied-target`; the metadata
      path and the output encoding; the target's checks, which read the resolved config.
      `dynamic.py:_without_default_formatting` (the formatters dynamic generation runs with): `preset` alone, so
      that no later refusal comes before the facade's warnings.
    """
    gave: dict[_OptionStage, Mapping[str, Any]] = {}
    for stage, values_of in _OPTION_STAGES.items():
        if stage not in stages:
            continue
        values = gave[stage] = values_of(config, record)
        if changed := {name: value for name, value in values.items() if getattr(config, name) != value}:
            config = config.model_copy(update=changed)
        record = record.after(stage, values)
        if checks is not None and (check := checks.get(stage)) is not None:
            check(config, values)
    if "implied-model" in stages:
        _warn_of_replaced_options(config, record, gave.get("preset", ()))
    return config, record


def _resolve_run_options(
    config: GenerateConfig,
    stages: Collection[_OptionStage],
    checks: Mapping[_OptionStage, Callable[[GenerateConfig, Mapping[str, Any]], None]] | None = None,
) -> GenerateConfig:
    """Return a config with the values its option stages give it (`_run_option_stages`).

    The fields the config has set are the given ones, with the values they have here (row `given-is-fields-set`). A
    record the config carries is not read, so a command's config is resolved here again, to the same values. A config
    that `load_pyproject_config()` returned gives no values: the loader marks as set every field its stage gave, also
    one that kept its value, so a value of that config does not say that its caller gave it.
    """
    from datamodel_code_generator._run_record import RunOptions  # noqa: PLC0415

    fields_set = (
        {name: getattr(config, name) for name in config.model_fields_set}
        if config._pyproject_path is None  # noqa: SLF001
        else dict.fromkeys(config.model_fields_set)
    )
    return _run_option_stages(config, RunOptions(fields_set=fields_set), stages, checks)[0]
