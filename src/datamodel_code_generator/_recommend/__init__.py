"""Option recommendations: analyse a schema and recommend options on top of the latest dated preset.

The CLI calls ``recommend_options`` once with the configuration it resolved and the requested mode, then renders
the result with ``_report.render_text`` or ``_report.render_json``. Quick mode analyses the schema and applies the
rule data; it never generates code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast

from datamodel_code_generator import Error
from datamodel_code_generator._recommend._rules import (
    Decision,
    OptionValue,
    decide,
    load_rules,
    option_arguments,
    toml_value,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from datamodel_code_generator._recommend._signals import SchemaKind, Signals

RecommendMode: TypeAlias = Literal["quick"]
Family: TypeAlias = Literal["standard", "practical"]
InputSource: TypeAlias = Literal["file", "url", "stdin", "directory", "input-model"]
_ANALYSED_TYPES: dict[str, SchemaKind] = {"openapi": "openapi", "jsonschema": "jsonschema", "asyncapi": "asyncapi"}
_DEFAULT_TABLE = ("tool", "datamodel-codegen")
_SENSITIVE_FIELDS = frozenset({"http_headers", "http_query_parameters"})


@dataclass(frozen=True, slots=True)
class RecommendRequest:
    """The configuration the CLI resolved for one recommendation.

    ``explicit_fields`` are the fields the user set, counted the way ``--preset`` counts them: the command-line
    fields when the preset is on the command line, otherwise the pyproject.toml and command-line fields.
    ``cli_values`` are the command-line options as config values, keyed by field name.
    """

    config: Any
    explicit_fields: frozenset[str]
    cli_values: Mapping[str, Any]
    has_project_config: bool
    preset_on_command_line: bool
    read_stdin: Callable[[], str]
    table: tuple[str, ...] = _DEFAULT_TABLE
    selector: tuple[str, ...] = ()
    mode: RecommendMode = "quick"


@dataclass(frozen=True, slots=True)
class Option:
    """One rule outcome with the CLI arguments that express it."""

    decision: Decision
    values: Mapping[str, OptionValue]
    arguments: list[str]


@dataclass(frozen=True, slots=True)
class Disagreement:
    """A value the configuration keeps although a rule suggests another one."""

    field: str
    configured: Any
    recommended: OptionValue
    decision: Decision | None
    kind: Literal["explicit", "preset", "newer-preset"]


@dataclass(frozen=True, slots=True)
class Rejection:
    """A preset family the recommendation does not start from because a rule value cannot be expressed on it."""

    preset: str
    decision: Decision
    arguments: list[str]


@dataclass(slots=True)
class Recommendation:
    """Everything a recommendation reports, before rendering."""

    rules_revision: str
    calibrated_presets: str
    mode: RecommendMode
    source: InputSource
    input_file_type: str | None
    signals: Signals | None
    output_model_type: str
    target_python_version: str
    preset: str
    family: str
    preset_configured: bool
    covers: list[Option] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
    recommendations: list[Option] = field(default_factory=list)
    consider: list[Option] = field(default_factory=list)
    not_recommended: list[Option] = field(default_factory=list)
    disagreements: list[Disagreement] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    command_values: dict[str, Any] = field(default_factory=dict)
    command_selector: tuple[str, ...] = ()
    config_values: dict[str, Any] | None = None
    config_complete: bool = True
    table: tuple[str, ...] = _DEFAULT_TABLE


@dataclass(slots=True)
class _Plan:
    """The outcome of starting from one preset."""

    preset: str
    family: str
    baseline: dict[str, Any]
    delta: list[Option] = field(default_factory=list)
    covers: list[Option] = field(default_factory=list)
    consider: list[Option] = field(default_factory=list)
    not_recommended: list[Option] = field(default_factory=list)
    blocked: list[tuple[Decision, str, OptionValue]] = field(default_factory=list)


def _read_input(request: RecommendRequest) -> tuple[str | None, InputSource, str | None]:
    """Return the schema text, where it came from, and the file stem that names the root."""
    config = request.config
    if config.input_model:
        return None, "input-model", None
    if config.url is not None:
        from datamodel_code_generator.http import DEFAULT_HTTP_TIMEOUT, get_body  # noqa: PLC0415

        text = get_body(
            config.url.geturl(),
            config.http_headers,
            bool(config.http_ignore_tls),
            config.http_query_parameters,
            DEFAULT_HTTP_TIMEOUT if config.http_timeout is None else config.http_timeout,
            allow_private_network=bool(config.allow_private_network),
            http_backend=config.http_backend,
            encoding=config.encoding,
        )
        return text, "url", Path(config.url.path).stem
    if config.input is not None:
        path = Path(config.input)
        if path.is_dir():
            return None, "directory", None
        try:
            return path.read_text(encoding=config.encoding), "file", path.stem
        except (OSError, UnicodeError) as e:
            msg = f"Unable to read the input {path.name}: {getattr(e, 'strerror', None) or e}"
            raise Error(msg) from e
    return request.read_stdin(), "stdin", None


def _input_file_type(config: Any, text: str | None) -> str | None:
    """Return the configured input file type, or the detected one for auto."""
    from datamodel_code_generator import InputFileType, infer_input_type  # noqa: PLC0415

    if config.input_file_type != InputFileType.Auto:
        return config.input_file_type.value
    if text is None:
        return None
    return infer_input_type(text).value


def _measure(text: str, kind: SchemaKind) -> Signals:
    from datamodel_code_generator._recommend._signals import measure_signals  # noqa: PLC0415
    from datamodel_code_generator._source import load_data  # noqa: PLC0415
    from datamodel_code_generator.util import get_yaml_parse_errors  # noqa: PLC0415

    try:
        document = load_data(text)
    except (TypeError, ValueError, *get_yaml_parse_errors()) as e:
        msg = f"Unable to read the input as a {kind} document: {e}"
        raise Error(msg) from e
    return measure_signals(document, kind)


def _option(decision: Decision, values: Mapping[str, OptionValue]) -> Option:
    arguments = [argument for name, value in values.items() for argument in option_arguments(name, value) or ()]
    return Option(decision, dict(values), arguments)


def _avoided(decision: Decision) -> Option:
    """Return the options an avoiding rule argues against, as the arguments that would turn them on."""
    return Option(decision, dict(decision.values), [f"--{name.replace('_', '-')}" for name in decision.values])


def _preset_values(preset: str, request: RecommendRequest) -> dict[str, Any]:
    """Return the values a preset gives the fields the user left unset."""
    from datamodel_code_generator.preset import PresetContext, resolve_preset_config_updates  # noqa: PLC0415

    config = request.config
    resolved = resolve_preset_config_updates(
        preset,
        context=PresetContext(
            input_file_type=config.input_file_type,
            output_model_type=config.output_model_type,
            target_python_version=config.target_python_version,
        ),
        use_annotated=config.use_annotated,
        explicit_fields=set(request.explicit_fields),
    )
    return {item.field_name: item.pyproject_value for item in resolved.items}


def _plan(
    preset: str,
    family: str,
    decisions: Sequence[Decision],
    request: RecommendRequest,
    *,
    configured: bool,
) -> _Plan:
    """Compare the decisions with what a preset already gives the configuration."""
    config = request.config
    preset_values = {} if configured else _preset_values(preset, request)
    baseline = {
        name: preset_values[name] if name in preset_values else toml_value(getattr(config, name))
        for decision in decisions
        for name in decision.values
    }
    plan = _Plan(preset, family, baseline)
    for decision in decisions:
        changes = {
            name: value
            for name, value in decision.values.items()
            if name not in request.explicit_fields and baseline[name] != value
        }
        if decision.contested:
            if changes:
                plan.consider.append(_option(decision, changes))
            continue
        if blocked := [
            (decision, name, value) for name, value in changes.items() if option_arguments(name, value) is None
        ]:
            plan.blocked.extend(blocked)
        elif changes:
            plan.delta.append(_option(decision, changes))
        elif request.explicit_fields & decision.values.keys():
            continue
        elif decision.avoids:
            plan.not_recommended.append(_avoided(decision))
        else:
            plan.covers.append(_option(decision, decision.values))
    return plan


def _option_count(plan: _Plan) -> int:
    return sum(len(option.values) for option in plan.delta)


def _start(decisions: Sequence[Decision], request: RecommendRequest, recommendation: Recommendation) -> _Plan:
    """Choose the preset to start from: the configured one, or the latest preset that needs the fewest options."""
    from datamodel_code_generator.preset import latest_preset_name  # noqa: PLC0415

    config = request.config
    target = config.target_python_version
    if (configured := config.preset) is not None:
        family = cast("Family", configured.partition("-")[0])
        plan = _plan(configured, family, decisions, request, configured=True)
        if (latest := latest_preset_name(family, target)) != configured:
            recommendation.disagreements.append(Disagreement("preset", configured, latest, None, "newer-preset"))
        return plan
    plans = [
        _plan(latest_preset_name(family, target), family, decisions, request, configured=False)
        for family in load_rules().data.families
    ]
    chosen = min(plans, key=lambda plan: (bool(plan.blocked), _option_count(plan)))
    recommendation.rejected.extend(
        Rejection(plan.preset, decision, [f"--{name.replace('_', '-')}"])
        for plan in plans
        if plan is not chosen
        for decision, name, _ in plan.blocked
    )
    return chosen


def _disagreements(decisions: Sequence[Decision], request: RecommendRequest) -> list[Disagreement]:
    config = request.config
    return [
        Disagreement(name, configured, value, decision, "explicit")
        for decision in decisions
        if not decision.contested
        for name, value in decision.values.items()
        if name in request.explicit_fields and (configured := toml_value(getattr(config, name))) != value
    ]


def _command_values(request: RecommendRequest) -> tuple[dict[str, Any], list[str]]:
    """Return the command-line values to repeat, leaving out credentials and redacting URLs."""
    from datamodel_code_generator._url_redaction import redact_url  # noqa: PLC0415

    notes: list[str] = []
    values: dict[str, Any] = {}
    for name, value in request.cli_values.items():
        if name in _SENSITIVE_FIELDS:
            notes.append(f"--{name.replace('_', '-')} is not repeated here; add it to the command again.")
            continue
        if name == "url":
            notes.append("--url is repeated without its user information, query, and fragment; add them again.")
            value = redact_url(value)  # noqa: PLW2901
        values[name] = value
    return values, notes


def recommend_options(request: RecommendRequest) -> Recommendation:
    """Analyse the input schema and decide the options to add on top of the latest dated preset."""
    rules = load_rules()
    config = request.config
    text, source, stem = _read_input(request)
    input_file_type = _input_file_type(config, text)
    output_model_type = config.output_model_type.value
    signals = (
        _measure(text, kind)
        if text is not None and (kind := _ANALYSED_TYPES.get(input_file_type or "")) is not None
        else None
    )
    decisions = (
        decide(
            rules,
            signals,
            input_file_type=input_file_type or "",
            output_model_type=output_model_type,
            input_stem=stem,
        )
        if signals is not None
        else []
    )
    recommendation = Recommendation(
        rules_revision=rules.data.rules_revision,
        calibrated_presets=rules.data.calibrated_presets,
        mode=request.mode,
        source=source,
        input_file_type=input_file_type,
        signals=signals,
        output_model_type=output_model_type,
        target_python_version=config.target_python_version.value,
        preset="",
        family="",
        preset_configured=config.preset is not None,
        table=request.table,
        command_selector=request.selector,
    )
    plan = _start(decisions, request, recommendation)
    recommendation.preset = plan.preset
    recommendation.family = plan.family
    recommendation.covers = plan.covers
    recommendation.recommendations = plan.delta
    recommendation.not_recommended = plan.not_recommended
    recommendation.consider = plan.consider
    recommendation.disagreements = [
        *recommendation.disagreements,
        *_disagreements(decisions, request),
        *(Disagreement(name, plan.baseline[name], value, decision, "preset") for decision, name, value in plan.blocked),
    ]
    additions: dict[str, Any] = {} if config.preset is not None else {"preset": plan.preset}
    for option in plan.delta:
        additions.update(option.values)
    command_values, notes = _command_values(request)
    recommendation.notes.extend(notes)
    if "target_python_version" not in request.explicit_fields and config.preset is None:
        recommendation.notes.append(
            f"Target Python is not set: the recommendation assumes {recommendation.target_python_version}, the "
            "default. Set --target-python-version to start from the preset of the Python version the project runs."
        )
    if signals is None:
        recommendation.notes.append(_unanalysed_note(source, input_file_type))
    elif rules.data.calibrated_presets not in plan.preset:
        recommendation.notes.append(f"The rules were calibrated on the {rules.data.calibrated_presets} presets.")
    if request.has_project_config and not request.preset_on_command_line:
        recommendation.command_values = command_values
        recommendation.config_values = additions
        recommendation.config_complete = False
    elif request.has_project_config:
        recommendation.command_values = {**command_values, **additions}
        recommendation.config_values = None
    else:
        recommendation.command_values = {**command_values, **additions}
        recommendation.config_values = dict(recommendation.command_values)
    return recommendation


def _unanalysed_note(source: InputSource, input_file_type: str | None) -> str:
    reason = f"schema rules are not available for {input_file_type} input"
    match source:
        case "directory":
            reason = "a directory input is not analysed"
        case "input-model":
            reason = "--input-model inputs are not analysed"
    return f"Only the preset is recommended: {reason}; the rules cover OpenAPI, AsyncAPI, and JSON Schema documents."


__all__ = ["RecommendRequest", "Recommendation", "recommend_options"]
