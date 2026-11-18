"""A class body inside a function reads the function's locals as free names."""


def build(kind):
    default = 0

    class Config:
        name = kind
        size = default

        def describe(self):
            return kind

    return Config
