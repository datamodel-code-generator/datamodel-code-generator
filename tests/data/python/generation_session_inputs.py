"""Exercise the real capture session through the existing generation driver."""

from __future__ import annotations

import gc
import hashlib
import sys
import weakref
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from datamodel_code_generator import (
    GenerateConfig,
    _CollapseRootModelsRecursionError,
    _prepare_generate_facade_config,
    _run_generation,
)
from datamodel_code_generator._generation_contract import BindingCaptureError
from datamodel_code_generator._openapi_artifacts import ModelArtifact
from datamodel_code_generator._openapi_generation import (
    OpenAPIGenerationSession,
)
from datamodel_code_generator.parser.openapi import OpenAPIParser
from datamodel_code_generator.parser.openapi_contract import BindingCaptureMixin

if TYPE_CHECKING:
    from types import FrameType

    import pytest

    from datamodel_code_generator._openapi_generation import ModelGenerationProduct


class GenerationSessionObserver:
    """Profile completed real calls without replacing parsers, stores, or sessions."""

    def __init__(self) -> None:
        """Keep only traces, digests, and weak object handles."""
        self.events: list[str] = []
        self.parsers: list[weakref.ReferenceType[OpenAPIParser]] = []
        self.graph: list[weakref.ReferenceType[object]] = []
        self.hashes: dict[int, str] = {}
        self.factory_reads = 0

    def record(self, frame: FrameType, event: str, value: Any) -> None:
        """Observe real driver boundaries without modifying return values."""
        code = frame.f_code
        if event == "return" and code is OpenAPIGenerationSession.__call__.__code__ and value is not None:
            self.parsers.append(weakref.ref(value))
        if event != "call":
            return
        if code not in {
            OpenAPIGenerationSession.parser_factory.fget.__code__,
            OpenAPIGenerationSession.__call__.__code__,
            OpenAPIParser.parse.__code__,
            BindingCaptureMixin.dispose.__code__,
            OpenAPIGenerationSession.freeze_attempt.__code__,
            OpenAPIGenerationSession.accept_attempt.__code__,
            OpenAPIGenerationSession.discard_attempt.__code__,
            OpenAPIGenerationSession.close.__code__,
        }:
            return
        local = frame.f_locals
        if code is OpenAPIGenerationSession.parser_factory.fget.__code__:
            self.factory_reads += 1
        if code is OpenAPIGenerationSession.__call__.__code__:
            self.events.append(f"construct:{local['self']._next_attempt + 1}")
        elif code is OpenAPIParser.parse.__code__:
            self.events.append(f"parse:{local['self'].binding_ledger.attempt_id}")
        elif code is BindingCaptureMixin.dispose.__code__:
            self.events.append(f"dispose:{local['self'].binding_ledger.attempt_id}")
        elif code is OpenAPIGenerationSession.freeze_attempt.__code__:
            parser, results = local["parser"], local["results"]
            if not isinstance(parser, BindingCaptureMixin):
                return
            attempt = parser.binding_ledger.attempt_id
            self.events.append(f"freeze:{attempt}:{bool(parser.results)}")
            body = results if isinstance(results, str) else "\n".join(result.body for result in results.values())
            self.hashes[attempt] = hashlib.sha256(body.encode()).hexdigest()
            self.graph.extend(weakref.ref(node) for node in parser.binding_ledger._anchors)
        elif code is OpenAPIGenerationSession.accept_attempt.__code__:
            self.events.append(f"accept:{local['attempt_id']}")
        elif code is OpenAPIGenerationSession.discard_attempt.__code__:
            if isinstance(parser := local["parser"], BindingCaptureMixin):
                self.events.append(f"discard:{parser.binding_ledger.attempt_id}")
        elif code is OpenAPIGenerationSession.close.__code__:
            self.events.append("close")


def run_generation_session(
    source: Path, *, failure: str = "", monkeypatch: pytest.MonkeyPatch | None = None, output: Path | None = None
) -> tuple[dict[str, object], int]:
    """Use the real accepted batch while preserving the independent S01 trace oracle."""
    observer = GenerationSessionObserver()
    config = _prepare_generate_facade_config(
        GenerateConfig(
            input_file_type="openapi", formatters=[], disable_timestamp=True, collapse_root_models=True, output=output
        ).model_copy(update={"repair_invalid_dotted_stdout": True})
    )
    session = OpenAPIGenerationSession(
        output=Path("models.py"), model_package="models", root_selector_document=source.as_uri()
    )
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


def observe_api_session(
    source: Path, config: GenerateConfig, *, failure: str = "", monkeypatch: pytest.MonkeyPatch | None = None
) -> tuple[Any, dict[str, object]]:
    """Observe real API factory selection, acceptance, and disposal against the S02 oracle."""
    observer = GenerationSessionObserver()
    session = OpenAPIGenerationSession(
        output=Path("models.py"), model_package="models", root_selector_document=source.as_uri()
    )
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


