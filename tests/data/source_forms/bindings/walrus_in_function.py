"""An assignment expression binds in the scope it stands in."""


def f(values):
    if (count := len(values)) > 1:
        return count
    while (item := values.pop()) is not None:
        pass
    return item
