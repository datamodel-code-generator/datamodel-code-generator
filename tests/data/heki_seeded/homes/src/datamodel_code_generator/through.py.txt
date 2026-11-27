from typing import TYPE_CHECKING

from . import core
from datamodel_code_generator import decide

if TYPE_CHECKING:
    from .core import Rule

    Checked = Rule | None


def by_module() -> None:
    core.decide()


by_package = decide()
