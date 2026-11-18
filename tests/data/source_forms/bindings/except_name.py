"""The name of an except clause binds in the enclosing scope, whatever the handler's form."""


def f():
    try:
        pass
    except (ValueError, TypeError) as error:
        seen = error
    except Exception as other:
        seen = other
    return seen
