"""Recommendation rule data: loading, validation through the preset config model, and evaluation."""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass
from enum import Enum
from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from datamodel_code_generator._recommend._signals import (
    SignalName,
    normalize_name,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from collections.abc import Set as AbstractSet

    from datamodel_code_generator._recommend._signals import Signals

RULES_RESOURCE = "recommender_rules.json"
OptionValue: TypeAlias = bool | str | list[str]
Operator: TypeAlias = Literal[">=", ">", "<=", "<", "==", "!="]
Category: TypeAlias = Literal[
    "io",
    "target",
    "preset",
    "style",
    "runtime-behaviour",
    "formatting",
    "compat",
    "debug",
    "platform",
    "command",
    "pending",
]
OutputModelTypeName: TypeAlias = Literal[
    "pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"
]
RuleId: TypeAlias = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]+\.[a-z0-9-]+$")]
OptionalSection: TypeAlias = Literal["validation", "policy"]

_OPERATORS: dict[str, Callable[[float, float], bool]] = {
    ">=": operator.ge,
    ">": operator.gt,
    "<=": operator.le,
    "<": operator.lt,
    "==": operator.eq,
    "!=": operator.ne,
}
_WORDS = re.compile(r"[A-Za-z0-9]+")


class _Data(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SignalCondition(_Data):
    """Compare one signal count with a threshold."""

    signal: SignalName
    op: Operator
    value: float


class RatioCondition(_Data):
    """Compare the ratio of two signal counts with a threshold; a zero denominator never matches."""

    ratio: tuple[SignalName, SignalName]
    op: Operator
    value: float


class AllCondition(_Data):
    """Match when every nested condition matches."""

    all: tuple[Condition, ...]


class AnyCondition(_Data):
    """Match when at least one nested condition matches."""

    any: tuple[Condition, ...]


class NotCondition(_Data):
    """Match when the nested condition does not match."""

    not_: Condition = Field(alias="not")


Condition: TypeAlias = SignalCondition | RatioCondition | AllCondition | AnyCondition | NotCondition
AllCondition.model_rebuild()
AnyCondition.model_rebuild()
NotCondition.model_rebuild()


class AppliesTo(_Data):
    """Input and output model types a rule applies to; an empty list means all."""

    input_file_types: tuple[Literal["openapi", "jsonschema", "asyncapi"], ...] = ()
    output_model_types: tuple[OutputModelTypeName, ...] = ()


class Rule(_Data):
    """One recommendation: config values to use when a schema condition holds.

    An ``optional`` rule lists its values in that section of the report for the user to decide; they never join the
    command, the configuration, or the compared candidates.
    """

    id: RuleId
    decision: Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]+$")]
    optional: OptionalSection | None = None
    applies_to: AppliesTo = AppliesTo()
    when: Condition
    contested_when: Condition | None = None
    contested_reason: str = ""
    set: dict[str, Any] = Field(default_factory=dict)
    value_from: dict[str, Literal["pascal_case_input_stem"]] = Field(default_factory=dict)
    reason: str
    caveats: tuple[str, ...] = ()
    evidence: tuple[str, ...]


class RuleOption(_Data):
    """A registered option that rules decide."""

    rules: tuple[RuleId, ...] = Field(min_length=1)


class NotRecommendedOption(_Data):
    """A registered option the recommender never sets, with the reason."""

    not_recommended: Category
    reason: str
    follow_up: str | None = None
    release: str | None = None


class RulesData(_Data):
    """The recommender rule file."""

    format_version: Literal[1]
    rules_revision: str
    calibrated_presets: str
    families: tuple[Literal["standard", "practical"], ...] = Field(min_length=1)
    full_trial_max_models: int = Field(
        gt=0, description="The estimated model count up to which the command without a mode compares candidates."
    )
    rules: tuple[Rule, ...]
    options: dict[str, RuleOption | NotRecommendedOption]


@dataclass(frozen=True, slots=True)
class LoadedRules:
    """Rule data with each rule's values as pyproject.toml spells them."""

    data: RulesData
    values: Mapping[str, Mapping[str, OptionValue]]


@dataclass(frozen=True, slots=True)
class Decision:
    """A rule that fired, with the values it decides and whether the schema also argues against it."""

    rule: Rule
    values: Mapping[str, OptionValue]
    contested: bool

    @property
    def avoids(self) -> bool:
        """Return whether the rule only turns options off."""
        return all(value is False for value in self.values.values())


