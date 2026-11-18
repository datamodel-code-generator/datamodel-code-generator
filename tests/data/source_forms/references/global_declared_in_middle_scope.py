"""Review R03: a function nested in one that declares global reads the enclosing function's declaration's name."""

x = 0


def a():
    global x

    def b():
        return x

    return b
