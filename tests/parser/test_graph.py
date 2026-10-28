"""Unit tests for parser graph helpers."""

from __future__ import annotations

import pytest

from datamodel_code_generator.parser._graph import stable_toposort


@pytest.mark.allow_direct_assert
def test_stable_toposort_breaks_ties_by_input_order() -> None:
    """Equal-key nodes retain input order."""
    nodes = ["schema", "model", "field"]

    assert stable_toposort(nodes, {}, key=lambda _: 0) == ["schema", "model", "field"]


@pytest.mark.allow_direct_assert
def test_stable_toposort_ignores_edges_outside_node_set() -> None:
    """Edges from or to unknown nodes are ignored."""
    nodes = ["model", "schema"]
    edges = {
        "external": {"model"},
        "schema": {"missing"},
    }

    assert stable_toposort(nodes, edges, key=lambda _: 0) == ["model", "schema"]


@pytest.mark.allow_direct_assert
def test_stable_toposort_appends_cycle_remainder_in_key_order() -> None:
    """Cycle fallback appends blocked nodes deterministically by key."""
    nodes = ["beta", "alpha", "free"]
    edges = {
        "alpha": {"beta"},
        "beta": {"alpha"},
    }
    order = {"alpha": 0, "beta": 1, "free": 2}

    assert stable_toposort(nodes, edges, key=order.__getitem__) == ["free", "alpha", "beta"]


@pytest.mark.allow_direct_assert
def test_stable_toposort_cycle_fallback_preserves_duplicate_nodes() -> None:
    """Cycle fallback preserves duplicate input nodes while excluding emitted nodes."""
    nodes = ["beta", "alpha", "alpha", "free"]
    edges = {
        "alpha": {"beta"},
        "beta": {"alpha"},
    }
    order = {"alpha": 0, "beta": 1, "free": 2}

    assert stable_toposort(nodes, edges, key=order.__getitem__) == ["free", "alpha", "alpha", "beta"]


@pytest.mark.allow_direct_assert
def test_stable_toposort_keeps_duplicate_sources_before_destinations() -> None:
    """All occurrences of a source precede the destination it constrains."""
    nodes = ["a", "a", "b"]
    edges = {"a": {"b"}}
    order = {"a": 1, "b": 0}

    assert stable_toposort(nodes, edges, key=order.__getitem__) == ["a", "a", "b"]


@pytest.mark.allow_direct_assert
def test_stable_toposort_compatibility_reexport_preserves_identity_and_metadata() -> None:
    """The legacy parser import remains the canonical public surface."""
    from datamodel_code_generator._graph import stable_toposort as shared_stable_toposort

    assert stable_toposort is shared_stable_toposort
    assert stable_toposort.__module__ == "datamodel_code_generator.parser._graph"
