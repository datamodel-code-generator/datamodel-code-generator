"""Import a back only when a function that defines a class runs."""

VALUE = 1


def load():
    class Holder:
        from scripts import a

    return Holder
