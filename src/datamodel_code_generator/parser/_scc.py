"""Compatibility re-export for the parser SCC helpers."""

from __future__ import annotations

from datamodel_code_generator._scc import find_circular_sccs, strongly_connected_components

__all__ = ["find_circular_sccs", "strongly_connected_components"]
