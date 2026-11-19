"""__class__ and zero-argument super() in methods read the class cell, unless a function on the way binds it."""


class Base:
    def name(self):
        return "base"


class Child(Base):
    def name(self):
        return super().name() + __class__.__name__

    def nested(self):
        return lambda: __class__

    def explicit(self, __class__):
        return __class__

    def enclosing(self):
        __class__ = 1
        return lambda: __class__
