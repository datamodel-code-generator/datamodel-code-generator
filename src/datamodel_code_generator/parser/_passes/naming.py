"""The naming pass (L2): the names of the models once every model has claimed one.

``name_models`` runs right after ``settle_yielding_names``, when the L1 resolver and the settle have named every model,
and records the naming plan (``parser/_naming.py``) that L3 reads for the name a model was numbered from
(``structure.delete_duplicate_models``, ``structure.replace_duplicate_name_in_module``). The parser comes in through
``NamingHost``, which ``Parser`` satisfies structurally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typing_extensions import Protocol

from datamodel_code_generator.parser._naming import plan_names

if TYPE_CHECKING:
    from datamodel_code_generator.parser._naming import NamingPlan
    from datamodel_code_generator.parser._passes._state import SettleRun

__all__ = ["NamingHost", "name_models"]


class NamingHost(Protocol):
    """What the naming pass writes on the parser."""

    _naming_plan: NamingPlan


def name_models(parser: NamingHost, _run: SettleRun[Any]) -> None:
    """Plan the names of the models after their last claim (``plan_names``), for L3 to read.

    The plan of policy ``legacy``, the only one today, names no model: each keeps the name the L1 resolver and
    ``settle_yielding_names`` gave it, and the pass renames nothing.
    """
    parser._naming_plan = plan_names("legacy")  # noqa: SLF001
