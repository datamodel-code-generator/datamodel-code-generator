"""C3 (scripts/_change_units.py): the segments a position lies in, the header that holds it and its innermost node.

segments: yes
"""


@staticmethod
def pick(flag, value, default=(1 if True else 2)):
    label = value.upper() if flag else value.casefold()
    found = flag or value.endswith("_x") or value
    names = [n for n in value if flag and n if n != "q" for m in n]
    table = {key: key * 2 for key in value}
    if flag: label = label.lower()
    match value:
        case str() if flag:
            label = "s"
        case [1, *_] | (2, _):
            label = "t"
        case _:
            label = "u"
    convert = lambda item, scale=(2 if flag else 3): item * scale
    rows = (row for row in (x for x in value))
    return label, found, names, table, convert, rows


class Holder(dict, metaclass=type):
    size = 1
