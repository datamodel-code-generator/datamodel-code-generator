"""Trial naming: generate candidate configurations up to their model metadata and compare the class names.

A candidate is a complete generation config. It runs generate()'s own pipeline without formatting or output, so its
class names are the names a user gets, and it counts the fields dcg warns it generates without some of their
constraints. ``rank`` orders candidates by the tuned naming benchmark's problem names, with names that carry no
meaning first: rejected candidates last, then fewer numbered or doubled names, fewer other problem names (such as
names over 40 characters), fewer renamed locations against a previous version of the input, and a shorter median
name; only then fewer fields without their constraints. Only candidates that generate exactly the same class names are
then ordered by fewer added options and the option spelling; other ties keep the given order.
"""

from __future__ import annotations

import io
import warnings
from contextlib import redirect_stderr
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from datamodel_code_generator import Error, _collect_model_metadata, enable_parsed_source_cache
from datamodel_code_generator._recommend._metrics import name_metrics, name_records, name_stability
from datamodel_code_generator.parser.base import dropped_constraints_recorded
from datamodel_code_generator.remote_lock import RemoteLockError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path
    from urllib.parse import ParseResult

    from datamodel_code_generator._recommend._metrics import NameMetrics, NameRecord, NameStability, SchemaVocabulary
    from datamodel_code_generator._recommend._rules import OptionValue
    from datamodel_code_generator._shared_types import DefaultPutDict
    from datamodel_code_generator.enums import InputFileType

    TrialInput = Path | str | ParseResult | Mapping[str, Any]

__all__ = ["CandidateOutcome", "TrialCandidate", "rank", "run_trial"]


@dataclass(frozen=True, slots=True)
class TrialCandidate:
    """A labelled generation config, with the options it adds on top of its preset as ``pyproject.toml`` spells them.

    ``preset`` is None for the configuration as set without a preset. A candidate that is not ``selectable`` is
    measured and ranked but never chosen.
    """

    label: str
    config: Any
    preset: str | None
    options: Mapping[str, OptionValue]
    selectable: bool = True


@dataclass(frozen=True, slots=True)
class CandidateOutcome:
    """The class names one candidate generates, or the error that rejects it.

    ``dropped_constraints`` counts the fields dcg warns the candidate generates without some of their constraints.
    """

    candidate: TrialCandidate
    records: tuple[NameRecord, ...]
    metrics: NameMetrics | None
    stability: NameStability | None
    error: str | None
    dropped_constraints: int | None = None


def _score(outcome: CandidateOutcome) -> tuple[int, int, int, int, int, int]:
    """Return the ranking key of a candidate, lower is better: its class names, then its fields without constraints."""
    if (metrics := outcome.metrics) is None:
        return 1, 0, 0, 0, 0, 0
    return (
        0,
        metrics.numbered + metrics.doubled,
        metrics.other_problems,
        0 if outcome.stability is None else outcome.stability.locations.renamed,
        metrics.median_length,
        outcome.dropped_constraints or 0,
    )


def rank(outcomes: Sequence[CandidateOutcome]) -> tuple[CandidateOutcome, ...]:
    """Return the candidates best first.

    Candidates with equal scores keep the given order, except that among candidates that generate exactly the same
    class names at the same locations, fewer added options and then the option spelling come first.
    """
    first_seen: dict[tuple[tuple[str, str], ...], int] = {}
    names = [tuple(sorted((record.path, record.name) for record in outcome.records)) for outcome in outcomes]
    for index, generated in enumerate(names):
        first_seen.setdefault(generated, index)
    order = {
        id(outcome): (
            first_seen[generated],
            len(options := outcome.candidate.options),
            " ".join(f"{name}={value}" for name, value in sorted(options.items())),
        )
        for outcome, generated in zip(outcomes, names, strict=True)
    }
    return tuple(sorted(outcomes, key=lambda outcome: (_score(outcome), order[id(outcome)])))


def _rejection(error: Exception) -> str:
    """Return the one-line reason a candidate's generation failed, naming the exception unless it is dcg's own."""
    message, name = str(error).strip().partition("\n")[0], type(error).__name__
    if isinstance(error, Error):
        return message or name
    return f"{name}: {message}" if message else name


def _run_candidate(  # noqa: PLR0913
    input_: TrialInput,
    candidate: TrialCandidate,
    config: Any,
    *,
    vocabulary: SchemaVocabulary,
    previous_input: Path | None,
    remote_text_cache: DefaultPutDict[str, str],
) -> tuple[CandidateOutcome, InputFileType | None]:
    try:
        with warnings.catch_warnings(), redirect_stderr(io.StringIO()):
            warnings.simplefilter("ignore")
            with dropped_constraints_recorded() as dropped:
                metadata, identities, input_file_type = _collect_model_metadata(
                    input_, config, remote_text_cache=remote_text_cache
                )
            records = name_records(metadata, identities)
            previous = (
                None
                if previous_input is None
                else name_records(
                    *_collect_model_metadata(
                        previous_input,
                        config.model_copy(update={"input_file_type": input_file_type}),
                        remote_text_cache=remote_text_cache,
                    )[:2]
                )
            )
    except RemoteLockError:
        raise
    except Exception as error:  # noqa: BLE001
        return CandidateOutcome(candidate, (), None, None, _rejection(error)), None
    outcome = CandidateOutcome(
        candidate,
        records,
        name_metrics(records, vocabulary),
        None if previous is None else name_stability(previous, records),
        None,
        len(dropped.warnings),
    )
    return outcome, input_file_type


def run_trial(
    input_: TrialInput,
    candidates: Sequence[TrialCandidate],
    *,
    vocabulary: SchemaVocabulary,
    previous_input: Path | None,
    remote_text_cache: DefaultPutDict[str, str],
) -> tuple[tuple[CandidateOutcome, ...], InputFileType | None]:
    """Generate every candidate's class names for the input, in the given order, and return the input type parsed.

    ``vocabulary`` comes from the input's loaded document. With ``previous_input``, every candidate also runs on that
    earlier version of the input and records the classes it renames between them. An input type left to detection
    is detected once, by the first generation that succeeds. The runs print nothing and show no warnings; a candidate
    whose generation fails, with any exception, is rejected with a one-line reason, while a remote lock error
    concerns the input, not a candidate, and propagates. The runs share parsed local documents, and runs that share
    ``remote_text_cache`` fetch each remote document once.
    """
    outcomes: list[CandidateOutcome] = []
    detected: InputFileType | None = None
    restore = enable_parsed_source_cache()
    try:
        for candidate in candidates:
            config = candidate.config
            outcome, input_file_type = _run_candidate(
                input_,
                candidate,
                config if detected is None else config.model_copy(update={"input_file_type": detected}),
                vocabulary=vocabulary,
                previous_input=previous_input,
                remote_text_cache=remote_text_cache,
            )
            outcomes.append(outcome)
            detected = detected or input_file_type
    finally:
        restore()
    return tuple(outcomes), detected
