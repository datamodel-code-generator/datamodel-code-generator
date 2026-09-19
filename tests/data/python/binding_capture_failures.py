"""Damage observed producer records during real generation to exercise failure boundaries."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from datamodel_code_generator.parser.openapi_contract import BindingCaptureMixin
from tests.data.python.generation_session_inputs import run_generation_session

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def producer_failure(source: Path, case: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Keep the normal engine and capture methods, injecting only an inconsistent observation."""
    method = case["method"]
    original = getattr(BindingCaptureMixin, method)

    def damaged(parser: BindingCaptureMixin, *args: Any, **kwargs: Any) -> Any:
        match case["id"]:
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
        return original(parser, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(BindingCaptureMixin, method, damaged)
        actual, retained = run_generation_session(source)
    return {
        "error": actual["error"],
        "batch": actual["batch"],
        "retained_parsers": actual["retained_parsers"],
        "retained_graph": retained,
    }
