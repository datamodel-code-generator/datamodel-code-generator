"""#7026 doc-citations: the members a class has, as the architecture page may cite them.

citations: docstring
The page is this docstring. Resolved: `class_members_cited.py:Parser.results`, `Parser.extra`, `Parser.rest`,
`Parser.cursor`, `Parser.handle`, `Parser.nested`, `Parser.counter`, `Parser.kind`, `Parser.helper`, `Parser.registry`,
`class_members_cited.py:build`, `class_members_cited.py:Sequence`, `class_members_cited.py:LIMIT`.
Broken: `Parser.missing`, `Parser.other`, `Parser.skipped`, `class_members_cited.py:gone`, `class_members_cited.py:Parser.nothing`.
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

LIMIT = 3
build = lambda: None


class Parser:
    kind = "json"

    def parse(self):
        self.results, (self.extra, *self.rest) = [], (0, 1)
        for self.cursor in range(3):
            pass
        with open("x") as self.handle:
            pass
        self.counter: int = 0

    @classmethod
    def helper(cls):
        cls.registry = {}

    def closure(self):
        def inner():
            self.nested = 1

        return inner

    @staticmethod
    def static(other):
        other.skipped = 1
