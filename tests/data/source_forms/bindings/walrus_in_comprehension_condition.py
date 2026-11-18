"""Review R16: a walrus in a comprehension condition binds in the enclosing function."""


def f(xs):
    ys = [y for x in xs if (y := x * 2) > 1]
    return y, ys
