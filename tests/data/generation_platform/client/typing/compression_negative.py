"""Reject request codings that are unsupported or selected outside the client."""

from __future__ import annotations

from pets import Client
from pets.options import RequestOptions


def wrong_codings(client: Client) -> None:
    """Reject each mistyped coding."""
    Client(compression=5)  # error
    Client(compression="br")  # error
    RequestOptions(compression="gzip")  # error
    client.with_options(compression=None)  # error
