"""Independent native control for the established generated null root syntax."""

from __future__ import annotations

from pydantic import RootModel


class NativeNull(RootModel[None]):
    """Native null root used to establish dependency-version capabilities."""

    root: None
