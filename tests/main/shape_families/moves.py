"""How two generated modules of one shape decide its payload pool, for comparing two generators of the shape.

A shape moves between two generators (a release and the candidate) when their modules decide a payload of its pool
differently. ``verdicts`` is what is compared: each payload's verdict in lax mode and in strict mode over its JSON text
(the family verdict's ``lax_verdicts`` and ``strict_verdicts``, through its loader), or the one entry that says the
module does not import. ``moved`` names the smallest payload two such vectors disagree on.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .verdict import _loaded, _UnusableCellError, lax_verdicts, strict_verdicts, truth

if TYPE_CHECKING:
    from pathlib import Path

    from .families import Shape


def verdicts(output: Path, shape: Shape, backend: str) -> tuple[str, ...]:
    """Return a module's verdict on each payload of the shape's pool, lax and strict, or why it does not import."""
    known = truth(shape)
    try:
        with _loaded(output, [shape.name], backend) as (runtime,):
            pairs = zip(lax_verdicts(runtime, known), strict_verdicts(runtime, known), strict=True)
            return tuple(f"{lax}, strict {strict}" for lax, strict in pairs)
    except _UnusableCellError as unusable:
        return (f"{unusable.kind}: {unusable.detail}",)


def moved(shape: Shape, before: tuple[str, ...], after: tuple[str, ...]) -> tuple[str, str] | None:
    """Return the smallest payload two modules of a shape decide differently and how each decides it, or None.

    A pool holds more than one payload, so a vector of one entry is a module that does not import: two of them do not
    differ in behaviour; against one that imports, the witness is empty and the detail says which side fails.
    """
    unusable = [len(side) == 1 for side in (before, after)]
    if before == after or all(unusable):
        return None
    if any(unusable):
        first, second = (side[0] if len(side) == 1 else "imports" for side in (before, after))
        return "", f"before: {first}; after: {second}"
    differing = [
        (json.dumps(payload), first, second)
        for payload, first, second in zip(truth(shape).payloads, before, after, strict=True)
        if first != second
    ]
    witness, first, second = min(differing, key=lambda item: (len(item[0]), item[0]))
    return witness, f"before: {first}; after: {second}"
