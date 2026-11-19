"""#7026: the separator of str.split given by keyword, positionally and through the unbound method.

keywords: sep
"""


def cut(ref):
    return ref.split(sep="#", maxsplit=1), ref.rsplit("#", 1), str.split(ref, "#"), ref.split(maxsplit=1)
