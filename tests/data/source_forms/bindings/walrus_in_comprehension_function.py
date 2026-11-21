"""A walrus inside a comprehension binds in the enclosing function, not in the comprehension."""


def f(values):
    found = [last for v in values if (last := v)]
    nested = [[deep := w for w in row] for row in values]
    return found, last, nested, deep
