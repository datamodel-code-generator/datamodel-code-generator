"""Reject request codings that are unsupported or selected outside the client."""

from __future__ import annotations

from pets.options import ClientOptions, RequestOptions


def wrong_codings() -> None:
    """Reject each mistyped coding."""
    ClientOptions(compression=5)  # error
    ClientOptions(compression="br")  # error
    RequestOptions(compression="gzip")  # error
