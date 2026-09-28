"""Bounded inspection of visible exception provenance, without retaining messages or suppressed causes."""

from __future__ import annotations

import errno
import socket
import ssl
import sys
from dataclasses import dataclass, field
from typing import Final

_DEPTH: Final = 16
_NODES: Final = 64
_TRANSIENT: Final = frozenset({
    errno.ECONNREFUSED,
    errno.ECONNRESET,
    errno.ETIMEDOUT,
    errno.EHOSTUNREACH,
    errno.ENETUNREACH,
})


@dataclass(frozen=True, slots=True)
class CauseGraph:
    """A temporary, bounded graph of public exception links."""

    nodes: tuple[BaseException, ...] = field(repr=False)
    leaves: tuple[BaseException, ...] = field(repr=False)


def _members(error: BaseException) -> tuple[BaseException, ...] | None:
    if sys.version_info >= (3, 11) and isinstance(error, BaseExceptionGroup):  # noqa: F821
        return error.exceptions
    return None


def cause_graph(error: BaseException, /) -> CauseGraph | None:
    """Visit every group branch, accepting shared leaves while rejecting cycles and excess work."""
    nodes: list[BaseException] = []
    leaves: list[BaseException] = []
    active: set[int] = set()

    def visit(current: BaseException, depth: int) -> bool:
        if depth > _DEPTH or len(nodes) >= _NODES or id(current) in active:
            return False
        nodes.append(current)
        active.add(id(current))
        try:
            if (members := _members(current)) is not None:
                return all(visit(child, depth + 1) for child in members)
            child = current.__cause__
            if child is None and not current.__suppress_context__:
                child = current.__context__
            if child is not None:
                return visit(child, depth + 1)
            leaves.append(current)
            return True
        finally:
            active.remove(id(current))

    return CauseGraph(tuple(nodes), tuple(leaves)) if visit(error, 0) else None


def _transient_leaf(error: BaseException) -> bool:
    if isinstance(error, socket.gaierror):
        return error.errno == socket.EAI_AGAIN
    return isinstance(error, OSError) and error.errno in _TRANSIENT


@dataclass(frozen=True, slots=True)
class ConnectFailureEvidence:
    """Keep an identity anchor and nonsecret classification from the official failed trace event."""

    failure: BaseException = field(repr=False)
    known: bool
    transient: bool


def transient_connect(graph: CauseGraph | None, observation: ConnectFailureEvidence | None = None, /) -> bool:
    """Require every final branch to prove transience and reject SSL anywhere in the visible graph."""
    if graph is None:
        return False
    for node in graph.nodes:
        if isinstance(node, ssl.SSLError) or (isinstance(node, socket.gaierror) and node.errno != socket.EAI_AGAIN):
            return False
        if observation is not None and node is observation.failure and not observation.transient:
            return False
    return all(
        _transient_leaf(leaf)
        or (observation is not None and observation.known and observation.transient and leaf is observation.failure)
        for leaf in graph.leaves
    )


def connect_failure(error: BaseException, /) -> ConnectFailureEvidence:
    """Classify while the native failed event still exposes its valid cause graph."""
    graph = cause_graph(error)
    return ConnectFailureEvidence(error, graph is not None, transient_connect(graph))
