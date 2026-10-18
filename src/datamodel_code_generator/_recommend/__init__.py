"""Option recommendations: analyse a schema and recommend options on top of the latest dated preset.

The CLI calls ``recommend_options`` once with the configuration it resolved and the requested mode, then renders
the result with ``_report.render_text`` or ``_report.render_json``. Quick mode analyses the schema and applies the
rule data; it never generates code. Full mode also generates candidate configurations in memory, compares their class
names, and recommends the best one; without a mode, schemas small enough are compared.
"""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass, field, replace
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
    from collections.abc import Set as AbstractSet

    from datamodel_code_generator._recommend._metrics import NameChanges, NameMetrics
    from datamodel_code_generator._recommend._signals import SchemaKind, Signals
    from datamodel_code_generator._recommend._trial import CandidateOutcome, TrialCandidate, TrialInput
    from datamodel_code_generator.enums import InputFileType
    from datamodel_code_generator.preset import ResolvedPresetConfig

RecommendMode: TypeAlias = Literal["quick", "full"]
InputSource: TypeAlias = Literal["file", "url", "stdin", "directory", "input-model"]
_ANALYSED_TYPES: dict[str, SchemaKind] = {"openapi": "openapi", "jsonschema": "jsonschema", "asyncapi": "asyncapi"}
_DOCUMENT_NAMES: dict[SchemaKind, str] = {
    "openapi": "an OpenAPI",
    "jsonschema": "a JSON Schema",
    "asyncapi": "an AsyncAPI",
}
_DEFAULT_TABLE = ("tool", "datamodel-codegen")
_SENSITIVE_FIELDS = frozenset({"http_headers", "http_query_parameters"})
_MAX_CANDIDATES = 8
_CandidateKey: TypeAlias = tuple[bool, frozenset[str]]
_Pair: TypeAlias = "tuple[Option, tuple[Option, ...], tuple[Option, ...]]"
_TrialRun: TypeAlias = "tuple[tuple[CandidateOutcome, ...], InputFileType | None]"


@dataclass(frozen=True, slots=True)
class RecommendRequest:
    """The configuration the CLI resolved for one recommendation.

    ``explicit_fields`` are the fields the user set, counted the way ``--preset`` counts them: the command-line
    fields when the preset is on the command line, otherwise the pyproject.toml and command-line fields.
    ``cli_values`` are the command-line options as config values, keyed by field name. ``mode`` is None when the
    command names no mode. ``generation_config`` returns the config a generation of this configuration runs with
    after config updates, applied the way ``--preset`` applies its values. ``target`` is the option that selects a
    server or client target, such as ``--generate-server``.
    """

    config: Any
    explicit_fields: frozenset[str]
    cli_values: Mapping[str, Any]
    has_project_config: bool
    preset_on_command_line: bool
    read_stdin: Callable[[], str]
    generation_config: Callable[[Mapping[str, Any]], Any]
    table: tuple[str, ...] = _DEFAULT_TABLE
    selector: tuple[str, ...] = ()
    mode: RecommendMode | None = None
    response_observer: Callable[[str, Any, Any, bytes], None] | None = None
    target: str | None = None


@dataclass(frozen=True, slots=True)
class OptionEffect:
    """What the trial measured for one option: the candidates without and with it, and the classes that change."""

    before: str
    after: str
    before_metrics: NameMetrics
    after_metrics: NameMetrics
    changes: NameChanges


@dataclass(frozen=True, slots=True)
class Option:
    """One rule outcome with the CLI arguments that express it, and its trial measurement in full mode."""

    decision: Decision
    values: Mapping[str, OptionValue]
    arguments: list[str]
    effect: OptionEffect | None = None


@dataclass(frozen=True, slots=True)
class Trial:
    """The compared candidates, their ranking, the selected one, and its changes from the current configuration.

    ``dropped`` counts candidates the limit left out; the last ``remeasured`` outcomes measure options on the selected
    configuration when it is not the rules' recommendation.
    """

    outcomes: tuple[CandidateOutcome, ...]
    ranking: tuple[CandidateOutcome, ...]
    selected: CandidateOutcome | None
    current: CandidateOutcome
    migration: NameChanges | None
    dropped: int
    remeasured: int


