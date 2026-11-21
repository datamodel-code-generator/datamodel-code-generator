"""Review R01: a class-body comprehension reads class names only in its first iterable."""

n = 0


class C:
    n = 3
    xs = [i * n for i in range(n)]
    ok = any(v > n for v in xs)
