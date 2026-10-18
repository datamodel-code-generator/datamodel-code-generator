"""Text and JSON output of option recommendations."""

from __future__ import annotations

import json
import textwrap
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from datamodel_code_generator._recommend._rules import format_text, option_arguments
from datamodel_code_generator._recommend._signals import Signals

if TYPE_CHECKING:
    from collections.abc import Sequence

    from datamodel_code_generator._recommend import Disagreement, Option, Recommendation, Trial
    from datamodel_code_generator._recommend._metrics import NameChanges, NameMetrics, NameStability
    from datamodel_code_generator._recommend._trial import CandidateOutcome
    from datamodel_code_generator._structured_output import (
        RecommendCandidatePayload,
        RecommendDisagreementPayload,
        RecommendNameChangesPayload,
        RecommendOptionPayload,
        RecommendTrialPayload,
    )

_WIDTH = 100
_DETAIL_INDENT = " " * 6
_EXAMPLE_INDENT = " " * 8
_TABLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("classes", "types"),
    ("problems", "problems"),
    ("numbered", "numbered"),
    ("long", "long"),
    ("doubled", "doubled"),
    ("displaced", "displaced"),
    ("median", "median_length"),
)
_OPTIONAL_SECTIONS: tuple[tuple[str, str], ...] = (
    ("validation", "Optional (validation; not in the command)"),
    ("policy", "Optional (policy; not in the command)"),
)
_SIGNAL_LINES: tuple[tuple[str, str], ...] = (
    ("components", "{components}, {operations} operations"),
    ("inline objects / enums", "{inline_objects} / {inline_enums}, nesting depth {max_inline_depth}"),
    ("shared property names", "{shared_inline_names} ({shared_inline_name_examples})"),
    ("inline names = component names", "{inline_names_equal_component}"),
    (
        "repeated inline objects / enums",
        (
            "{duplicate_inline_objects} of {duplicate_inline_shapes} shapes / "
            "{duplicate_inline_enums} of {duplicate_inline_enum_shapes} value sets"
        ),
    ),
    (
        "titled components",
        (
            "{titled_components}: {component_title_duplicate_groups} shared titles ({duplicate_title_examples}), "
            "{component_titles_equal_other_key} equal another key, {component_titles_equal_own_key} equal their own key"
        ),
    ),
    (
        "inline titles",
        "{inline_titled_objects} objects, most repeated {inline_title_max_repeat} ({inline_title_examples})",
    ),
    (
        "unions",
        "{unions} oneOf/anyOf, {discriminated_unions} with a discriminator, {inline_union_variants} inline variants",
    ),
    (
        "union branch tags",
        (
            "{inline_tagged_union_branches} tagged, {single_key_required_open} single required key, "
            "{tagged_behind_ref} tagged only through allOf $ref, {parent_copy_union_branches} copy their parent"
        ),
    ),
    ("component keys", "{key_style}; {dotted_keys} dotted in {dotted_key_namespaces} namespaces"),
    (
        "constraint keywords",
        (
            "uniqueItems {unique_items}, patternProperties {pattern_properties} ({unmodeled_pattern_properties} beyond "
            "a typed map), multipleOf {multiple_of}, OpenAPI 3.0 nullable {nullable_keywords}, boolean exclusive "
            "bounds {boolean_exclusive_bounds}"
        ),
    ),
    (
        "formats",
        (
            "{formats} ({format_examples}), decimal {decimal_formats}; int32/int64 {integer_range_formats}, "
            "ranges not enforced"
        ),
    ),
    ("integer/number/boolean schemas", "{coercible_scalars}"),
)
_ROOT_LINE = ("root schema", "object model {root_is_object}, title words {root_title_words}")
_NO_SIGNALS = Signals(counts={}, facts={})


def _wrap(text: str, indent: str = _DETAIL_INDENT) -> list[str]:
    return textwrap.wrap(text, width=_WIDTH, initial_indent=indent, subsequent_indent=indent, break_on_hyphens=False)


def _value_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _count(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 'es' if noun.endswith('s') else 's'}"


def _changes_text(changes: NameChanges) -> str:
    return (
        f"{changes.renamed} of {_count(changes.matched, 'class')} matched by schema location renamed "
        f"({changes.renumbered} only renumbered), {changes.removed} removed, {changes.added} added"
        f"{', e.g.' if changes.examples else ''}"
    )


def _example_lines(changes: NameChanges, indent: str = _EXAMPLE_INDENT) -> list[str]:
    return [f"{indent}{change.before} -> {change.after}   at {change.location}" for change in changes.examples]


def _effect_lines(option: Option) -> list[str]:
    if (effect := option.effect) is None:
        return []
    without, with_ = effect.before_metrics, effect.after_metrics
    text = (
        f"trial: {effect.before} -> {effect.after}: classes {without.types} -> {with_.types}, "
        f"problem names {without.problems} -> {with_.problems}; {_changes_text(effect.changes)}"
    )
    return [*_wrap(text), *_example_lines(effect.changes)]