@dataclass(frozen=True, slots=True)
class Disagreement:
    """A value the configuration keeps although a rule suggests another one."""

    field: str
    configured: Any
    recommended: OptionValue
    decision: Decision | None
    kind: Literal["explicit", "preset", "newer-preset"]


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
    recommendations: list[Option] = field(default_factory=list)
    consider: list[Option] = field(default_factory=list)
    optional: list[Option] = field(default_factory=list)
    not_recommended: list[Option] = field(default_factory=list)
    disagreements: list[Disagreement] = field(default_factory=list)
    left_out: list[Option] = field(default_factory=list)
    trial: Trial | None = None
    notes: list[str] = field(default_factory=list)
    runnable: bool = True
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
    optional: list[Option] = field(default_factory=list)
    not_recommended: list[Option] = field(default_factory=list)
    blocked: list[tuple[Decision, str, OptionValue]] = field(default_factory=list)


def _read_file(path: Path, encoding: str, what: str) -> str:
    try:
        return path.read_text(encoding=encoding)
    except (OSError, UnicodeError) as e:
        msg = f"Unable to read {what}: {getattr(e, 'strerror', None) or e}"
        raise Error(msg) from e


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
            response_observer=request.response_observer,
            encoding=config.encoding,
        )
        return text, "url", Path(config.url.path).stem
    if config.input is not None:
        path = Path(config.input)
        if path.is_dir():
            return None, "directory", None
        return _read_file(path, config.encoding, f"the input {path.name}"), "file", path.stem
    return request.read_stdin(), "stdin", None


def _input_file_type(config: Any, text: str | None) -> str | None:
    """Return the configured input file type, or the detected one for auto."""
    from datamodel_code_generator import InputFileType, infer_input_type  # noqa: PLC0415

    if config.input_file_type != InputFileType.Auto:
        return config.input_file_type.value
    if text is None:
        return None
    return infer_input_type(text).value


def _load(text: str) -> dict[str, Any]:
    """Load a JSON or YAML mapping, raising the loader's error."""
    from datamodel_code_generator._source import load_data  # noqa: PLC0415

    return load_data(text)


def _load_errors() -> tuple[type[Exception], ...]:
    from datamodel_code_generator.util import get_yaml_parse_errors  # noqa: PLC0415

    return TypeError, ValueError, *get_yaml_parse_errors()


def _document(text: str, kind: SchemaKind, what: str) -> dict[str, Any]:
    try:
        return _load(text)
    except _load_errors() as e:
        msg = f"Unable to read {what} as {_DOCUMENT_NAMES[kind]} document: {e}"
        raise Error(msg) from e


def _measure(document: dict[str, Any], kind: SchemaKind, config: Any) -> Signals:
    """Return the signals of a schema document, for the scopes the configuration generates."""
    from datamodel_code_generator._recommend._signals import measure_signals  # noqa: PLC0415

    return measure_signals(
        document,
        kind,
        openapi_scopes=frozenset(scope.value for scope in config.openapi_scopes or ()) or frozenset({"schemas"}),
        include_paths=config.openapi_include_paths or (),
    )


@dataclass(frozen=True, slots=True)
class _Input:
    """The input as read: its text, where it came from, its type, and the schema document the rules analysed."""

    text: str | None
    source: InputSource
    stem: str | None
    file_type: str | None
    kind: SchemaKind | None
    document: dict[str, Any] | None
    signals: Signals | None
    swagger: bool = False


def _analyse(request: RecommendRequest) -> _Input:
    """Read and analyse the input; a Swagger 2.0 document typed as OpenAPI generates nothing, so it is not analysed."""
    text, source, stem = _read_input(request)
    file_type = _input_file_type(request.config, text)
    if text is None or (kind := _ANALYSED_TYPES.get(file_type or "")) is None:
        return _Input(text, source, stem, file_type, None, None, None)
    document = _document(text, kind, "the input")
    if kind == "openapi" and "swagger" in document:
        return _Input(text, source, stem, file_type, None, document, None, swagger=True)
    return _Input(text, source, stem, file_type, kind, document, _measure(document, kind, request.config))


