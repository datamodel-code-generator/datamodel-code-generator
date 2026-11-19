"""The names of the definitions around a scope, as the source nests them: no ``<locals>``, lambdas or comprehensions.

``rebased`` is declared global, so its qualname restarts at the module; its nested name keeps ``outer``.
nested-names: yes
"""


class Outer:
    def method(self):
        def inner():
            return [lambda: (x for x in ()) for _ in ()]

        return inner

    class Nested:
        attribute = [value for value in ()]


def outer():
    global rebased

    def rebased():
        pass

    return lambda: [item for item in ()]