def toml_value(value: object) -> Any:
    """Return a config value as pyproject.toml spells it, for comparing rule values with a configuration."""
    if isinstance(value, Enum):
        return str(value.value)
    return [toml_value(item) for item in value] if isinstance(value, tuple | list) else value


@cache
def _option_strings() -> dict[str, tuple[str, ...]]:
    from datamodel_code_generator.arguments import arg_parser  # noqa: PLC0415

    return {action.dest: tuple(action.option_strings) for action in arg_parser._actions if action.option_strings}  # noqa: SLF001


def option_arguments(field: str, value: OptionValue) -> list[str] | None:
    """Return the CLI arguments that set a config field to a value, or None when no option can express it."""
    option = f"--{field.replace('_', '-')}"
    if value is True:
        return [option]
    if value is False:
        negative = f"--no-{field.replace('_', '-')}"
        return [negative] if negative in _option_strings().get(field, ()) else None
    return [option, *value] if isinstance(value, list) else [option, value]


def _rule_values(rule: Rule) -> dict[str, OptionValue]:
    """Return a rule's values as pyproject.toml spells them, validated through the model of where they go.

    Values the command applies on top of a preset are validated like preset values; an optional rule's values are
    only shown, so any generation option may be one.
    """
    if rule.optional is None:
        from datamodel_code_generator.preset import PresetConfig  # noqa: PLC0415

        return {item.field_name: item.pyproject_value for item in PresetConfig(**rule.set).items()}
    from datamodel_code_generator.config import GenerateConfig  # noqa: PLC0415

    validated = GenerateConfig.model_validate(rule.set)
    return {name: toml_value(getattr(validated, name)) for name in rule.set}


@cache
def load_rules() -> LoadedRules:
    """Load the shipped rule file, validating each rule's values through the preset or generation config model."""
    resource = files("datamodel_code_generator.resources").joinpath(RULES_RESOURCE)
    data = RulesData.model_validate_json(resource.read_text(encoding="utf-8"))
    return LoadedRules(data, {rule.id: _rule_values(rule) for rule in data.rules})


def matches(condition: Condition, counts: Mapping[str, int]) -> bool:
    """Evaluate a rule condition against signal counts."""
    if isinstance(condition, SignalCondition):
        return _OPERATORS[condition.op](counts[condition.signal], condition.value)
    if isinstance(condition, RatioCondition):
        numerator, denominator = (counts[name] for name in condition.ratio)
        return bool(denominator) and _OPERATORS[condition.op](numerator / denominator, condition.value)
    if isinstance(condition, AllCondition):
        return all(matches(item, counts) for item in condition.all)
    if isinstance(condition, AnyCondition):
        return any(matches(item, counts) for item in condition.any)
    return not matches(condition.not_, counts)


def pascal_case_input_stem(stem: str | None, taken: AbstractSet[str]) -> str | None:
    """Return a class name made of the words of an input file name, unless a named schema already has that name."""
    if not stem or not (words := _WORDS.findall(stem)):
        return None
    name = "".join(word[:1].upper() + word[1:] for word in words)
    name = name if name[0].isalpha() else f"Model{name}"
    return None if normalize_name(name) in taken else name


def format_text(template: str, signals: Signals) -> str:
    """Fill a rule text with signal counts and facts, writing none for an empty fact."""
    return template.format_map({**signals.counts, **{name: value or "none" for name, value in signals.facts.items()}})


def _applies(rule: Rule, input_file_type: str, output_model_type: str) -> bool:
    applies_to = rule.applies_to
    return (not applies_to.input_file_types or input_file_type in applies_to.input_file_types) and (
        not applies_to.output_model_types or output_model_type in applies_to.output_model_types
    )


def decide(
    rules: LoadedRules,
    signals: Signals,
    *,
    input_file_type: str,
    output_model_type: str,
    input_stem: str | None,
) -> list[Decision]:
    """Return one decision per decision group: the first of its rules that applies, fires, and has its values."""
    decided: set[str] = set()
    decisions: list[Decision] = []
    for rule in rules.data.rules:
        if (
            rule.decision in decided
            or not _applies(rule, input_file_type, output_model_type)
            or not matches(rule.when, signals.counts)
        ):
            continue
        if (name := pascal_case_input_stem(input_stem, signals.component_names) if rule.value_from else "") is None:
            continue
        decided.add(rule.decision)
        decisions.append(
            Decision(
                rule=rule,
                values={**rules.values[rule.id], **dict.fromkeys(rule.value_from, name)},
                contested=rule.contested_when is not None and matches(rule.contested_when, signals.counts),
            )
        )
    return decisions
