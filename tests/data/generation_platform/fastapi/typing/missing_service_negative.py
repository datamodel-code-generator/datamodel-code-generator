"""Serve Pydantic dataclasses in ways that type checkers must reject: one error on each line marked error."""

from __future__ import annotations

from pets_models import Pet


def answer(body: Pet) -> Pet:
    _tag: str = body.tag  # error
    return Pet(name=body.name, owner=body.owner, tag=None)  # error
