"""An augmented assignment reads and binds its name; through a subscript or an attribute it binds nothing."""

TOTAL = 0
TOTAL += 1


def bump(counter, table):
    counter += 1
    table["n"] += 1
    table.size += 1
    return counter
