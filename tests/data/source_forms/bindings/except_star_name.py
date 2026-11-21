"""Review R06: the name of an except* clause binds like an except name."""


def f():
    try:
        pass
    except* ValueError as group:
        seen = group
    return seen