def _option_lines(option: Option, signals: Signals, *, against: bool = False) -> list[str]:
    rule = option.decision.rule
    lines = [f"  {' '.join(option.arguments)}   [{rule.id}]", *_wrap(format_text(rule.reason, signals))]
    if against:
        lines.extend(_wrap(f"against: {format_text(rule.contested_reason, signals)}"))
    lines.extend(line for caveat in rule.caveats for line in _wrap(f"caveat: {format_text(caveat, signals)}"))
    lines.extend(line for evidence in rule.evidence for line in _wrap(f"measured: {evidence}"))
    lines.extend(_effect_lines(option))
    return lines


def _metric_cells(metrics: NameMetrics, stability: NameStability | None, unconstrained: int | None) -> list[str]:
    cells = [
        f"{metrics.problems} ({metrics.problems_percent}%)" if name == "problems" else str(getattr(metrics, name))
        for _, name in _TABLE_COLUMNS
    ]
    return [*cells, str(unconstrained), *(() if stability is None else (str(stability.locations.renamed),))]


def _table_row(cells: Sequence[str], widths: Sequence[int]) -> str:
    return "  " + "  ".join(
        cell.ljust(width) if index == 0 else cell.rjust(width)
        for index, (cell, width) in enumerate(zip(cells, widths, strict=False))
    )


def _trial_footnotes(trial: Trial, *, stability: bool) -> list[str]:
    notes: list[str] = []
    if not trial.outcomes[0].candidate.selectable:
        notes.append(
            f"{trial.outcomes[0].candidate.label}: the configuration as set, without the preset; never selected"
        )
    if trial.remeasured:
        notes.append(
            f"the last {_count(trial.remeasured, 'row')}: the selected configuration without or with one option, "
            "to measure each option on it; never selected"
        )
    notes.append("unconstrained: fields that dcg warns it generates without some of their constraints")
    if stability:
        notes.append("renamed: classes a candidate renames between --diff-against and the input")
    return [line for note in notes for line in _wrap(note, "  ")]


def _trial_lines(trial: Trial) -> list[str]:
    """Render the candidates' table and the changes from the current configuration."""
    stability = any(outcome.stability is not None for outcome in trial.outcomes)
    headers = [
        "candidate",
        *(header for header, _ in _TABLE_COLUMNS),
        "unconstrained",
        *(("renamed",) if stability else ()),
    ]
    rows = [
        [
            outcome.candidate.label,
            *(
                ()
                if outcome.metrics is None
                else _metric_cells(outcome.metrics, outcome.stability, outcome.dropped_constraints)
            ),
        ]
        for outcome in trial.outcomes
    ]
    widths = [max(len(row[index]) for row in (headers, *rows) if index < len(row)) for index in range(len(headers))]
    lines = [
        "",
        f"Trial  {_count(len(trial.outcomes), 'candidate')} generated in memory without formatting; nothing written",
        _table_row(headers, widths),
    ]
    for outcome, row in zip(trial.outcomes, rows, strict=True):
        if outcome.error is not None:
            lines.append(f"{_table_row(row, widths)}  rejected: {outcome.error}")
        else:
            lines.append(_table_row(row, widths) + ("   selected" if outcome is trial.selected else ""))
    lines.extend(_trial_footnotes(trial, stability=stability))
    if trial.migration is not None and trial.selected is not None:
        labels = f"{trial.current.candidate.label} -> {trial.selected.candidate.label}"
        lines.extend((
            "",
            f"Changes from the current configuration ({labels})",
            *_wrap(_changes_text(trial.migration), "  "),
            *_example_lines(trial.migration, "    "),
        ))
    return lines


def _signal_lines(signals: Signals, input_file_type: str | None) -> list[str]:
    values = {**signals.counts, **{name: value or "none" for name, value in signals.facts.items()}}
    rows = [*_SIGNAL_LINES, _ROOT_LINE] if input_file_type == "jsonschema" else list(_SIGNAL_LINES)
    width = max(len(label) for label, _ in rows)
    scopes = f" (OpenAPI scopes: {signals.facts['scopes']})" if signals.facts["scopes"] else ""
    return [f"Schema  {signals.facts['schema']}{scopes}"] + [
        f"  {label.ljust(width)}  {template.format_map(values)}" for label, template in rows
    ]


def _flag(name: str) -> str:
    return f"--{name.replace('_', '-')}"


