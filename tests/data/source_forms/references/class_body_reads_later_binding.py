"""Review R22, S1: a class-body read of a name the class binds later falls back to the module at run time."""

x = "module"


class C:
    y = x
    x = "class"
