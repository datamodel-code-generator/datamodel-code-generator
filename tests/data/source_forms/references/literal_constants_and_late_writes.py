"""#7017, the docs builders and the store check's _literal_names: literal tables (a read-only mapping too), and the writes that make them unreadable.

constants: PLAIN NESTED NEGATIVE FINAL WRITTEN APPENDED REBOUND CALLED STARRED UNHASHABLE MALFORMED MISSING TUPLE_CALL READ_ONLY OPAQUE
"""

from typing import Final

PLAIN = ("a", "b")
NESTED = {"x": frozenset({"y"}), "z": [1, (2, 3)], "w": set(), "v": dict()}
NEGATIVE = (-1, +2.5)
FINAL: Final = {"k": None}
WRITTEN = {"a": 1}
WRITTEN["b"] = 2
APPENDED = []
APPENDED.append(1)
REBOUND = 1
REBOUND = 2
CALLED = sorted(["b", "a"])
STARRED = {**PLAIN}
UNHASHABLE = {[1]: 2}
MALFORMED = dict([("ok", 1), ("bad", 2, 3)])
TUPLE_CALL = tuple(["a"])
from types import MappingProxyType

READ_ONLY = MappingProxyType({"set_fields": "copied"})
OPAQUE = MappingProxyType(PLAIN)