def _disagreement_text(disagreement: Disagreement, preset: str) -> str:
    flag = _flag(disagreement.field)
    text = (
        f"{flag} is set to {_value_text(disagreement.configured)}; the rules suggest "
        f"{_value_text(disagreement.recommended)}"
    )
    match disagreement.kind:
        case "newer-preset":
            text = f"--preset {disagreement.configured}: a newer preset is available, {disagreement.recommended}"
        case "preset":
            text = (
                f"{preset} sets {flag} to {_value_text(disagreement.configured)}; no option sets it to "
                f"{_value_text(disagreement.recommended)}"
            )
    return text if disagreement.decision is None else f"{text}   [{disagreement.decision.rule.id}]"


def _start_lines(recommendation: Recommendation, signals: Signals) -> list[str]:
    """Render the preset to start from and the options to add on top of it."""
    added = sum(len(option.values) for option in recommendation.recommendations)
    return [
        "",
        f"Start from  --preset {recommendation.preset}",
        *(
            f"  already sets {' '.join(option.arguments)}   [{option.decision.rule.id}]"
            for option in recommendation.covers
        ),
        "",
        f"Add ({added} option{'' if added == 1 else 's'})" if added else "Add  nothing on top of the preset",
        *(line for option in recommendation.recommendations for line in _option_lines(option, signals)),
    ]


def _optional(recommendation: Recommendation, section: str) -> list[Option]:
    """Return the optional rule outcomes of one section of the report."""
    return [option for option in recommendation.optional if option.decision.rule.optional == section]


def render_text(recommendation: Recommendation, *, command: str | None, pyproject: str | None) -> str:
    """Render a recommendation as the text report; with nothing to run, it shows the schema and the notes only."""
    signals = recommendation.signals or _NO_SIGNALS
    lines = [
        f"Option recommendations (experimental; {recommendation.mode} mode; rules {recommendation.rules_revision})",
        "",
    ]
    if recommendation.signals is None:
        lines.append(f"Schema  not analysed ({recommendation.input_file_type or recommendation.source} input)")
    else:
        lines.extend(_signal_lines(signals, recommendation.input_file_type))
    if recommendation.runnable:
        lines.extend(_start_lines(recommendation, signals))
    for title, options, against in (
        ("Left out after the trial (the rules recommend these)", recommendation.left_out, False),
        ("Consider (the schema also has signals against these)", recommendation.consider, True),
        *((title, _optional(recommendation, section), False) for section, title in _OPTIONAL_SECTIONS),
        ("Not recommended", recommendation.not_recommended, False),
    ):
        if options:
            lines.extend(("", title))
            lines.extend(line for option in options for line in _option_lines(option, signals, against=against))
    if recommendation.trial is not None:
        lines.extend(_trial_lines(recommendation.trial))
    if recommendation.disagreements:
        lines.extend(("", "Your configuration"))
        lines.extend(f"  {_disagreement_text(item, recommendation.preset)}" for item in recommendation.disagreements)
    if recommendation.notes:
        lines.extend(("", "Notes"))
        for note in recommendation.notes:
            lines.extend(
                textwrap.wrap(
                    note, width=_WIDTH, initial_indent="  - ", subsequent_indent="    ", break_on_hyphens=False
                )
            )
    if command is not None:
        lines.extend(("", "Command", f"  {command}"))
    if pyproject is not None:
        complete = recommendation.config_complete
        title = "pyproject.toml" if complete else "pyproject.toml: keys to add or change"
        body = pyproject.splitlines() if complete or recommendation.config_values else ["nothing to change"]
        lines.extend(("", title, *(f"  {line}" for line in body)))
    return "\n".join(lines) + "\n"


def _changes_payload(changes: NameChanges) -> RecommendNameChangesPayload:
    from datamodel_code_generator._structured_output import RecommendNameChangesPayload  # noqa: PLC0415

    return RecommendNameChangesPayload.model_validate(asdict(changes))


def _candidate_payload(outcome: CandidateOutcome) -> RecommendCandidatePayload:
    from datamodel_code_generator._structured_output import (  # noqa: PLC0415
        RecommendCandidatePayload,
        RecommendNameMetricsPayload,
        RecommendNameStabilityPayload,
    )

    candidate, metrics, stability = outcome.candidate, outcome.metrics, outcome.stability
    return RecommendCandidatePayload(
        label=candidate.label,
        preset=candidate.preset,
        set=dict(candidate.options),
        arguments=[
            argument for name, value in candidate.options.items() for argument in option_arguments(name, value) or ()
        ],
        selectable=candidate.selectable,
        error=outcome.error,
        dropped_constraint_warnings=outcome.dropped_constraints,
        metrics=None
        if metrics is None
        else RecommendNameMetricsPayload(**asdict(metrics), problems_percent=metrics.problems_percent),
        stability=None if stability is None else RecommendNameStabilityPayload.model_validate(asdict(stability)),
    )


