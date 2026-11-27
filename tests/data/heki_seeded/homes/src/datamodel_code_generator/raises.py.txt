from .core import Detail, Failure


def raised() -> None:
    raise Failure('x')


def caught() -> bool:
    try:
        raise Detail
    except Failure as error:
        return isinstance(error, Detail)
