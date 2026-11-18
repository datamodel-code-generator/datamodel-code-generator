"""C3 P1/P2: a lambda, a genexpr and a nested def on one line; two lambdas on one line are two scopes."""


def outer(rows):
    key = lambda value: value.upper()
    total = sum(x for x in rows)
    def inner(): return (
        "a",
        "b",
    )
    pair = (lambda a: a, lambda b: b)
    nested = list(name for name in (item for item in rows))
    return key, total, inner, pair, nested
