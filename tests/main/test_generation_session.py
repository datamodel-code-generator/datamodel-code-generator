"""Exercise real accepted batches and products through the unchanged generation driver."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datamodel_code_generator import (
    GenerateConfig,
    OpenAPIScope,
    _prepare_generate_facade_config,
    _run_generation,
)
from datamodel_code_generator._openapi_generation import OpenAPIGenerationSession, SourceLease
from datamodel_code_generator._openapi_type_binding import require_type_bindings
from datamodel_code_generator.parser.openapi import OpenAPIParser
from datamodel_code_generator.parser.openapi_contract import BindingResolverMixin
from datamodel_code_generator.parser.openapi_contract_store import BindingLedger
from tests.conftest import assert_output
from tests.data.python.generation_session_inputs import generate_product, session_protocol_failure

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform"
EXPECTED = DATA / "expected/main/generation_platform"


@pytest.mark.parametrize(
    ("source", "options"),
    [
        (
            "binding/session-constrained-object-True.json",
            {"field_constraints": True, "use_annotated": True},
        ),
        (
            "binding/session-discriminator-forward.json",
            {
                "output_model_type": "dataclasses.dataclass",
                "module_split_mode": "single",
                "use_enum_values_in_discriminator": True,
                "use_subclass_enum": True,
                "reuse_model": True,
                "collapse_root_models": True,
            },
        ),
        (
            "binding/session-extra-items.json",
            {
                "output_model_type": "typing.TypedDict",
                "use_standard_primitive_types": True,
                "use_closed_typed_dict": True,
            },
        ),
        ("binding/type-projection.json", {"enum_field_as_literal": "all"}),
    ],
    ids=["annotated", "enum-literal", "extra-items", "value-literal"],
)
def test_required_models_behind_wrapped_types(
    source: str, options: dict[str, object], request: pytest.FixtureRequest
) -> None:
    """Follow models behind Annotated, enum-member Literal, and TypedDict extra-items types.

    No target calls require_type_bindings, so this probe keeps it covered until a review decides whether it goes.
    """
    product, retained = generate_product(
        (SOURCE / source).resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            formatters=[],
            disable_timestamp=True,
            **options,
        ),
    )
    product.close()
    assert_output(
        "\n".join(
            sorted({
                error.code
                for error in require_type_bindings(product.batch, tuple(use.id for use in product.batch.type_uses))
            })
        ),
        EXPECTED / "session-review/wrapped-types" / f"{request.node.callspec.id}.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize("case", sorted(path.stem for path in (EXPECTED / "session/protocol").glob("*.txt")))
def test_session_protocol_ownership_and_transfer(case: str) -> None:
    """Reject foreign or repeated transfers and release every real attempt's graph."""
    actual = session_protocol_failure((SOURCE / "observation.json").resolve(), case)
    assert_output(json.dumps(actual, indent=2) + "\n", EXPECTED / "session/protocol" / f"{case}.txt")


@pytest.mark.parametrize("case", sorted(path.stem for path in (EXPECTED / "session/artifacts").glob("*.txt")))
def test_product_artifact_integrity(case: str) -> None:
    """Demand final types only from unique, present, decodable, well-formed ordinary artifacts."""
    product, retained = generate_product(
        (SOURCE / "binding/session-selection.json").resolve(),
        GenerateConfig(
            input_file_type="openapi", openapi_scopes=[OpenAPIScope.Api], formatters=[], disable_timestamp=True
        ),
        artifact_failure=case,
    )
    product.close()
    codes = sorted({
        item.code
        for use in product.batch.type_uses
        if use.id.role == "request_body"
        for item in require_type_bindings(product.batch, (use.id,))
    })
    assert_output(json.dumps(codes, indent=2) + "\n", EXPECTED / "session/artifacts" / f"{case}.txt")
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


def test_product_artifact_validation_exception() -> None:
    """Propagate an invalid writer encoding after model disposal instead of accepting a product."""
    with pytest.raises(LookupError, match="unknown encoding: unknown-artifact-encoding"):
        generate_product(
            (SOURCE / "observation.json").resolve(),
            GenerateConfig(input_file_type="openapi", formatters=[], disable_timestamp=True),
            artifact_failure="unknown_encoding",
        )


