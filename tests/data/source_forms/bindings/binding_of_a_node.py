"""SD (step 1, private-reexport): the binding each node makes, an import's alias included, wherever the name lives.

An import under ``global`` binds in the module, a ``nonlocal`` name in the enclosing function, a comprehension's walrus
in the function around it; ``(x): int`` and a name that is read bind nothing.
questions: imports
binding-nodes: all
"""

import os.path as _path
from typing import cast as _cast


def load():
    global _json
    import json as _json

    return _json, _path, _cast


def outer():
    count = 0

    def bump():
        nonlocal count
        count = count + 1
        return count

    return bump


def handle(value, *rest, flag=False, **extra):
    (annotated): int
    try:
        flag = not flag
    except ValueError as error:
        print(error, extra)
    match value:
        case {"k": captured, **others}:
            print(captured, others)
        case [first, *tail]:
            print(first, tail)
    return [seen for seen in rest if (last := seen)], last


class Holder:
    size = 0

    def grow(self):
        self.size += 1
