"""Damage observed producer records during real generation to exercise failure boundaries."""

from __future__ import annotations

import gc
import weakref
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from datamodel_code_generator._generation_contract import AttemptId, BindingCaptureError
from datamodel_code_generator.enums import JsonSchemaVersion, OpenAPIScope
from datamodel_code_generator.parser.openapi_contract import (
    BindingCaptureMixin,
    ContractApiOpenAPIParser,
    ContractOpenAPIParser,
)
from datamodel_code_generator.parser.openapi_contract_origins import ValidatedSchemaOriginIndex

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import pytest


@contextmanager
def producer_fault(case: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the normal engine and capture methods, injecting only an inconsistent observation."""
    method = case["method"]
    owner = ValidatedSchemaOriginIndex if case.get("owner") == "origins" else BindingCaptureMixin
    original = getattr(owner, method)

    def damaged(parser: BindingCaptureMixin, *args: Any, **kwargs: Any) -> Any:
        match case["id"]:
            case "origin-raw-child" | "origin-raw-node":
                if kwargs["obj"].properties and isinstance(kwargs["raw"], dict):
                    properties = None if case["id"] == "origin-raw-child" else dict.fromkeys(kwargs["obj"].properties)
                    kwargs["raw"] = {**kwargs["raw"], "properties": properties}
            case "origin-merged-child":
                if args[2].properties:
                    args = (
                        replace(args[0], raw={"properties": None}),
                        args[1].model_copy(update={"properties": None}),
                        args[2],
                    )
            case "origin-item-array":
                kwargs["obj"] = kwargs["obj"].model_copy(update={"type": "object"})
            case "origin-common-sequence":
                args = (args[0], args[1].model_copy(update={"allOf": [args[1]]}), *args[2:])
            case "origin-materialized-child":
                args = (args[0], args[1].model_copy(update={"properties": {"ghost": args[1]}}), *args[2:])
            case "combined-keyword":
                args = (*args[:-1], "unobserved")
            case "combined-owner":
                saved = parser._combined_branches
                parser._combined_branches = []
                try:
                    return original(parser, *args, **kwargs)
                finally:
                    parser._combined_branches = saved
            case "combined-identity":
                parser._combined_branches[-1].name = "unobserved"
            case "combined-missing-decision" | "combined-decision-order":
                frame = parser._combined_branches[-1]
                if frame.false_decisions:
                    if case["id"] == "combined-missing-decision":
                        frame.false_decisions.clear()
                    else:
                        frame.false_decisions[0] = ("#/unobserved", frame.false_decisions[0][1])
            case "combined-invalid-decision":
                if parser._combined_branches[-1].false_decisions:
                    parser._combined_branches[-1].false_decisions[0] = None
            case "combined-missing-item":
                args = (*args[:-1], ())
            case "combined-extra-item":
                if args[-1]:
                    args = (*args[:-1], (*args[-1], args[-1][-1]))
            case "combined-invalid-node":
                kwargs["original"] = False
            case "combined-missing-edge":
                args[0].edges.clear()
            case "allof-owner":
                args = (replace(args[0]),)
            case "allof-count":
                if args[0].direct_refs:
                    args[0].producer.resolutions.clear()
            case "allof-order":
                if args[0].producer.resolutions:
                    args[0].producer.resolutions[0] = replace(args[0].producer.resolutions[0], input="#/unobserved")
            case "union-owner" | "union-missing-reference" | "union-order" | "union-changed-parent":
                if parser._allof_refs:
                    frame = parser._allof_refs[-1]
                    if (
                        parser.binding_resolver.resolution_owner is frame.producer
                        and frame.producer.resolutions
                        and all(args[1] is not node for node in frame.obj.allOf)
                    ):
                        match case["id"]:
                            case "union-owner":
                                frame.name = "unobserved"
                            case "union-missing-reference":
                                frame.direct_refs = ()
                            case "union-order":
                                frame.producer.resolutions[-1] = replace(
                                    frame.producer.resolutions[-1], input="#/unobserved"
                                )
                            case _:
                                frame.materialized_parents[len(frame.producer.resolutions) - 1] = frame.obj
            case "root-duplicate-producer" | "root-reference-owner":
                frame = args[0]
                roots = [
                    value
                    for value in frame.values
                    if value.producer == "validation_keywords"
                    and any(source is frame.source for source in value.sources)
                ]
                if roots:
                    if case["id"] == "root-duplicate-producer":
                        frame.values.append(roots[0])
                    elif frame.references.resolutions:
                        frame.references.resolutions[0] = replace(frame.references.resolutions[0], input="#/unobserved")
            case "root-child-missing" | "root-child-identity" | "root-child-extra":
                if parser._root_value_children and not parser._root_value_children[-1].bound and args[0]:
                    match case["id"]:
                        case "root-child-missing":
                            args = ([],)
                        case "root-child-identity":
                            if not parser._root_value_children[-1].sources[0].ref:
                                args = ([args[0][0].model_copy(), *args[0][1:]],)
                        case _:
                            args = ([*args[0], args[0][-1]],)
            case "root-child-resolution" | "root-child-sibling":
                if parser._root_value_children:
                    frame = parser._root_value_children[-1]
                    if not frame.bound and frame.sources and getattr(frame.sources[0], "ref", None):
                        if case["id"] == "root-child-resolution":
                            frame.references.resolutions.clear()
                        else:
                            args = (args[0][:1],)
            case "ref-sibling-resolution":
                if args[0] is not args[1]:
                    args[2].resolutions.clear()
            case "allof-object-reference" | "allof-object-extra":
                if args[1] is not None and args[2].resolutions:
                    if case["id"] == "allof-object-reference":
                        args[2].resolutions.clear()
                    else:
                        args[2].resolutions.append(args[2].resolutions[0])
            case "default-missing":
                parser.binding_resolver.default_resolutions.clear()
            case "default-identity":
                parser.binding_resolver.default_resolutions[-1] = replace(
                    parser.binding_resolver.default_resolutions[-1], field_name="unobserved"
                )
            case "pending-default-identity":
                if parser._pending_field_default is not None:
                    parser._pending_field_default = replace(parser._pending_field_default, field_name="unobserved")
        return original(parser, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(owner, method, damaged)
        yield


def provenance_failure(source: Path, case: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Retain the failure itself while checking parser and observed graph release."""
    parser_type = ContractApiOpenAPIParser if case.get("api") == "true" else ContractOpenAPIParser
    parser = parser_type(
        source,
        attempt_id=AttemptId(1),
        formatters=[],
        collapse_root_models=True,
        field_constraints=case.get("field_constraints") == "true",
        jsonschema_version=JsonSchemaVersion.Draft202012,
        openapi_scopes=[OpenAPIScope.Api] if case.get("api") == "true" else [OpenAPIScope.Schemas],
    )
    failure = None
    references = ()
    try:
        with producer_fault(case, monkeypatch):
            try:
                parser.parse()
            except BindingCaptureError as error:
                failure = error
        references = tuple(weakref.ref(node) for node in parser.binding_ledger._anchors)
    finally:
        parser.dispose()
        parser.source_lease.close()
    latched = failure is not None and parser.binding_ledger.failure is failure
    parser_ref = weakref.ref(parser)
    del parser
    gc.collect()
    return {
        "error": [type(failure).__name__, str(failure)] if failure is not None else None,
        "latched_first": latched,
        "retained_parsers": int(parser_ref() is not None),
        "retained_graph": sum(node() is not None for node in references),
    }
