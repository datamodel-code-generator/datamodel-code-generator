"""#7119: a continuation line maps to its statement, through brackets, backslashes and strings."""

TOTAL = (
    1
    + 2
)
NAME = "a" \
    "b"
TEXT = """line
inside"""


def call(x):
    return call(
        x,
    )
