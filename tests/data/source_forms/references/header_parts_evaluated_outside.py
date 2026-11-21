"""C3 header rule: decorators, defaults, annotations, bases, keywords and a first iterable run in the enclosing scope."""


def factory(decorate, default, base, meta, rows, kind):
    @decorate(kind)
    def inner(value: kind = default, *, flag=default) -> kind:
        return value

    class Made(base, metaclass=meta):
        pass

    total = [row for row in rows]
    return inner, Made, total
