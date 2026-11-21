"""Import a back only when a function that defines a class runs."""

VALUE = 1


def load():
    class Holder:
        from datamodel_code_generator import a

    return Holder
