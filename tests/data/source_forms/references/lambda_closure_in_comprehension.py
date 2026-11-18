"""Review R27: a lambda in a comprehension reads the comprehension's target as a free name."""


def f(xs):
    return [lambda: x for x in xs]
