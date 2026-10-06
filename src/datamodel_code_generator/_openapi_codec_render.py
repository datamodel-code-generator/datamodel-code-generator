"""Names of native codec declarations rendered for a client package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datamodel_code_generator._target_contract import TypeUseId


@dataclass(frozen=True, slots=True)
class UseAccessors:
    """The generated names that serve one planned use."""

    use: TypeUseId
    codec: str


@dataclass(frozen=True, slots=True)
class RenderedBindings:
    """The source of `_generated/model_bindings.py` and the accessors of every planned use."""

    source: str
    uses: tuple[UseAccessors, ...]
