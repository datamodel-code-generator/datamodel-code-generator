"""Probe capture guards that no public input reaches, by driving the capture parser directly.

The client binding reports in tests/api_generation/test_client_bindings.py cover capture through generated
packages. What remains here injects faults or calls closed capture state, and each guard it reaches is a candidate
for removal once an independent review confirms that production callers never reach it.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import OpenAPIScope
from datamodel_code_generator._generation_contract import (
    AttemptId,
    BindingCaptureError,
    SourceDocumentId,
    SourceLocation,
)
from datamodel_code_generator.config import OpenAPIParserConfig
from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser, ContractOpenAPIParser
from tests.conftest import assert_output

if TYPE_CHECKING:
    from typing import Literal

    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.parser.openapi_contract_store import RootCollapse
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/binding"


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


@pytest.mark.parametrize(("document", "pointer"), json.loads((SOURCE / "invalid-source-locations.json").read_text()))
def test_source_lease_rejects_unobserved_locations(document: int, pointer: str) -> None:
    """Reject invalid source identities and lookups after real model generation."""
    parser = ContractApiOpenAPIParser(
        SOURCE / "resolver.json", attempt_id=AttemptId(1), openapi_scopes=[OpenAPIScope.Api], formatters=[]
    )
    try:
        assert_output(parser.parse(), EXPECTED / "resolver.py")
        with pytest.raises(BindingCaptureError, match=r"^Source location does not identify an observed node$"):
            parser.source_lease.borrow(SourceLocation(SourceDocumentId(document), pointer, "schema"))
    finally:
        parser.dispose()
        parser.source_lease.close()


def test_closed_binding_ledger_rejects_identities() -> None:
    """Reject graph identities once the parser is disposed, which only a caller holding the ledger can ask for."""
    parser = ContractApiOpenAPIParser(
        SOURCE / "resolver.json", attempt_id=AttemptId(7), openapi_scopes=[OpenAPIScope.Api], formatters=[]
    )
    try:
        parser.parse()
        reference = parser.model_resolver.references["resolver.json#/components/schemas/Pet"]
    finally:
        parser.dispose()
        parser.source_lease.close()
    with pytest.raises(BindingCaptureError, match="Binding ledger is closed"):
        parser.binding_ledger.identity(reference)


def test_opaque_default_overrides() -> None:
    """Record overrides keyed by non-str values as opaque, which only an unvalidated parser config can hold."""

    class OverrideKey(str, Enum):
        A = "a"

    config = OpenAPIParserConfig(openapi_scopes=[OpenAPIScope.Api], formatters=[])
    config.default_value_overrides = {OverrideKey.A: "y"}
    parser = ContractApiOpenAPIParser(SOURCE / "capture-branches.json", attempt_id=AttemptId(1), config=config)
    try:
        parser.parse()
        assert_output(
            json.dumps(
                {
                    "capture_failed": parser.binding_ledger.failure is not None,
                    "default_producers": sorted({
                        entry.producer for entry in parser.binding_resolver.default_resolutions
                    }),
                },
                indent=2,
            )
            + "\n",
            EXPECTED / "capture-branches-enum-override.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("failure", ["missing_root", "structural_cycle", "after_enter"])
def test_capture_collapse_failures(failure: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject malformed recipes and retain incomplete context entry on real parse failures."""
    from datamodel_code_generator.parser import base
    from datamodel_code_generator.parser.openapi_contract_store import ContractGenerationStore

    parser = ContractApiOpenAPIParser(
        SOURCE / "replacements.json",
        attempt_id=AttemptId(1),
        openapi_scopes=[OpenAPIScope.Api],
        collapse_root_models=True,
        formatters=[],
    )
    if failure == "after_enter":

        def fail_import(*_args: object) -> None:
            message = "injected import failure"
            raise RuntimeError(message)

        monkeypatch.setattr(base, "_register_data_type_import", fail_import)
    else:
        original = ContractGenerationStore._begin_collapse

        def corrupt_root(
            self: ContractGenerationStore,
            data_type: DataType,
            replacement: DataType | Reference,
            owner: DataType | DataModelFieldBase,
            kind: Literal["field", "nested", "reference"],
        ) -> RootCollapse:
            model = next(model for model in parser.results if model.reference is data_type.reference)
            if failure == "missing_root":
                fields = model.fields
                model.fields = []
                try:
                    return original(self, data_type, replacement, owner, kind)
                finally:
                    model.fields = fields
            root_type = model.fields[0].data_type
            root_type.data_types.append(root_type)
            try:
                return original(self, data_type, replacement, owner, kind)
            finally:
                root_type.data_types.pop()

        monkeypatch.setattr(ContractGenerationStore, "_begin_collapse", corrupt_root)
    try:
        with pytest.raises(RuntimeError):
            parser.parse()
        assert_output(
            json.dumps(
                {
                    "latched_capture_failure": isinstance(parser.binding_ledger.failure, BindingCaptureError),
                    "collapse_completion": [item.completed for item in parser.binding_ledger.collapses],
                },
                indent=2,
            )
            + "\n",
            EXPECTED / ("collapse-after-enter.txt" if failure == "after_enter" else "collapse-invalid-recipe.txt"),
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


def test_inherited_default_rejects_unmatched_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Latch a corrupt observer sequence during real inherited-field generation."""
    from datamodel_code_generator.parser.openapi import OpenAPIParser

    original = OpenAPIParser._apply_inherited_field_default

    def duplicate_resolution(
        parser: ContractOpenAPIParser,
        field: DataModelFieldBase,
        inherited_field: DataModelFieldBase,
        *,
        class_name: str,
    ) -> None:
        original(parser, field, inherited_field, class_name=class_name)
        parser.binding_resolver.default_resolutions.append(parser.binding_resolver.default_resolutions[-1])

    monkeypatch.setattr(OpenAPIParser, "_apply_inherited_field_default", duplicate_resolution)
    parser = ContractOpenAPIParser(
        SOURCE / "copied-field-origins.json",
        attempt_id=AttemptId(1),
        formatters=[],
        default_value_overrides=json.loads((SOURCE / "copied-default-overrides.json").read_text()),
    )
    try:
        with pytest.raises(BindingCaptureError, match="multiple unmatched resolver calls") as failure:
            parser.parse()
        assert_output(
            json.dumps({
                "latched_first": parser.binding_ledger.failure is failure.value,
                "cause": type(failure.value.__cause__).__name__,
            })
            + "\n",
            EXPECTED / "failure-capture.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()
