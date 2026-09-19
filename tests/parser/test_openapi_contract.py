"""Exercise capture against real generation and independent source/output fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datamodel_code_generator import OpenAPIScope
from datamodel_code_generator._generation_contract import AttemptId, BindingCaptureError, SourceLocation
from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser, ContractOpenAPIParser
from tests.conftest import assert_output

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/binding"


@pytest.mark.parametrize("api", [False, True])
def test_actual_reference_capture(api: bool) -> None:
    """Preserve model bytes while linking actual recursive resolver results."""
    parser_type = ContractApiOpenAPIParser if api else ContractOpenAPIParser
    parser = parser_type(
        SOURCE / "resolver.json",
        attempt_id=AttemptId(7),
        openapi_scopes=[OpenAPIScope.Api if api else OpenAPIScope.Schemas],
        formatters=[],
    )
    lease = parser.source_lease
    try:
        assert_output(parser.parse(), EXPECTED / "resolver.py")
        document = lease.documents()[0]
        root = lease.borrow(SourceLocation(document.id, "", "declaration"))
        reference = parser.model_resolver.references["resolver.json#/components/schemas/Pet"]
        events = parser.binding_resolver.resolutions
        by_sequence = {event.sequence: event for event in events}
        assert_output(
            json.dumps(
                {
                    "document_uris": [entry.uri for entry in lease.documents()],
                    "borrowed_root": root is parser.raw_obj,
                    "borrowed_response": lease.borrow(
                        SourceLocation(document.id, "/paths/~1pets/get/responses/404/description", "declaration")
                    )
                    == "missing",
                    "repeated_identity": lease.document_id(document.uri) == document.id,
                    "canonical_results": all(
                        event.output == "resolver.json#/components/schemas/Pet" for event in events
                    ),
                    "shared_reference": all(
                        event.reference == parser.binding_ledger.identity(reference)
                        for event in events
                        if event.operation == "add_ref"
                    ),
                    "nested_calls": any(
                        event.parent is not None and by_sequence[event.parent].operation == "add_ref"
                        for event in events
                    ),
                    "attempt": parser.binding_ledger.attempt_id,
                },
                indent=2,
            )
            + "\n",
            EXPECTED / "resolver-state.txt",
        )
    finally:
        parser.dispose()
        lease.close()
    with pytest.raises(BindingCaptureError, match="Binding ledger is closed"):
        parser.binding_ledger.identity(reference)
    with pytest.raises(RuntimeError, match="Source lease is closed"):
        lease.documents()
    lease.close()


@pytest.mark.parametrize(
    "case", ["external", "traversal", "media-32", "resources", "local-roots", "anchors", "typed-reuse", "method-case"]
)
def test_api_capture_engine_calls(case: str) -> None:
    """Keep original resolver, loader, validation and getter counts unchanged."""
    import sys

    from datamodel_code_generator.parser.openapi_scope import ApiOpenAPIParser
    from tests.data.python.binding_engine_observer import BindingEngineObserver

    source = DATA / "generation_platform/api_scope" / f"{case}.json"
    ordinary = ApiOpenAPIParser(source, openapi_scopes=[OpenAPIScope.Api], formatters=[])
    captured = ContractApiOpenAPIParser(
        source, attempt_id=AttemptId(1), openapi_scopes=[OpenAPIScope.Api], formatters=[]
    )
    outputs = []
    counts = []
    previous = sys.getprofile()
    try:
        for parser in (ordinary, captured):
            observer = BindingEngineObserver()
            sys.setprofile(observer.record)
            try:
                outputs.append(parser.parse())
            finally:
                sys.setprofile(previous)
            counts.append(observer.calls)
        assert_output(
            json.dumps(
                {"identical_model_bytes": outputs[0] == outputs[1], "identical_engine_calls": counts[0] == counts[1]},
                indent=2,
            )
            + "\n",
            EXPECTED / "engine-parity.txt",
        )
    finally:
        ordinary.dispose()
        captured.dispose()
        captured.source_lease.close()


def test_legacy_effective_operation_capture() -> None:
    """Retain effective inherited values and every actual body/status/media return."""
    parser = ContractOpenAPIParser(
        SOURCE / "legacy-operations.json",
        attempt_id=AttemptId(1),
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Paths],
        allow_responses_without_content=True,
        formatters=[],
    )
    try:
        assert_output(parser.parse(), EXPECTED / "resolver.py")
        assert_output(
            json.dumps(
                {
                    "origins": [operation.origin_state for operation in parser.legacy_operations],
                    "original_paths": [
                        [candidate.declaration.tokens for candidate in operation.candidates]
                        for operation in parser.legacy_operations
                    ],
                    "request_media": [list(item.types) for item in parser.request_types],
                    "response_media": [
                        [(status, list(media)) for status, media in item.types.items()]
                        for item in parser.response_types
                    ],
                    "security_is_borrowed": parser.legacy_operations[0].effective["security"]
                    is parser.raw_obj["security"],
                    "types_have_owner": all(
                        item.operation is parser.legacy_operations[0] for item in parser.request_types
                    ),
                },
                indent=2,
            )
            + "\n",
            EXPECTED / "legacy-operations.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("case", ["candidates", "ambiguous"])
def test_legacy_original_operation_candidates(case: str) -> None:
    """Reject ambiguous original occurrences without selecting by traversal order."""
    parser = ContractOpenAPIParser(
        SOURCE / f"legacy-{case}.yaml",
        attempt_id=AttemptId(1),
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Paths],
        openapi_include_paths=["/chosen"] if case == "candidates" else None,
        formatters=[],
    )
    try:
        parser.parse()
        assert_output(
            json.dumps([operation.origin_state for operation in parser.legacy_operations]) + "\n",
            EXPECTED / ("legacy-known.txt" if case == "candidates" else "legacy-ambiguous.txt"),
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


def test_source_lease_pointer_boundaries() -> None:
    """Read array occurrences and reject bad pointer shapes after actual generation."""
    parser = ContractApiOpenAPIParser(
        SOURCE / "resolver.json", attempt_id=AttemptId(1), openapi_scopes=[OpenAPIScope.Api], formatters=[]
    )
    lease = parser.source_lease
    try:
        assert_output(parser.parse(), EXPECTED / "resolver.py")
        document = lease.documents()[0]
        assert_output(
            json.dumps(lease.borrow(SourceLocation(document.id, "/components/schemas/Pet/required/0", "schema")))
            + "\n",
            EXPECTED / "source-required.txt",
        )
        with pytest.raises(BindingCaptureError, match="plain JSON pointer"):
            lease.borrow(SourceLocation(document.id, "#/components", "schema"))
        with pytest.raises(BindingCaptureError, match="does not identify an observed node"):
            lease.borrow(SourceLocation(document.id, "/openapi/child", "schema"))
        with pytest.raises(BindingCaptureError, match="does not identify an observed node"):
            lease.borrow(SourceLocation(document.id, "/components/schemas/Pet/required/01", "schema"))
        frame = parser.binding_frames.schemas[0]
    finally:
        parser.dispose()
        lease.close()
    with pytest.raises(BindingCaptureError, match="Declaration capture is closed"):
        parser.binding_frames.append(frame)


@pytest.mark.parametrize("failure", ["capture", "unexpected"])
def test_capture_failure_latches_before_propagation(failure: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep first observer failure distinct from ordinary parser exceptions."""
    parser = ContractApiOpenAPIParser(
        SOURCE / "resolver.json", attempt_id=AttemptId(1), openapi_scopes=[OpenAPIScope.Api], formatters=[]
    )
    error_type = BindingCaptureError if failure == "capture" else ValueError

    def fail_registration(_uri: str, _raw: object) -> None:
        message = "injected capture failure"
        raise error_type(message)

    monkeypatch.setattr(parser.source_lease, "register", fail_registration)
    try:
        with pytest.raises(BindingCaptureError) as first:
            parser.parse()
        with pytest.raises(BindingCaptureError):
            parser.parse()
        assert_output(
            json.dumps({
                "latched_first": parser.binding_ledger.failure is first.value,
                "cause": type(first.value.__cause__).__name__,
            })
            + "\n",
            EXPECTED / f"failure-{failure}.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()
