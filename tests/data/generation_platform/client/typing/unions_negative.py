"""Refuse a response union read as one of its members or as another union."""

from __future__ import annotations

from pets import Client
from pets_models import Circle, Dog


def refuse(client: Client) -> None:
    _circle: Circle = client.default.get_shape()  # error
    _dog: Dog = client.default.get_pet()  # error
