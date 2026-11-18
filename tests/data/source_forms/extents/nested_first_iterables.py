"""C3 v2 (_client/channels.py:156): a scope inside another comprehension's first iterable owns its own tokens.

questions: bindings, references
"""


def f(rows, kinds):
    a = list(name for name in (item for item in rows))
    b = [name for name in [item for item in rows] if name]
    c = {key: rows for key in (lambda seq: seq)(kinds)}
    d = list(
        (name, kind)
        for name, kind in zip(
            (row.name for row in rows),
            kinds,
            strict=True,
        )
    )
    return a, b, c, d
