"""The words of a recommendation that the recommender and the JSON report of it both spell, without importing either.

``arguments.py`` keeps its own copy of the modes, as the argument parser imports neither this module's package nor
pydantic.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

__all__ = ["DisagreementKind", "InputSource", "OptionValue", "RecommendMode"]

RecommendMode: TypeAlias = Literal["quick", "full"]
"""The mode a recommendation runs in."""
InputSource: TypeAlias = Literal["file", "url", "stdin", "directory", "input-model"]
"""Where the input of a recommendation was read from."""
OptionValue: TypeAlias = bool | str | list[str]
"""The value of a config field as pyproject.toml spells it."""
DisagreementKind: TypeAlias = Literal["explicit", "preset", "newer-preset"]
"""Why a configured value stays although a rule suggests another: the configuration, its preset or a newer preset."""
