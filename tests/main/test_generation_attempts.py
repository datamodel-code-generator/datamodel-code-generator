"""Observe shared-driver acceptance with real parsers and a bounded value consumer."""

from __future__ import annotations

import gc
import hashlib
import json
import operator
import shutil
import weakref
from pathlib import Path
from typing import Any

import pytest

from datamodel_code_generator import (
    GenerateConfig,
    _CollapseRootModelsRecursionError,
    _prepare_generate_facade_config,
    _run_generation,
)
from datamodel_code_generator._generation_contract import AttemptId, BindingCaptureError
from datamodel_code_generator.parser.openapi import OpenAPIParser
from tests.conftest import assert_output

DATA = Path(__file__).parents[1] / "data"
EXPECTED = DATA / "expected/main/generation_platform"
CASES = json.loads((DATA / "generation_platform/attempts.json").read_text())


class AttemptParser(OpenAPIParser):
    """Real parser with fault injection confined to exceptional test cases."""

    def __init__(self, session: AttemptConsumer, **kwargs: Any) -> None:
        """Attach attempt state before the ordinary constructor runs."""
        self.session = session
        self.attempt_id = AttemptId(len(session.parsers) + 1)
        session.parsers.append(weakref.ref(self))
        session.events.append(f"construct:{self.attempt_id}")
        super().__init__(**kwargs)

    def parse(self, *args: Any, **kwargs: Any) -> Any:
        """Run real parsing unless the case deliberately exercises a failure."""
        session = self.session
        session.events.append(f"parse:{self.attempt_id}")
        match session.failure:
            case "collapse_always":
                msg = "collapse failed"
                raise _CollapseRootModelsRecursionError(msg)
            case "collapse_once" if self.attempt_id == 1:
                msg = "collapse failed"
                raise _CollapseRootModelsRecursionError(msg)
            case "parse" | "parse_dispose" | "parse_close":
                msg = "parse failed"
                raise RuntimeError(msg)
            case "repair_parse" if self.attempt_id == 2:
                msg = "repair parse failed"
                raise RuntimeError(msg)
            case "repair_capture" if self.attempt_id == 2:
                session.fatal = BindingCaptureError("record failed")
                raise session.fatal
        return super().parse(*args, **kwargs)

    def dispose(self) -> None:
        """Observe real graph disposal before injecting a release failure."""
        self.session.events.append(f"dispose:{self.attempt_id}")
        super().dispose()
        if self.attempt_id == 1 and self.session.failure in {"source_change", "discard"}:
            with self.session.source_path.open("a") as source:
                source.write("\nx-test-repair: changed\n")
        match self.session.failure:
            case "dispose" | "parse_dispose" | "freeze_dispose":
                msg = "dispose failed"
                raise RuntimeError(msg)
            case "repair_dispose" if self.attempt_id == 2:
                msg = "dispose failed"
                raise RuntimeError(msg)


class AttemptConsumer:
    """A test-only value consumer, not a finished operation-binding implementation."""

    def __init__(self, failure: str) -> None:
        """Keep weak parser handles and an immutable provisional value only."""
        self.source_path = Path()
        self.failure = failure
        self.parsers: list[weakref.ReferenceType[AttemptParser]] = []
        self.events: list[str] = []
        self.fatal: BindingCaptureError | None = None
        self.candidate: tuple[int, str] | None = None
        self.accepted: tuple[int, str] | None = None

    def parser_factory(self, **kwargs: Any) -> AttemptParser:
        """Use one factory for initial, collapse, and stdout repair attempts."""
        return AttemptParser(self, **kwargs)

    def freeze_attempt(self, parser: AttemptParser, results: Any) -> AttemptId:
        """Read completed result bytes while the original graph is still live."""
        self.events.append(f"freeze:{parser.attempt_id}:{bool(parser.results)}")
        if self.failure in {"freeze", "freeze_dispose"} or (self.failure == "repair_freeze" and parser.attempt_id == 2):
            self.fatal = BindingCaptureError("freeze failed")
            raise self.fatal
        bodies = results if isinstance(results, str) else "\n".join(result.body for result in results.values())
        self.candidate = (parser.attempt_id, hashlib.sha256(bodies.encode()).hexdigest())
        return parser.attempt_id

    def accept_attempt(self, attempt_id: AttemptId) -> None:
        """Save only the selected immutable candidate."""
        self.events.append(f"accept:{attempt_id}")
        if self.failure == "accept":
            msg = "accept failed"
            raise BindingCaptureError(msg)
        self.accepted = self.candidate

    def discard_attempt(self, parser: AttemptParser) -> None:
        """Record rejected attempts without changing the earlier candidate."""
        self.events.append(f"discard:{parser.attempt_id}")
        if self.failure == "discard":
            msg = "discard failed"
            raise RuntimeError(msg)

    def raise_if_failed(self) -> None:
        """Re-raise a capture failure suppressed by ordinary repair handling."""
        if self.fatal is not None:
            raise self.fatal

    def take_accepted_batch(self) -> tuple[int, str] | None:
        """Return immutable observations without retaining parser state."""
        return self.accepted

    def close(self) -> None:
        """Release candidate resources while keeping transferred values usable."""
        self.events.append("close")
        self.candidate = None
        self.fatal = None
        if self.failure in {"close", "parse_close"}:
            msg = "close failed"
            raise RuntimeError(msg)


@pytest.mark.parametrize("case", CASES, ids=operator.itemgetter("name"))
def test_generation_attempt_lifetime(case: dict[str, str], tmp_path: Path) -> None:
    """Preserve accepted bytes, retry decisions, primary exceptions, and cleanup."""
    consumer = AttemptConsumer(case.get("failure", ""))
    config = _prepare_generate_facade_config(
        GenerateConfig(
            input_file_type="openapi",
            formatters=[],
            disable_timestamp=True,
            collapse_root_models=True,
        ).model_copy(update={"repair_invalid_dotted_stdout": True})
    )
    if consumer.failure == "emit":
        blocked = tmp_path / "blocked"
        blocked.write_text("not a directory")
        config = config.model_copy(update={"output": blocked / "models.py"})
    source_path = (DATA / "generation_platform" / case["input"]).resolve()
    if consumer.failure in {"source_change", "discard"}:
        copied_source = tmp_path / source_path.name
        shutil.copyfile(source_path, copied_source)
        source_path = copied_source
    consumer.source_path = source_path
    error = None
    result = None
    batch = None
    try:
        result = _run_generation(
            source_path,
            config,
            Path.cwd(),
            use_output_cwd=False,
            capture=consumer,
        )
        batch = consumer.take_accepted_batch()
        consumer.close()
    except (RuntimeError, OSError) as exc:
        error = [type(exc).__name__, str(exc) if not isinstance(exc, OSError) else str(exc.errno)]
    gc.collect()
    observation = {
        "events": consumer.events,
        "error": error,
        "batch": batch,
        "result_sha256": (
            hashlib.sha256(result.encode()).hexdigest()
            if isinstance(result, str)
            else {"/".join(key): hashlib.sha256(body.encode()).hexdigest() for key, body in result.items()}
            if isinstance(result, dict)
            else None
        ),
        "retained_parsers": sum(parser() is not None for parser in consumer.parsers),
    }
    assert_output(json.dumps(observation, indent=2) + "\n", EXPECTED / f"attempt_{case['name']}.txt")