def compare_api_session(source: Path, config: GenerateConfig) -> dict[str, object]:
    """Compare ordinary API generation with real capture without replacing engine methods."""
    from tests.data.python.generation_observer import GenerationObserver

    session = OpenAPIGenerationSession(
        output=Path("models.py"), model_package="models", root_selector_document=source.as_uri()
    )
    lifetime = GenerationSessionObserver()
    outputs = []
    calls = []
    previous = sys.getprofile()
    try:
        for capture in (None, session):
            observer = GenerationObserver()

            def observe(frame: FrameType, event: str, value: Any) -> None:
                observer.record(frame, event, value)
                if capture is not None:
                    lifetime.record(frame, event, value)

            sys.setprofile(observe)
            outputs.append(
                _run_generation(
                    source, _prepare_generate_facade_config(config), Path.cwd(), use_output_cwd=False, capture=capture
                )
            )
            calls.append(observer.calls)
    finally:
        session.close()
        sys.setprofile(previous)
    gc.collect()
    return {
        "outputs_equal": outputs[0] == outputs[1],
        "engine_calls_equal": calls[0] == calls[1],
        "factory_reads": lifetime.factory_reads,
        "retained_parsers": sum(parser() is not None for parser in lifetime.parsers),
    }


def _inject_session_failure(monkeypatch: pytest.MonkeyPatch, failure: str, source: Path) -> None:
    """Inject only exceptional boundaries while preserving the real session and frozen batches."""
    ordinary_parse = OpenAPIParser.parse
    ordinary_dispose = OpenAPIParser.dispose
    freeze = OpenAPIGenerationSession.freeze_attempt
    accept = OpenAPIGenerationSession.accept_attempt
    discard = OpenAPIGenerationSession.discard_attempt
    close = OpenAPIGenerationSession.close

    def failed_parse(self: BindingCaptureMixin, *args: Any, **kwargs: Any) -> Any:
        attempt = self.binding_ledger.attempt_id
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
            case "repair_capture" if attempt == 2:
                error = BindingCaptureError("record failed")
                self.binding_ledger.remember_failure(error)
                raise error
        return ordinary_parse(self, *args, **kwargs)

    def failed_dispose(self: BindingCaptureMixin) -> None:
        ordinary_dispose(self)
        attempt = self.binding_ledger.attempt_id
        if attempt == 1 and failure in {"source_change", "discard"}:
            with source.open("a", encoding="utf-8") as stream:
                stream.write("\nx-test-repair: changed\n")
        if failure in {"dispose", "parse_dispose", "freeze_dispose"} or (failure == "repair_dispose" and attempt == 2):
            message = "dispose failed"
            raise RuntimeError(message)

    def failed_freeze(self: OpenAPIGenerationSession, parser: BindingCaptureMixin, results: Any) -> Any:
        if failure in {"freeze", "freeze_dispose"} or (
            failure == "repair_freeze" and parser.binding_ledger.attempt_id == 2
        ):
            error = BindingCaptureError("freeze failed")
            parser.binding_ledger.remember_failure(error)
            raise error
        return freeze(self, parser, results)

    def failed_accept(self: OpenAPIGenerationSession, attempt_id: Any) -> None:
        if failure == "accept":
            message = "accept failed"
            raise BindingCaptureError(message)
        accept(self, attempt_id)

    def failed_discard(self: OpenAPIGenerationSession, parser: BindingCaptureMixin) -> None:
        if failure == "discard":
            message = "discard failed"
            raise RuntimeError(message)
        discard(self, parser)

    def failed_close(self: OpenAPIGenerationSession) -> None:
        close(self)
        if failure in {"close", "parse_close"}:
            message = "close failed"
            raise RuntimeError(message)

    monkeypatch.setattr(OpenAPIParser, "parse", failed_parse)
    monkeypatch.setattr(OpenAPIParser, "dispose", failed_dispose)
    monkeypatch.setattr(OpenAPIGenerationSession, "freeze_attempt", failed_freeze)
    monkeypatch.setattr(OpenAPIGenerationSession, "accept_attempt", failed_accept)
    monkeypatch.setattr(OpenAPIGenerationSession, "discard_attempt", failed_discard)
    monkeypatch.setattr(OpenAPIGenerationSession, "close", failed_close)


