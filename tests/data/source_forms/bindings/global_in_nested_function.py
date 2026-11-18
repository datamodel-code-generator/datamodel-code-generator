"""Review R02: global in a nested function binds a module name, not the enclosing function's local."""


def outer():
    x = 1

    def inner():
        global x
        x = 2

    def reader():
        return x

    return inner, reader
