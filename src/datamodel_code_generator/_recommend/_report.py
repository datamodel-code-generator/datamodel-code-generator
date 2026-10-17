"""Text and JSON output of option recommendations."""

from __future__ import annotations

import json
import textwrap
from typing import TYPE_CHECKING, Any

from datamodel_code_generator._recommend._rules import format_text
from datamodel_code_generator._recommend._signals import Signals

if TYPE_CHECKING:
    from datamodel_code_generator._recommend import Disagreement, Option, Recommendation, Rejection
    from datamodel_code_generator._structured_output import (
        RecommendDisagreementPayload,
        RecommendOptionPayload,
        RecommendRejectedPayload,
    )

_WIDTH = 100
_DETAIL_INDENT = " " * 6
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
)
_ROOT_LINE = ("root schema", "object model {root_is_object}, title words {root_title_words}")
_NO_SIGNALS = Signals(counts={}, facts={})


def _wrap(text: str, indent: str = _DETAIL_INDENT) -> list[str]:
    return textwrap.wrap(text, width=_WIDTH, initial_indent=indent, subsequent_indent=indent, break_on_hyphens=False)


def _value_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _option_lines(option: Option, signals: Signals, *, against: bool = False) -> list[str]:
    rule = option.decision.rule
    lines = [f"  {' '.join(option.arguments)}   [{rule.id}]", *_wrap(format_text(rule.reason, signals))]
    if against:
        lines.extend(_wrap(f"against: {format_text(rule.contested_reason, signals)}"))
    lines.extend(line for caveat in rule.caveats for line in _wrap(f"caveat: {format_text(caveat, signals)}"))
    lines.extend(line for evidence in rule.evidence for line in _wrap(f"measured: {evidence}"))
    return lines


def _signal_lines(signals: Signals, input_file_type: str | None) -> list[str]:
    values = {**signals.counts, **{name: value or "none" for name, value in signals.facts.items()}}
    rows = [*_SIGNAL_LINES, _ROOT_LINE] if input_file_type == "jsonschema" else list(_SIGNAL_LINES)
    width = max(len(label) for label, _ in rows)
    return [f"Schema  {signals.facts['schema']}"] + [
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


def render_text(recommendation: Recommendation, *, command: str, pyproject: str | None) -> str:
    """Render a recommendation as the text report."""
    signals = recommendation.signals or _NO_SIGNALS
    lines = [
        f"Option recommendations (experimental; {recommendation.mode} mode; rules {recommendation.rules_revision})",
        "",
    ]
    if recommendation.signals is None:
        lines.append(f"Schema  not analysed ({recommendation.input_file_type or recommendation.source} input)")
    else:
        lines.extend(_signal_lines(signals, recommendation.input_file_type))
    lines.extend(("", f"Start from  --preset {recommendation.preset}"))
    lines.extend(
        f"  already sets {' '.join(option.arguments)}   [{option.decision.rule.id}]" for option in recommendation.covers
    )
    lines.extend(
        f"  not {rejection.preset}: {' '.join(rejection.arguments)} cannot be turned off on it   "
        f"[{rejection.decision.rule.id}]"
        for rejection in recommendation.rejected
    )
    added = sum(len(option.values) for option in recommendation.recommendations)
    lines.extend((
        "",
        f"Add ({added} option{'' if added == 1 else 's'})" if added else "Add  nothing on top of the preset",
    ))
    for option in recommendation.recommendations:
        lines.extend(_option_lines(option, signals))
    for title, options, against in (
        ("Consider (the schema also has signals against these)", recommendation.consider, True),
        ("Not recommended", recommendation.not_recommended, False),
    ):
        if options:
            lines.extend(("", title))
            lines.extend(line for option in options for line in _option_lines(option, signals, against=against))
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
    lines.extend(("", "Command", f"  {command}"))
    if pyproject is not None:
        complete = recommendation.config_complete
        title = "pyproject.toml" if complete else "pyproject.toml: keys to add or change"
        body = pyproject.splitlines() if complete or recommendation.config_values else ["nothing to change"]
        lines.extend(("", title, *(f"  {line}" for line in body)))
    return "\n".join(lines) + "\n"


def _option_payload(option: Option, signals: Signals) -> RecommendOptionPayload:
    from datamodel_code_generator._structured_output import RecommendOptionPayload  # noqa: PLC0415

    rule = option.decision.rule
    return RecommendOptionPayload(
        rule=rule.id,
        decision=rule.decision,
        set=dict(option.values),
        arguments=option.arguments,
        reason=format_text(rule.reason, signals),
        contested_reason=format_text(rule.contested_reason, signals) if option.decision.contested else None,
        caveats=[format_text(caveat, signals) for caveat in rule.caveats],
        evidence=list(rule.evidence),
    )


def _rejected_payload(rejection: Rejection, signals: Signals) -> RecommendRejectedPayload:
    from datamodel_code_generator._structured_output import RecommendRejectedPayload  # noqa: PLC0415

    return RecommendRejectedPayload(
        preset=rejection.preset,
        rule=rejection.decision.rule.id,
        arguments=rejection.arguments,
        reason=format_text(rejection.decision.rule.reason, signals),
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
            rejected=[_rejected_payload(rejection, signals) for rejection in recommendation.rejected],
        ),
        recommendations=[_option_payload(option, signals) for option in recommendation.recommendations],
        consider=[_option_payload(option, signals) for option in recommendation.consider],
        not_recommended=[_option_payload(option, signals) for option in recommendation.not_recommended],
        disagreements=[_disagreement_payload(item) for item in recommendation.disagreements],
        notes=recommendation.notes,
        arguments=arguments,
        config=None
        if config is None
        else _nested(recommendation.table, {name.replace("_", "-"): value for name, value in sorted(config.items())}),
        config_complete=recommendation.config_complete,
        content=content,
    )
    return json.dumps(payload.model_dump(mode="json"), indent=2, ensure_ascii=False)
