"""A1 M1: comprehension targets are local to the comprehension, in every comprehension kind."""

item = "module"


def f(rows):
    a = [item for item in rows]
    b = {item for item in rows}
    c = {key: item for key, item in rows}
    d = list(item for item in rows)
    e = [cell for row in rows for cell in row if cell]
    return a, b, c, d, e, item
