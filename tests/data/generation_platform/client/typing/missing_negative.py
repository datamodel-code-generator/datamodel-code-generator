"""Refuse null for a member that is not nullable, and an unset member read as its own type."""

from __future__ import annotations

from pets_models import LoginPostRequest, Pet


def refuse(pet: Pet) -> None:
    Pet(name="a", owner=None, tag=None)  # error
    LoginPostRequest(user="ann", remember=None)  # error
    _tag: str = pet.tag  # error
