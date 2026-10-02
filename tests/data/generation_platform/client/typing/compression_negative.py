"""Reject request codings that are not strings or None."""

from __future__ import annotations

from pets.options import ClientOptions, RequestOptions


def wrong_codings() -> None:
    """Reject each mistyped coding."""
    ClientOptions(compression=5)  # error
    RequestOptions(compression=b"gzip")  # error
    RequestOptions(compression=("gzip",))  # error
