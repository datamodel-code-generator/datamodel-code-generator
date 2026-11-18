"""A del statement makes the name local to the function, so an earlier read is no global read (review R04)."""

x = 1


def f():
    print(x)
    del x


def g(table):
    del table["k"], table.attr
