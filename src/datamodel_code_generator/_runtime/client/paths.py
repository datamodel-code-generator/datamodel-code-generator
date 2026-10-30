"""Path templates: the parameters of their segments, and the dot segments URL normalization removes from a path."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ("PLACEHOLDER", "dot_segment", "dotted_route", "filled", "path_segments")

PLACEHOLDER: Final = re.compile(r"\{([^{}]*)\}")
_DOT_SEGMENT: Final = re.compile(r"(?:\.|%2[Ee]){1,2}")
_DOTTED_ROUTE: Final = re.compile(r"/(?:\.|%2[Ee]){1,2}(?=/|$)")


def path_segments(template: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return each segment of a path template that has parameters, with their names in order."""
    return tuple(
        (segment, names)
        for segment in template.split("/")
        if (names := tuple(match[1] for match in PLACEHOLDER.finditer(segment)))
    )


def filled(template: str, values: Mapping[str, str]) -> str:
    """Return a template with each `{name}` replaced by the value of that name, which every placeholder must have."""
    return PLACEHOLDER.sub(lambda match: values[match[1]], template)


def dot_segment(segment: str, texts: Mapping[str, str]) -> bool:
    """Return whether a path template segment with its parameters' texts is `.` or `..`, which normalization removes.

    `%2E` in either case counts as the `.` it is equivalent to.
    """
    return _DOT_SEGMENT.fullmatch(filled(segment, texts)) is not None


def dotted_route(route: str) -> bool:
    """Return whether a built path has a `.` or `..` segment, `%2E` in either case counting as `.`."""
    return _DOTTED_ROUTE.search(route) is not None
