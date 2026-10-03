"""Exercise the target generation session through the existing generation driver."""

from __future__ import annotations

import gc
import hashlib
import sys
import weakref
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from datamodel_code_generator import (
    GenerateConfig,
    OpenAPIScope,
    _CollapseRootModelsRecursionError,
    _prepare_generate_facade_config,
    _run_generation,
)
from datamodel_code_generator._generation_contract import BindingCaptureError
from datamodel_code_generator._openapi_generation import TargetGenerationSession
from datamodel_code_generator.parser.openapi import OpenAPIParser

if TYPE_CHECKING:
    from types import FrameType


class GenerationSessionObserver:
    """Profile completed real calls without replacing parsers, stores, or sessions."""

    def __init__(self, session: type[TargetGenerationSession] = TargetGenerationSession) -> None:
        """Keep only traces, digests, and weak object handles for one session type."""
        self.session = session
        self.events: list[str] = []
        self.parsers: list[weakref.ReferenceType[OpenAPIParser]] = []
        self.graph: list[weakref.ReferenceType[object]] = []
        self.hashes: dict[int, str] = {}
        self.factory_reads = 0
        self.dispose_codes: set[object] = set()

    def record(self, frame: FrameType, event: str, value: Any) -> None:
        """Observe real driver boundaries without modifying return values."""
        code, session = frame.f_code, self.session
        if event == "return" and code is session.__call__.__code__ and value is not None:
            self.parsers.append(weakref.ref(value))
            self.dispose_codes.add(type(value).dispose.__code__)
        if event != "call":
            return
        if code in self.dispose_codes:
            self.events.append(f"dispose:{frame.f_locals['self'].attempt}")
            return
        if code not in {
            session.parser_factory.fget.__code__,
            session.__call__.__code__,
            OpenAPIParser.parse.__code__,
            session.freeze_attempt.__code__,
            session.accept_attempt.__code__,
            session.discard_attempt.__code__,
            session.close.__code__,
        }:
            return
        local = frame.f_locals
        if code is session.parser_factory.fget.__code__:
            self.factory_reads += 1
        if code is session.__call__.__code__:
            self.events.append(f"construct:{len(self.parsers) + 1}")
        elif code is OpenAPIParser.parse.__code__:
            self.events.append(f"parse:{local['self'].attempt}")
        elif code is session.freeze_attempt.__code__:
            parser, results = local["parser"], local["results"]
            self.events.append(f"freeze:{parser.attempt}:{bool(parser.results)}")
            body = results if isinstance(results, str) else "\n".join(result.body for result in results.values())
            self.hashes[parser.attempt] = hashlib.sha256(body.encode()).hexdigest()
            self.graph.extend(
                weakref.ref(model) for _, models, _ in getattr(parser, "module_outputs", ()) for model in models
            )
        elif code is session.accept_attempt.__code__:
            self.events.append(f"accept:{local['attempt_id']}")
        elif code is session.discard_attempt.__code__:
            self.events.append(f"discard:{local['parser'].attempt}")
        elif code is session.close.__code__:
            self.events.append("close")


@dataclass(frozen=True)
class _Accepted:
    attempt: int


class OrdinaryCaptureSession(TargetGenerationSession):
    """Capture attempts of ordinary OpenAPI parsing, which only the released driver contract sees."""

    def __init__(self, **kwargs: Any) -> None:
        """Count attempts without binding operations."""
        super().__init__(**kwargs)
        self.attempts = 0
        self.accepted = 0

    def __call__(self, *, source: Any, config: Any) -> OpenAPIParser:
        """Construct an ordinary parser for the next attempt."""
        self.attempts += 1
        parser = OpenAPIParser(source, config=config)
        parser.attempt = self.attempts
        return parser

    def freeze_attempt(self, parser: Any, results: Any) -> Any:
        """Remember the attempt without projecting its graph."""
        self.accepted = parser.attempt
        return parser.attempt

    @staticmethod
    def discard_attempt(parser: Any) -> None:
        """Hold no attempt state to release."""

    def take_accepted_batch(self) -> Any:
        """Return the accepted attempt."""
        return _Accepted(self.accepted)


def _session(source: Path, session: type[TargetGenerationSession] = TargetGenerationSession) -> TargetGenerationSession:
    return session(output=Path("models.py"), model_package="models", root_selector_document=source.as_uri())


