"""Native unconstrained null alias for comparison with generated aliases."""

from __future__ import annotations

from pydantic import RootModel

NativeNull = RootModel[None]
