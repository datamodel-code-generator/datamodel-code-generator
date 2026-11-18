"""C3 rule 1: docstrings, comment-only, blank and multi-line string lines; only lines with a code token count."""


def f():
    """A docstring
    over two lines."""
    # a comment

    value = """first
second"""
    return value  # trailing comment
