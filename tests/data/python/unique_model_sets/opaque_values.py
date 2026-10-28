"""An external runtime type whose short name shadows a builtin."""
from enum import Enum


class int(Enum):
    one = 1


def validate_value(value, info):
    return value
