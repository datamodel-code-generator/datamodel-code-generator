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

    def record(self, frame: FrameType, event: str, value: Any) -> None:
        """Observe real driver boundaries without modifying return values."""
        code = frame.f_code
        local = frame.f_locals
        if event == "return" and code is OpenAPIGenerationSession.__call__.__code__ and value is not None:
            self.parsers.append(weakref.ref(value))
        if event != "call":
            return
        if code is OpenAPIGenerationSession.__call__.__code__:
            self.events.append(f"construct:{local['self']._next_attempt + 1}")
        elif code is OpenAPIParser.parse.__code__:
            self.events.append(f"parse:{local['self'].binding_ledger.attempt_id}")
        elif code is BindingCaptureMixin.dispose.__code__:
            self.events.append(f"dispose:{local['self'].binding_ledger.attempt_id}")
        elif code is OpenAPIGenerationSession.freeze_attempt.__code__:
            parser, results = local["parser"], local["results"]
            attempt = parser.binding_ledger.attempt_id
            self.events.append(f"freeze:{attempt}:{bool(parser.results)}")
            body = results if isinstance(results, str) else "\n".join(result.body for result in results.values())
            self.hashes[attempt] = hashlib.sha256(body.encode()).hexdigest()
            self.graph.extend(weakref.ref(node) for node in parser.binding_ledger._anchors)
        elif code is OpenAPIGenerationSession.accept_attempt.__code__:
            self.events.append(f"accept:{local['attempt_id']}")
        elif code is OpenAPIGenerationSession.discard_attempt.__code__:
            self.events.append(f"discard:{local['parser'].binding_ledger.attempt_id}")
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
    source: Path, config: GenerateConfig, *, artifact_rewrite: tuple[str, str] | None = None
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
        product = session.take_product(artifacts, allow_empty_api=True)
    finally:
        session.close()
        sys.setprofile(previous)
    gc.collect()
    return product, sum(node() is not None for node in (*observer.graph, *observer.parsers))
