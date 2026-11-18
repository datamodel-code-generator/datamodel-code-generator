"""Review R36, S2: a positional-only self is the receiver; a staticmethod's first parameter is not.

questions: bindings
"""


class C:
    def __init__(self, /, value):
        self.value = value

    @staticmethod
    def make(other):
        other.hidden = 1
