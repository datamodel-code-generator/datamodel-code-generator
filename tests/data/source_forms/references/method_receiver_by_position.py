"""A1 S1, S2: the receiver is the first positional parameter whatever its name; a staticmethod has none.

questions: bindings
"""


class C:
    def plain(self):
        self.a = 1

    def renamed(this):
        this.b = 2

    def positional_only(self, /, value):
        self.value = value

    @classmethod
    def build(cls):
        cls.c = 3

    @staticmethod
    def make(other):
        other.hidden = 1

    def closure(self):
        def inner():
            self.d = 4

        return inner

    def none():
        pass
