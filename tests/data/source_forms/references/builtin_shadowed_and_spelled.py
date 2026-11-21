"""#7026: globals() written with a space, a parameter that shadows the builtin, and a module-level rebinding."""

TABLE = tuple(globals ().items())


def shadowed(globals):
    return globals().items()


def module_rebinding():
    return open


open = None
