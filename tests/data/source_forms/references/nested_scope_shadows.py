"""A1 M1: match, except and import names in a nested function hide the outer function's names there only."""


def outer(result, error, json):
    def nested(value):
        match value:
            case [result]:
                pass
        try:
            pass
        except OSError as error:
            pass
        import json

        return result, error, json

    return nested, result, error, json
