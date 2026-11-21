"""Import a back only when a function runs."""

VALUE = 1


def load():
    from datamodel_code_generator import a

    return a