def generate_product(
    source: Path,
    config: GenerateConfig,
    *,
    artifact_rewrite: tuple[str, str] | None = None,
    artifact_failure: str = "",
) -> tuple[ModelGenerationProduct, int]:
    """Finish ordinary emission, then transfer real immutable values and borrowed sources."""
    observer = GenerationSessionObserver()
    session = OpenAPIGenerationSession(
        output=Path("models.py"), model_package="models", root_selector_document=source.as_uri()
    )
    previous = sys.getprofile()
    sys.setprofile(observer.record)
    try:
        result = _run_generation(
            source, _prepare_generate_facade_config(config), Path.cwd(), use_output_cwd=False, capture=session
        )
        artifacts = (
            (ModelArtifact(("models.py",), result.encode()),)
            if isinstance(result, str)
            else tuple(ModelArtifact(key, value.encode()) for key, value in result.items())
            if isinstance(result, dict)
            else ()
        )
        if artifact_rewrite is not None:
            original, replacement = artifact_rewrite
            artifacts = tuple(
                replace(artifact, content=artifact.content.decode().replace(original, replacement).encode())
                for artifact in artifacts
            )
        match artifact_failure:
            case "duplicate":
                artifacts = (*artifacts, artifacts[0])
            case "missing":
                artifacts = ()
            case "encoding":
                artifacts = (replace(artifacts[0], content=b"\xff"),)
            case "syntax":
                artifacts = (replace(artifacts[0], content=b'"""'),)
            case "unknown_encoding":
                artifacts = (replace(artifacts[0], encoding="unknown-artifact-encoding"),)
        product = session.take_product(artifacts, allow_empty_api=True)
    finally:
        session.close()
        sys.setprofile(previous)
    gc.collect()
    return product, sum(node() is not None for node in (*observer.graph, *observer.parsers))


def _exercise_session_protocol(source: Path, case: str, observer: GenerationSessionObserver) -> tuple[list[tuple[str, str]], int | None]:
    """Exercise invalid ownership and transfer requests around one real driver execution."""
    from datamodel_code_generator._generation_contract import AttemptId
    from datamodel_code_generator.config import OpenAPIParserConfig

    session = OpenAPIGenerationSession(
        output=Path("models.py"), model_package="models", root_selector_document=source.as_uri()
    )
    other = OpenAPIGenerationSession(
        output=Path("models.py"), model_package="models", root_selector_document=source.as_uri()
    )
    parsers = []
    errors = []
    accepted = None
    previous = sys.getprofile()

    def record(frame: FrameType, event: str, value: Any) -> None:
        observer.record(frame, event, value)
        if event == "return" and frame.f_code is OpenAPIGenerationSession.__call__.__code__ and value is not None:
            parsers.append(value)

    sys.setprofile(record)
    try:
        if case in {"batch_before_generation", "lease_before_generation", "product_before_generation"}:
            try:
                if case == "batch_before_generation":
                    session.take_accepted_batch()
                elif case == "lease_before_generation":
                    session.source_lease
                else:
                    session.take_product((), allow_empty_api=True)
            except RuntimeError as error:
                errors.append((type(error).__name__, str(error)))
        if case == "unused_attempt":
            session(source=source, config=OpenAPIParserConfig(formatters=[]))
        result = _run_generation(
            source,
            _prepare_generate_facade_config(GenerateConfig(input_file_type="openapi", formatters=[], disable_timestamp=True)),
            Path.cwd(),
            use_output_cwd=False,
            capture=session,
        )
        if not isinstance(result, str):
            raise TypeError(type(result))
        match case:
            case "closed_session":
                session.close()
                session.take_accepted_batch()
            case "duplicate_transfer":
                accepted = session.take_accepted_batch().attempt
                session.take_accepted_batch()
            case "discard_accepted":
                session.discard_attempt(parsers[-1])
                session.discard_attempt(parsers[-1])
                session.source_lease
            case "foreign_freeze":
                foreign = other(source=source, config=OpenAPIParserConfig(formatters=[]))
                session.freeze_attempt(foreign, result)
            case "ordinary_freeze":
                foreign = OpenAPIParser(source, config=OpenAPIParserConfig(formatters=[]))
                parsers.append(foreign)
                session.freeze_attempt(foreign, result)
            case "foreign_discard":
                foreign = other(source=source, config=OpenAPIParserConfig(formatters=[]))
                session.discard_attempt(foreign)
                accepted = session.take_accepted_batch().attempt
            case "ordinary_discard":
                foreign = OpenAPIParser(source, config=OpenAPIParserConfig(formatters=[]))
                parsers.append(foreign)
                session.discard_attempt(foreign)
                accepted = session.take_accepted_batch().attempt
            case "wrong_accept":
                session.accept_attempt(AttemptId(99))
            case "product_after_transfer":
                accepted = session.take_accepted_batch().attempt
                session.take_product((ModelArtifact(("models.py",), result.encode()),), allow_empty_api=True)
            case _:
                accepted = session.take_accepted_batch().attempt
    except RuntimeError as error:
        errors.append((type(error).__name__, str(error)))
    finally:
        sys.setprofile(previous)
        for parser in parsers:
            parser.dispose()
        parsers.clear()
        foreign = None
        parser = None
        session.close()
        other.close()
    return errors, accepted


def session_protocol_failure(source: Path, case: str) -> dict[str, object]:
    """Measure retained graphs after the complete failing ownership call returns."""
    observer = GenerationSessionObserver()
    errors, accepted = _exercise_session_protocol(source, case, observer)
    gc.collect()
    return {
        "errors": errors,
        "accepted": accepted,
        "retained_graph": sum(node() is not None for node in observer.graph),
        "retained_parsers": sum(node() is not None for node in observer.parsers),
    }