def _trial_payload(trial: Trial | None) -> RecommendTrialPayload | None:
    from datamodel_code_generator._recommend._metrics import METRICS_VERSION  # noqa: PLC0415
    from datamodel_code_generator._structured_output import RecommendTrialPayload  # noqa: PLC0415

    if trial is None:
        return None
    return RecommendTrialPayload(
        metrics_version=METRICS_VERSION,
        candidates=[_candidate_payload(outcome) for outcome in trial.outcomes],
        ranking=[outcome.candidate.label for outcome in trial.ranking],
        selected=None if trial.selected is None else trial.selected.candidate.label,
        current=trial.current.candidate.label,
        migration=None if trial.migration is None else _changes_payload(trial.migration),
        dropped=trial.dropped,
        remeasured=trial.remeasured,
    )


def _option_payload(option: Option, signals: Signals) -> RecommendOptionPayload:
    from datamodel_code_generator._structured_output import (  # noqa: PLC0415
        RecommendOptionPayload,
        RecommendOptionTrialPayload,
    )

    rule = option.decision.rule
    effect = option.effect
    return RecommendOptionPayload(
        rule=rule.id,
        decision=rule.decision,
        set=dict(option.values),
        arguments=option.arguments,
        reason=format_text(rule.reason, signals),
        contested_reason=format_text(rule.contested_reason, signals) if option.decision.contested else None,
        caveats=[format_text(caveat, signals) for caveat in rule.caveats],
        evidence=list(rule.evidence),
        trial=None
        if effect is None
        else RecommendOptionTrialPayload(
            before=effect.before,
            after=effect.after,
            changes=_changes_payload(effect.changes),
        ),
    )


def _disagreement_payload(disagreement: Disagreement) -> RecommendDisagreementPayload:
    from datamodel_code_generator._structured_output import RecommendDisagreementPayload  # noqa: PLC0415

    return RecommendDisagreementPayload(
        field=disagreement.field,
        kind=disagreement.kind,
        configured=disagreement.configured,
        recommended=disagreement.recommended,
        rule=None if disagreement.decision is None else disagreement.decision.rule.id,
    )


def _nested(table: tuple[str, ...], values: dict[str, Any]) -> dict[str, Any]:
    nested: dict[str, Any] = values
    for key in reversed(table):
        nested = {key: nested}
    return nested


def render_json(recommendation: Recommendation, *, content: str, arguments: list[str]) -> str:
    """Render a recommendation as the recommend-options JSON payload."""
    return json.dumps(
        recommendation_payload(recommendation, content=content, arguments=arguments), indent=2, ensure_ascii=False
    )


def recommendation_payload(recommendation: Recommendation, *, content: str, arguments: list[str]) -> dict[str, Any]:
    """Return the recommend-options JSON payload of a recommendation as JSON values."""
    from datamodel_code_generator._structured_output import (  # noqa: PLC0415
        RecommendBaselinePayload,
        RecommendContextPayload,
        RecommendInputPayload,
        RecommendOptionsPayload,
    )

    signals = recommendation.signals or _NO_SIGNALS
    config = recommendation.config_values
    payload = RecommendOptionsPayload(
        version=1,
        format="json",
        kind="recommend-options",
        mode=recommendation.mode,
        rules_revision=recommendation.rules_revision,
        calibrated_presets=recommendation.calibrated_presets,
        input=RecommendInputPayload(
            source=recommendation.source,
            input_file_type=recommendation.input_file_type,
            document=signals.facts.get("schema"),
            analysed=recommendation.signals is not None,
        ),
        context=RecommendContextPayload(
            output_model_type=recommendation.output_model_type,
            target_python_version=recommendation.target_python_version,
        ),
        signals=dict(signals.counts),
        facts=dict(signals.facts),
        baseline=RecommendBaselinePayload(
            preset=recommendation.preset,
            family=recommendation.family,
            configured=recommendation.preset_configured,
            covers=[_option_payload(option, signals) for option in recommendation.covers],
        ),
        recommendations=[_option_payload(option, signals) for option in recommendation.recommendations],
        left_out=[_option_payload(option, signals) for option in recommendation.left_out],
        consider=[_option_payload(option, signals) for option in recommendation.consider],
        validation=[_option_payload(option, signals) for option in _optional(recommendation, "validation")],
        policy=[_option_payload(option, signals) for option in _optional(recommendation, "policy")],
        not_recommended=[_option_payload(option, signals) for option in recommendation.not_recommended],
        disagreements=[_disagreement_payload(item) for item in recommendation.disagreements],
        trial=_trial_payload(recommendation.trial),
        notes=recommendation.notes,
        arguments=arguments,
        config=None
        if config is None
        else _nested(recommendation.table, {name.replace("_", "-"): value for name, value in sorted(config.items())}),
        config_complete=recommendation.config_complete,
        content=content,
    )
    return payload.model_dump(mode="json")
