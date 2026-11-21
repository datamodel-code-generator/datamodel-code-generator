"""Review R20: nonlocal in a class body rebinds the enclosing function's name."""


def f():
    x = 1

    class C:
        nonlocal x
        x = 2

    return x, C
