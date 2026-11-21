"""Sweep find (input_model.py:575): nonlocal names a binding of the enclosing function that comes later in source."""


def outer():
    def bump():
        nonlocal total
        total += 1

    total = 0
    bump()
    return total


def chain():
    value = 0

    def middle():
        nonlocal value

        def inner():
            nonlocal value
            value = 2

        return inner

    return middle, value
