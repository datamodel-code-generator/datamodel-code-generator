"""Import a back only when a function runs."""

VALUE = 1


def load():
    from scripts import a

    return a
