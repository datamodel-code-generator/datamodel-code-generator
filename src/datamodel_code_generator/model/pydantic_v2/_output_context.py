"""Private output capability context shared by Pydantic v2 generators."""

from __future__ import annotations

from typing_extensions import Sentinel

ANNOTATED_CONSTRAINTS_CONTEXT: object = Sentinel("ANNOTATED_CONSTRAINTS_CONTEXT")
