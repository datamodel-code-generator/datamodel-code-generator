"""Review R24: a globals() write leaves the name unbound to the model."""

globals()["Late"] = 1


def f():
    return Late
