"""A6 table, review R17, N1: TYPE_CHECKING read by origin (module, alias, assignment alias), negated, and a local
TYPE_CHECKING that is not typing's.
"""

import typing
import typing as t
from typing import TYPE_CHECKING as TC

TYPE = typing.TYPE_CHECKING
if typing.TYPE_CHECKING:
    from a import A
if t.TYPE_CHECKING:
    from b import B
if TC:
    from c import C
if TYPE:
    from d import D
if not TC:
    from e import E
else:
    from f import F


def local():
    TYPE_CHECKING = False
    if TYPE_CHECKING:
        from g import G
    return G