def _option(decision: Decision, values: Mapping[str, OptionValue]) -> Option:
    arguments = [argument for name, value in values.items() for argument in option_arguments(name, value) or ()]
    return Option(decision, dict(values), arguments)


def _avoided(decision: Decision) -> Option:
    """Return the options an avoiding rule argues against, as the arguments that would turn them on."""
    return Option(decision, dict(decision.values), [f"--{name.replace('_', '-')}" for name in decision.values])


def _resolve_preset(preset: str, request: RecommendRequest) -> ResolvedPresetConfig:
    """Resolve a preset for the fields the user left unset, the way ``--preset`` does."""
    from datamodel_code_generator.preset import PresetContext, resolve_preset_config_updates  # noqa: PLC0415

    config = request.config
    return resolve_preset_config_updates(
        preset,
        context=PresetContext(
            input_file_type=config.input_file_type,
            output_model_type=config.output_model_type,
            target_python_version=config.target_python_version,
        ),
        use_annotated=config.use_annotated,
        explicit_fields=set(request.explicit_fields),
    )


def _preset_values(preset: str, request: RecommendRequest) -> dict[str, Any]:
    """Return the values a preset gives the fields the user left unset."""
    return {item.field_name: item.pyproject_value for item in _resolve_preset(preset, request).items}


def _plan(
    preset: str,
    family: str,
    decisions: Sequence[Decision],
    request: RecommendRequest,
    *,
    configured: bool,
) -> _Plan:
    """Compare the decisions with what a preset already gives the configuration.

    An optional rule lists the values the user did not set and the preset does not give, for the user to decide.
    """
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
        if decision.rule.optional is not None:
            if changes:
                plan.optional.append(_option(decision, changes))
            continue
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


def _start(decisions: Sequence[Decision], request: RecommendRequest, recommendation: Recommendation) -> _Plan:
    """Choose the preset to start from: the configured one, else the first family whose presets express every rule.

    The families are tried in rule-data order (standard first), so another family is only chosen when a rule value
    has no option on the families before it; the notes say which value that is.
    """
    from datamodel_code_generator.preset import latest_preset_name  # noqa: PLC0415

    config = request.config
    target = config.target_python_version
    if (configured := config.preset) is not None:
        family = configured.partition("-")[0]
        plan = _plan(configured, family, decisions, request, configured=True)
        if (latest := latest_preset_name(family, target)) != configured:
            recommendation.disagreements.append(Disagreement("preset", configured, latest, None, "newer-preset"))
        return plan
    plans = [
        _plan(latest_preset_name(family, target), family, decisions, request, configured=False)
        for family in load_rules().data.families
    ]
    chosen = next((plan for plan in plans if not plan.blocked), plans[0])
    recommendation.notes.extend(
        f"Starting from {chosen.preset}: no option sets --{name.replace('_', '-')} to {json.dumps(value)} on "
        f"{plan.preset} [{decision.rule.id}]."
        for plan in plans[: plans.index(chosen)]
        for decision, name, value in plan.blocked
    )
    return chosen


