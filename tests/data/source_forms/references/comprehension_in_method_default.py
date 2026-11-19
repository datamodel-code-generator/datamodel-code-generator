"""Review R25: a comprehension in a method default runs in the class body and reads class names in its iterable."""


class C:
    size = 3

    def m(self, d=[i for i in range(size)]):
        return d
