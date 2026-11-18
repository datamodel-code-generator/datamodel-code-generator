"""__class__ and zero-argument super() in methods read the class cell."""


class Base:
    def name(self):
        return "base"


class Child(Base):
    def name(self):
        return super().name() + __class__.__name__

    def nested(self):
        return lambda: __class__