@pytest.mark.abnormal_path("traces the session lifecycle while a test injects parse, bind, or release faults")
def run_generation_session(
    source: Path,
    *,
    failure: str = "",
    monkeypatch: pytest.MonkeyPatch | None = None,
    output: Path | None = None,
    api_scope: bool = True,
) -> tuple[dict[str, object], int]:
    """Use the real accepted batch while preserving the independent S01 trace oracle.

    Without the API scope, an ordinary capture session drives the released driver contract instead of a target.
    """
    session_type = TargetGenerationSession if api_scope else OrdinaryCaptureSession
    observer = GenerationSessionObserver(session_type)
    config = _prepare_generate_facade_config(
        GenerateConfig(
            input_file_type="openapi",
            formatters=[],
            disable_timestamp=True,
            collapse_root_models=True,
            output=output,
            openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api] if api_scope else None,
        ).model_copy(update={"repair_invalid_dotted_stdout": True})
    )
    session = _session(source, session_type)
    if failure and monkeypatch is not None:
        _inject_session_failure(monkeypatch, failure, source)
    previous = sys.getprofile()
    sys.setprofile(observer.record)
    result = None
    accepted = None
    error = None
    try:
        result = _run_generation(source, config, Path.cwd(), use_output_cwd=False, capture=session)
        batch = session.take_accepted_batch()
        accepted = (batch.attempt, observer.hashes[batch.attempt])
        session.close()
    except (RuntimeError, OSError) as exc:
        error = [type(exc).__name__, str(exc) if not isinstance(exc, OSError) else str(exc.errno)]
    finally:
        sys.setprofile(previous)
        with suppress(RuntimeError):
            session.close()
    gc.collect()
    observation = {
        "events": observer.events,
        "error": error,
        "batch": accepted,
        "result_sha256": hashlib.sha256(result.encode()).hexdigest()
        if isinstance(result, str)
        else {"/".join(key): hashlib.sha256(body.encode()).hexdigest() for key, body in result.items()}
        if isinstance(result, dict)
        else None,
        "retained_parsers": sum(parser() is not None for parser in observer.parsers),
    }
    return observation, sum(node() is not None for node in observer.graph)


@pytest.mark.abnormal_path("traces the API factory under an injected collapse retry or a removed capability")
def observe_api_session(
    source: Path, config: GenerateConfig, *, failure: str = "", monkeypatch: pytest.MonkeyPatch | None = None
) -> tuple[Any, dict[str, object]]:
    """Observe real API factory selection, acceptance, and disposal against the S02 oracle."""
    observer = GenerationSessionObserver()
    session = _session(source)
    if failure and monkeypatch is not None:
        _inject_session_failure(monkeypatch, failure, source)
    previous = sys.getprofile()
    sys.setprofile(observer.record)
    try:
        result = _run_generation(
            source, _prepare_generate_facade_config(config), Path.cwd(), use_output_cwd=False, capture=session
        )
        accepted = session.take_accepted_batch().attempt
    finally:
        session.close()
        sys.setprofile(previous)
    gc.collect()
    return result, {
        "empty_result": None,
        "factory_reads": observer.factory_reads,
        "accepted_attempt": accepted,
        "retained_parsers": sum(parser() is not None for parser in observer.parsers),
    }


@pytest.mark.abnormal_path("replaces session and parser boundaries to inject faults no input can cause")
def _inject_session_failure(monkeypatch: pytest.MonkeyPatch, failure: str, source: Path) -> None:
    """Inject only exceptional boundaries while preserving the real session and bound batches."""
    ordinary_parse = OpenAPIParser.parse
    ordinary_dispose = OpenAPIParser.dispose
    freeze = TargetGenerationSession.freeze_attempt
    accept = TargetGenerationSession.accept_attempt
    discard = TargetGenerationSession.discard_attempt
    close = TargetGenerationSession.close

    def failed_parse(self: OpenAPIParser, *args: Any, **kwargs: Any) -> Any:
        attempt = self.attempt
        match failure:
            case "collapse_always" | "collapse_once" if failure == "collapse_always" or attempt == 1:
                message = "collapse failed"
                raise _CollapseRootModelsRecursionError(message)
            case "parse" | "parse_dispose" | "parse_close":
                message = "parse failed"
                raise RuntimeError(message)
            case "repair_parse" if attempt == 2:
                message = "repair parse failed"
                raise RuntimeError(message)
        return ordinary_parse(self, *args, **kwargs)

    def failed_dispose(self: OpenAPIParser) -> None:
        ordinary_dispose(self)
        attempt = self.attempt
        if attempt == 1 and failure in {"source_change", "discard"}:
            with source.open("a", encoding="utf-8") as stream:
                stream.write("\nx-test-repair: changed\n")
        if failure in {"dispose", "parse_dispose", "freeze_dispose"} or (failure == "repair_dispose" and attempt == 2):
            message = "dispose failed"
            raise RuntimeError(message)

    def failed_freeze(self: TargetGenerationSession, parser: OpenAPIParser, results: Any) -> Any:
        if failure in {"freeze", "freeze_dispose"} or (failure == "repair_freeze" and parser.attempt == 2):
            message = "freeze failed"
            raise BindingCaptureError(message)
        return freeze(self, parser, results)

    def failed_accept(self: TargetGenerationSession, attempt_id: Any) -> None:
        if failure == "accept":
            message = "accept failed"
            raise BindingCaptureError(message)
        accept(self, attempt_id)

    def failed_discard(parser: OpenAPIParser) -> None:
        if failure == "discard":
            message = "discard failed"
            raise RuntimeError(message)
        discard(parser)

    def failed_close(self: TargetGenerationSession) -> None:
        close(self)
        if failure in {"close", "parse_close"}:
            message = "close failed"
            raise RuntimeError(message)

    monkeypatch.setattr(OpenAPIParser, "parse", failed_parse)
    monkeypatch.setattr(OpenAPIParser, "dispose", failed_dispose)
    monkeypatch.setattr(TargetGenerationSession, "freeze_attempt", failed_freeze)
    monkeypatch.setattr(TargetGenerationSession, "accept_attempt", failed_accept)
    monkeypatch.setattr(TargetGenerationSession, "discard_attempt", staticmethod(failed_discard))
    monkeypatch.setattr(TargetGenerationSession, "close", failed_close)
