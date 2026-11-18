"""#7017, the docs builders and the store's _literal_names: literal tables, and the writes that make them unreadable.

constants: PLAIN NESTED NEGATIVE FINAL WRITTEN APPENDED REBOUND CALLED STARRED UNHASHABLE MISSING TUPLE_CALL
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
TUPLE_CALL = tuple(["a"])
