"""Class-level names are not seen by methods, lambdas or comprehensions in the class body (first iterator aside)."""

y = "global"


class C:
    y = "class"
    items = [1, 2]
    z = [y for _ in items]
    first = [v for v in items]
    f = lambda self: y

    def m(self):
        return y
