"""Inject observer failures into real generation for lifetime regression inputs."""

from __future__ import annotations

import weakref
from contextlib import suppress
from typing import TYPE_CHECKING

from datamodel_code_generator._generation_contract import AttemptId, BindingCaptureError
from datamodel_code_generator.parser.openapi_contract import ContractOpenAPIParser
from datamodel_code_generator.parser.openapi_contract_store import BindingLedger

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from datamodel_code_generator.model.base import DataModel


def failed_module_capture(
    source: Path, monkeypatch: pytest.MonkeyPatch, *, chained: bool
) -> tuple[ContractOpenAPIParser, tuple[weakref.ReferenceType[DataModel], ...], BindingCaptureError | None]:
    """Return only disposed ownership and weak references after an abnormal observer call."""
    parser = ContractOpenAPIParser(source, attempt_id=AttemptId(1), formatters=[])
    results = parser.parse()
    references = tuple(weakref.ref(model) for model in parser.results)

    def fail_identity(_ledger: BindingLedger, _node: object) -> None:
        if chained:
            msg = "Injected identity failure"
            raise ValueError(msg)
        msg = "Injected binding failure"
        raise BindingCaptureError(msg)

    with monkeypatch.context() as patch:
        patch.setattr(BindingLedger, "identity", fail_identity)
        with suppress(BindingCaptureError):
            parser.resolve_module_results(results)
    failure = parser.binding_ledger.failure
    parser.dispose()
    parser.source_lease.close()
    return parser, references, failure