@pytest.mark.parametrize("case", json.loads((SOURCE / "binding/batch-failures.json").read_text()))
def test_invalid_batch_demand(case: str) -> None:
    """Reject corrupt accepted identities and keep unrelated failures outside selected demands."""
    from tests.data.python.binding_batch_failures import corrupt_batch

    product, retained = generate_product(
        (SOURCE / "binding/session-legacy.json").resolve(),
        GenerateConfig(
            input_file_type="openapi", openapi_scopes=[OpenAPIScope.Api], formatters=[], disable_timestamp=True
        ),
    )
    product.close()
    batch, requested = corrupt_batch(product.batch, case)
    assert_output(
        "".join(
            f"{code}\n"
            for code in sorted({error.code for error in require_type_bindings(batch, (requested, requested))})
        ),
        EXPECTED / "session/batch-failures" / f"{case}.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize("constructor_failure", [False, True])
def test_session_release_failure(constructor_failure: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing ledger cleanup still releases sources and preserves any constructor error."""
    source = (SOURCE / "observation.json").resolve()
    session = OpenAPIGenerationSession(
        output=Path("models.py"), model_package="models", root_selector_document=source.as_uri()
    )
    config = _prepare_generate_facade_config(
        GenerateConfig(
            input_file_type="openapi", formatters=[], type_mappings=["invalid"] if constructor_failure else []
        )
    )
    leases: list[SourceLease] = []
    real_close = BindingLedger.close
    lease_close = SourceLease.close

    def failed_release(ledger: BindingLedger) -> None:
        real_close(ledger)
        message = "ledger release failed"
        raise RuntimeError(message)

    def observe_release(lease: SourceLease) -> None:
        leases.append(lease)
        lease_close(lease)

    if not constructor_failure:
        _run_generation(source, config, Path.cwd(), use_output_cwd=False, capture=session)
        leases.append(session.source_lease)
    with monkeypatch.context() as fault:
        fault.setattr(BindingLedger, "close", failed_release)
        fault.setattr(SourceLease, "close", observe_release)
        if constructor_failure:
            with pytest.raises(ValueError, match="Invalid type mapping format: 'invalid'"):
                _run_generation(source, config, Path.cwd(), use_output_cwd=False, capture=session)
        else:
            with pytest.raises(RuntimeError, match="ledger release failed"):
                session.close()
    session.close()
    assert_output(str(len(set(leases))) + "\n", EXPECTED / "one-source-lease.txt")
    for lease in leases:
        with pytest.raises(RuntimeError, match="Source lease is closed"):
            lease.documents()


@pytest.mark.parametrize("case", ["freeze", "multiple", "reentrant"])
def test_session_cleanup_failure_identity(case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never replace the original failure while closing multiple or reentrant attempt owners."""
    from tests.data.python.generation_session_inputs import session_cleanup_failures

    assert_output(
        json.dumps(session_cleanup_failures((SOURCE / "observation.json").resolve(), case, monkeypatch), indent=2)
        + "\n",
        EXPECTED / "session/cleanup" / f"{case}.txt",
    )


def test_parser_dispose_preserves_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ordinary disposal errors survive sidecar failures and every remaining owner closes."""
    source = (SOURCE / "observation.json").resolve()
    session = OpenAPIGenerationSession(
        output=Path("models.py"), model_package="models", root_selector_document=source.as_uri()
    )
    ordinary_dispose = OpenAPIParser.dispose
    ledger_close = BindingLedger.close
    resolver_close = BindingResolverMixin.close_capture
    released: list[int] = []

    def failed_dispose(parser: OpenAPIParser) -> None:
        ordinary_dispose(parser)
        message = "ordinary dispose failed"
        raise ValueError(message)

    def failed_ledger(ledger: BindingLedger) -> None:
        ledger_close(ledger)
        message = "ledger release failed"
        raise RuntimeError(message)

    def observe_resolver(resolver: BindingResolverMixin) -> None:
        resolver_close(resolver)
        released.append(len(resolver.resolutions) + len(resolver.default_resolutions))

    with monkeypatch.context() as fault:
        fault.setattr(OpenAPIParser, "dispose", failed_dispose)
        fault.setattr(BindingLedger, "close", failed_ledger)
        fault.setattr(BindingResolverMixin, "close_capture", observe_resolver)
        with pytest.raises(ValueError, match="ordinary dispose failed"):
            _run_generation(
                source,
                _prepare_generate_facade_config(GenerateConfig(input_file_type="openapi", formatters=[])),
                Path.cwd(),
                use_output_cwd=False,
                capture=session,
            )
    assert_output("\n".join(map(str, released)) + "\n", EXPECTED / "no-retained-graph.txt")
