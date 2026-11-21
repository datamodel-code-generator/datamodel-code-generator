"""C3 P3/P4: an elif branch and an overriding method read on the base text map to their own clause and owner."""


class Base:
    def run(self, x):
        if x == 1:
            return "one"
        elif x == 2:
            return "two"
        return "other"


class Child(Base):
    def run(self, x):
        return super().run(x)
