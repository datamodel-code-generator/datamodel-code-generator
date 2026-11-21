"""A1 M1: a comprehension target, a match capture, an except name and an import name hide a parameter."""


def comprehension(field, models):
    return [field._state for field in models], field


def match_capture(field, value):
    def inner():
        match value:
            case object() as field:
                return field
        return field

    return inner


def except_name(field):
    def inner():
        try:
            pass
        except ValueError as field:
            return field
        import os as field

        return field

    return inner
