"""The generation store check: what a name holds follows the language's bindings, not its spelling.

A write through a name is a write of the guarded container (``field.extras``) the name's binding aliases, or no
model-graph write when the binding holds a schema object (a parameter annotated ``JsonSchemaObject``). Every binding
form replaces what a name held (a ``match`` capture, an import, a nested ``def``, a ``lambda`` parameter), a
comprehension's target lives in the comprehension, a nested function reads its enclosing function's binding, a
method does not read the class body, and an alias of ``setattr`` at any level is ``setattr``; an in-place operator
keeps the alias.
"""


def closure_writes_the_alias(field):
    extras = field.extras

    def inner():
        extras["a"] = 1

    return inner


def closure_keeps_the_schema_object(obj: JsonSchemaObject):
    def inner():
        obj.required = True

    return inner


class Holder:
    shared = JsonSchemaObject.model_validate({})

    def method(self):
        shared.required = True


def local_setattr_alias(model):
    write = setattr
    write(model, "required", True)


def shadowed_setattr(model, setattr):
    setattr(model, "required", True)


def lambda_parameter(field, items):
    extras = field.extras
    return list(map(lambda extras: extras.update({}), items))


def comprehension_target(field, items):
    extras = field.extras
    [extras.update({}) for extras in items]
    extras["after"] = 1


def schema_object_in_a_string(obj: list["JsonSchemaObject"], other: "JsonSchemaObject | None"):
    obj.required = True
    other.required = True


def capture_rebinds(field, subject):
    extras = field.extras
    match subject:
        case [extras]:
            extras["a"] = 1


def import_rebinds(field):
    extras = field.extras
    import extras

    extras["a"] = 1


def def_rebinds(field):
    extras = field.extras

    def extras():
        pass

    extras["a"] = 1


def annotation_keeps_the_alias(field):
    extras = field.extras
    extras: dict
    extras["a"] = 1


def in_place_operator_keeps_the_alias(field):
    extras = field.extras
    extras |= {}
    extras["a"] = 1


def bound_twice(field, other):
    extras = field.extras
    extras = other
    extras["a"] = 1
    again = other
    again = field.extras
    again["b"] = 2


def uncalled_nonlocal_keeps_the_alias(field):
    extras = field.extras

    def inner():
        nonlocal extras
        extras = {}

    extras["a"] = 1


def right_side_reads_the_old_binding(field):
    extras = field.extras
    extras = extras.pop("a")
    other = field.extras
    other: dict = other.pop("b")
    third = field.extras
    (third := third.pop("c"))