def _disagreements(decisions: Sequence[Decision], request: RecommendRequest) -> list[Disagreement]:
    config = request.config
    return [
        Disagreement(name, configured, value, decision, "explicit")
        for decision in decisions
        if not decision.contested and decision.rule.optional is None
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


def _mode(request: RecommendRequest, analysed: _Input, previous: str | None) -> tuple[RecommendMode, str | None]:
    """Return the mode to run and, when the default leaves candidates uncompared, the note that says why.

    Without a mode, candidates are compared while the input, with ``previous`` (the text of --diff-against), is
    estimated at no more models than the rule data's ``full_trial_max_models``: each candidate generates every input.
    """
    if request.mode is not None:
        return request.mode, None
    limit = load_rules().data.full_trial_max_models
    if analysed.signals is None or analysed.kind is None:
        reason = "the size of this input cannot be estimated"
    else:
        estimates = [analysed.signals.counts["estimated_models"]]
        if previous is not None:
            document = _document(previous, analysed.kind, f"--diff-against {Path(request.config.diff_against).name}")
            estimates.append(_measure(document, analysed.kind, request.config).counts["estimated_models"])
        if sum(estimates) <= limit:
            return "full", None
        inputs = "the input and --diff-against are" if len(estimates) > 1 else "the schema is"
        reason = f"{inputs} estimated at {sum(estimates)} models, more than the {limit} compared by default"
    return "quick", (
        f"Candidates were not compared: {reason}. Run --recommend-options full to generate each candidate "
        "configuration in memory and compare their class names."
    )


def _trial_input(request: RecommendRequest, analysed: _Input) -> TrialInput:
    """Return the input a generation of this configuration reads."""
    config = request.config
    if analysed.source == "input-model":
        from datamodel_code_generator.input_model import Error as InputModelError  # noqa: PLC0415
        from datamodel_code_generator.input_model import (  # noqa: PLC0415
            _load_model_schema_with_python_type_expressions,
        )

        try:
            return _load_model_schema_with_python_type_expressions(
                config.input_model,
                config.input_file_type,
                config.input_model_ref_strategy,
                config.output_model_type,
            )
        except InputModelError as e:
            raise Error(str(e)) from e
    if analysed.source == "stdin":
        return cast("str", analysed.text)
    return config.url if analysed.source == "url" else config.input


def _vocabulary_document(analysed: _Input, trial_input: TrialInput) -> Mapping[str, Any] | None:
    """Return the document whose words class names may take: the analysed schema, or the input loaded as data."""
    from collections.abc import Mapping  # noqa: PLC0415

    if analysed.document is not None:
        return analysed.document
    if isinstance(trial_input, Mapping):
        return dict(trial_input)
    if analysed.text is not None:
        with suppress(*_load_errors()):
            return _load(analysed.text)
    return None


def _applied(options: Sequence[Option]) -> dict[str, Any]:
    """Return the config values of options, validated into the types generation takes."""
    from datamodel_code_generator.base_config import BaseGenerateConfig  # noqa: PLC0415

    values = {name: value for option in options for name, value in option.values.items()}
    validated = BaseGenerateConfig.model_validate(values)
    return {name: getattr(validated, name) for name in values}


def _preset_updates(preset: str, request: RecommendRequest) -> dict[str, Any]:
    """Return the config updates ``--preset`` makes, including the target and forced field constraints."""
    resolved = _resolve_preset(preset, request)
    updates: dict[str, Any] = {item.field_name: item.applied_value for item in resolved.items}
    if resolved.target_python_version is not None:
        updates["target_python_version"] = resolved.target_python_version
    if resolved.force_field_constraints:
        updates["field_constraints"] = True
    return updates


@dataclass(frozen=True, slots=True)
class _Candidate:
    """A candidate to compare: the current configuration or the preset, with options on top."""

    label: str
    on_preset: bool
    options: tuple[Option, ...]
    selectable: bool = True

    @property
    def key(self) -> _CandidateKey:
        return _key(self.options, on_preset=self.on_preset)


def _key(options: Sequence[Option], *, on_preset: bool = True) -> _CandidateKey:
    return on_preset, frozenset(option.decision.rule.id for option in options)


def _candidates(
    delta: Sequence[Option], consider: Sequence[Option], *, configured: bool
) -> tuple[list[_Candidate], int]:
    """Return the candidates to compare and how many the limit of eight left out.

    The current configuration, the preset, the recommendation, the recommendation with each option it may also take,
    and the recommendation without each option it adds; with a configured preset, the current configuration is the
    preset. A configuration is compared once, under its first label, and the last candidates are left out first.
    """
    base = "current" if configured else "preset"
    recommended = "recommended" if delta else base
    proposed = [
        *(() if configured else (_Candidate("current", on_preset=False, options=(), selectable=False),)),
        _Candidate(base, on_preset=True, options=()),
        _Candidate(recommended, on_preset=True, options=tuple(delta)),
        *(
            _Candidate(f"{recommended} + {option.decision.rule.decision}", on_preset=True, options=(*delta, option))
            for option in consider
        ),
        *(
            _Candidate(
                f"recommended - {option.decision.rule.decision}",
                on_preset=True,
                options=tuple(other for other in delta if other is not option),
            )
            for option in delta
        ),
    ]
    unique: dict[_CandidateKey, _Candidate] = {}
    for candidate in proposed:
        unique.setdefault(candidate.key, candidate)
    return list(unique.values())[:_MAX_CANDIDATES], max(len(unique) - _MAX_CANDIDATES, 0)


def _pairs(chosen: Sequence[Option], options: Sequence[Option]) -> list[_Pair]:
    """Return each option with the configurations without and with it around the chosen options."""
    chosen_rules = _key(chosen)[1]
    return [
        (
            option,
            tuple(other for other in chosen if other.decision.rule.id != option.decision.rule.id),
            (*chosen, *(() if option.decision.rule.id in chosen_rules else (option,))),
        )
        for option in options
    ]


def _remeasure(selected: _Candidate, pairs: Sequence[_Pair], known: AbstractSet[_CandidateKey]) -> list[_Candidate]:
    """Return the configurations next to the selected one that no candidate covered: without or with one option."""
    chosen_rules = _key(selected.options)[1]
    missing: dict[_CandidateKey, _Candidate] = {}
    for option, before, after in pairs:
        kept = option.decision.rule.id in chosen_rules
        label = f"{selected.label} {'-' if kept else '+'} {option.decision.rule.decision}"
        if (key := _key(options := before if kept else after)) not in known:
            missing.setdefault(key, _Candidate(label, on_preset=True, options=options, selectable=False))
    return list(missing.values())


def _measured(
    option: Option,
    outcomes: Mapping[_CandidateKey, CandidateOutcome],
    before: Sequence[Option],
    after: Sequence[Option],
) -> Option:
    """Return an option with the trial's measurement, when the candidates without and with it both generated."""
    from datamodel_code_generator._recommend._metrics import location_changes  # noqa: PLC0415

    without, with_ = outcomes.get(_key(before)), outcomes.get(_key(after))
    if without is None or with_ is None or without.metrics is None or with_.metrics is None:
        return option
    effect = OptionEffect(
        without.candidate.label,
        with_.candidate.label,
        without.metrics,
        with_.metrics,
        location_changes(without.records, with_.records),
    )
    return replace(option, effect=effect)


def _trial_candidates(
    request: RecommendRequest,
    recommendation: Recommendation,
    candidates: Sequence[_Candidate],
    input_file_type: InputFileType | None,
) -> list[TrialCandidate]:
    """Return each candidate's generation config: the configuration, the preset, and its options."""
    from datamodel_code_generator import InputFileType  # noqa: PLC0415
    from datamodel_code_generator._recommend._trial import TrialCandidate  # noqa: PLC0415

    config = request.config
    common = (
        {"input_file_type": input_file_type}
        if config.input_file_type == InputFileType.Auto and input_file_type is not None
        else {}
    )
    configured = config.preset is not None
    preset = {} if configured else _preset_updates(recommendation.preset, request)
    return [
        TrialCandidate(
            candidate.label,
            request.generation_config({
                **common,
                **(preset if candidate.on_preset else {}),
                **_applied(candidate.options),
            }),
            recommendation.preset if candidate.on_preset or configured else None,
            {name: value for option in candidate.options for name, value in option.values.items()},
            selectable=candidate.selectable,
        )
        for candidate in candidates
    ]


def _known_type(analysed: _Input) -> InputFileType | None:
    """Return the input type the analysis knows: the configured or detected one, JSON Schema for --input-model."""
    from datamodel_code_generator import InputFileType  # noqa: PLC0415

    known = analysed.file_type or ("jsonschema" if analysed.source == "input-model" else None)
    return None if known is None else InputFileType(known)


def _runner(request: RecommendRequest, analysed: _Input) -> Callable[[Sequence[TrialCandidate]], _TrialRun]:
    """Return the function that generates candidates: one input, its vocabulary, the previous input, one cache."""
    from functools import partial  # noqa: PLC0415

    from datamodel_code_generator._recommend._metrics import schema_vocabulary  # noqa: PLC0415
    from datamodel_code_generator._recommend._trial import run_trial  # noqa: PLC0415
    from datamodel_code_generator._shared_types import DefaultPutDict  # noqa: PLC0415

    config = request.config
    trial_input = _trial_input(request, analysed)
    remote_text_cache: DefaultPutDict[str, str] = DefaultPutDict()
    if analysed.source == "url":
        remote_text_cache[config.url.geturl()] = cast("str", analysed.text)
    return partial(
        run_trial,
        trial_input,
        vocabulary=schema_vocabulary(_vocabulary_document(analysed, trial_input)),
        previous_input=None if config.diff_against is None else Path(config.diff_against),
        remote_text_cache=remote_text_cache,
    )


def _compare(request: RecommendRequest, recommendation: Recommendation, analysed: _Input) -> None:
    """Generate the candidates, rank their class names, and recommend the options of the best one.

    When the best candidate is not the rules' recommendation, the configurations next to it that the first candidates
    did not cover are generated too, so each option is measured on the selected configuration.
    """
    from datamodel_code_generator._recommend._trial import rank  # noqa: PLC0415

    run = _runner(request, analysed)
    delta = recommendation.recommendations
    candidates, dropped = _candidates(delta, recommendation.consider, configured=request.config.preset is not None)
    known = _known_type(analysed)
    outcomes, detected = run(_trial_candidates(request, recommendation, candidates, known))
    by_key = {candidate.key: outcome for candidate, outcome in zip(candidates, outcomes, strict=True)}
    selected = next(
        (outcome for outcome in rank(outcomes) if outcome.error is None and outcome.candidate.selectable), None
    )
    center = next((candidate for candidate in candidates if by_key[candidate.key] is selected), None)
    chosen = delta if center is None else center.options
    pairs = _pairs(chosen, [*delta, *recommendation.consider])
    extra = [] if center is None or center.key == _key(delta) else _remeasure(center, pairs, by_key.keys())
    if extra:
        more, _ = run(_trial_candidates(request, recommendation, extra, detected))
        by_key.update((candidate.key, outcome) for candidate, outcome in zip(extra, more, strict=True))
        outcomes = (*outcomes, *more)
    _select(
        recommendation,
        pairs,
        by_key,
        Trial(outcomes, (), selected, outcomes[0], None, dropped, len(extra)),
        _key(chosen)[1],
    )
    _trial_notes(request, recommendation, None if known is not None else detected)


def _select(
    recommendation: Recommendation,
    pairs: Sequence[_Pair],
    by_key: Mapping[_CandidateKey, CandidateOutcome],
    trial: Trial,
    chosen: frozenset[str],
) -> None:
    """Recommend the chosen options, each with its measurement around them, and rank the trial's candidates."""
    from datamodel_code_generator._recommend._metrics import location_changes  # noqa: PLC0415
    from datamodel_code_generator._recommend._trial import rank  # noqa: PLC0415

    delta_rules = _key(recommendation.recommendations)[1]
    measured = [_measured(option, by_key, before, after) for option, before, after in pairs]
    recommendation.recommendations = [option for option in measured if option.decision.rule.id in chosen]
    recommendation.left_out = [option for option in measured if option.decision.rule.id in delta_rules - chosen]
    recommendation.consider = [option for option in measured if option.decision.rule.id not in delta_rules | chosen]
    current = next(outcome for outcome in trial.outcomes if outcome.candidate.label == "current")
    selected = trial.selected
    migration = (
        location_changes(current.records, selected.records)
        if selected is not None and selected is not current and current.error is None
        else None
    )
    recommendation.trial = replace(trial, ranking=rank(trial.outcomes), current=current, migration=migration)


def _trial_notes(request: RecommendRequest, recommendation: Recommendation, detected: InputFileType | None) -> None:
    """Note the candidates the limit left out, a trial where every candidate failed, the type read, and a target."""
    trial = cast("Trial", recommendation.trial)
    if trial.dropped:
        recommendation.notes.append(
            f"The trial compares at most {_MAX_CANDIDATES} candidates, so {trial.dropped} "
            f"{'was' if trial.dropped == 1 else 'were'} left out; the options they would measure show no trial result."
        )
    if trial.selected is None:
        recommendation.notes.append(
            "Every candidate on the preset failed to generate, so the options are the rules' recommendation alone."
        )
    if detected is not None:
        recommendation.notes.append(
            f"The trial read the input as {detected.value}; set --input-file-type to choose the input file type."
        )
    if request.target is not None:
        recommendation.notes.append(
            f"The trial compares the models only; the package {request.target} generates is not generated here."
        )


def _set_command(request: RecommendRequest, recommendation: Recommendation, command_values: dict[str, Any]) -> None:
    """Set the command and the pyproject.toml keys that carry the recommendation."""
    additions: dict[str, Any] = {} if request.config.preset is not None else {"preset": recommendation.preset}
    for option in recommendation.recommendations:
        additions.update(option.values)
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


def recommend_options(request: RecommendRequest) -> Recommendation:
    """Analyse the input schema and decide the options to add on top of the latest dated preset."""
    rules = load_rules()
    config = request.config
    analysed = _analyse(request)
    signals = analysed.signals
    output_model_type = config.output_model_type.value
    decisions = (
        decide(
            rules,
            signals,
            input_file_type=analysed.file_type or "",
            output_model_type=output_model_type,
            input_stem=analysed.stem,
        )
        if signals is not None
        else []
    )
    previous = (
        None
        if config.diff_against is None
        else _read_file(Path(config.diff_against), config.encoding, f"--diff-against {Path(config.diff_against).name}")
    )
    mode, mode_note = _mode(request, analysed, previous)
    recommendation = Recommendation(
        rules_revision=rules.data.rules_revision,
        calibrated_presets=rules.data.calibrated_presets,
        mode=mode,
        source=analysed.source,
        input_file_type=analysed.file_type,
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
    recommendation.optional = plan.optional
    recommendation.disagreements = [
        *recommendation.disagreements,
        *_disagreements(decisions, request),
        *(Disagreement(name, plan.baseline[name], value, decision, "preset") for decision, name, value in plan.blocked),
    ]
    command_values, notes = _command_values(request)
    recommendation.notes.extend(notes)
    if "target_python_version" not in request.explicit_fields and config.preset is None:
        assumed = "the default" if request.target is None else f"the lowest {request.target} supports"
        recommendation.notes.append(
            f"Target Python is not set: the recommendation assumes {recommendation.target_python_version}, {assumed}. "
            "Set --target-python-version to start from the preset of the Python version the project runs."
        )
    if signals is None:
        recommendation.notes.append(_unanalysed_note(analysed))
    elif rules.data.calibrated_presets not in plan.preset:
        recommendation.notes.append(f"The rules were calibrated on the {rules.data.calibrated_presets} presets.")
    if mode_note is not None:
        recommendation.notes.append(mode_note)
    if mode == "full":
        _compare(request, recommendation, analysed)
    if analysed.swagger:
        recommendation.runnable = False
    else:
        _set_command(request, recommendation, command_values)
    return recommendation


def _unanalysed_note(analysed: _Input) -> str:
    reason = f"schema rules are not available for {analysed.file_type} input"
    match analysed.source:
        case "directory":
            reason = "a directory input is not analysed"
        case "input-model":
            reason = "--input-model inputs are not analysed"
    if analysed.swagger:
        return (
            "Nothing is recommended: this is a Swagger 2.0 document, and with --input-file-type openapi generation "
            "finds no models in it. Generation reads Swagger 2.0 definitions with --input-file-type jsonschema; run "
            "the recommendation with it."
        )
    return f"Only the preset is recommended: {reason}; the rules cover OpenAPI, AsyncAPI, and JSON Schema documents."


__all__ = ["RecommendRequest", "Recommendation", "recommend_options"]
