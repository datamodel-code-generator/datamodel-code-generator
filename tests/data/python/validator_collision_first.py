"""External validators used by generated collision regression models."""


def validate(value, info):
    return value + ":first"


def plain(value):
    return value + ":plain"


def wrap(value, handler, info):
    return handler(value) + ":wrap"


def Any(value, info):
    return value + ":any"


def Other(value, info):
    return value + ":other"


def _validate(value, info):
    return value + ":private"


def Base(value, info):
    return value + ":custom"
