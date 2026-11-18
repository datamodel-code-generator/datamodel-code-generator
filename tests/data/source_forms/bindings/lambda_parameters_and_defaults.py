"""C3 P1: a lambda's parameters are its own, its defaults are read outside (lambda error=error in an except handler)."""


def f(callbacks):
    try:
        pass
    except ValueError as error:
        callbacks.append(lambda error=error: error)
    keyed = lambda key, /, *rest, flag=False, **options: (key, rest, flag, options)
    return keyed
