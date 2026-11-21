"""#7026: attributes stored on the receiver through tuple, starred, list, for, with and annotated targets.

questions: bindings
"""


class Parser:
    kind = "json"

    def parse(self):
        self.store, self.results = self._factory()
        (self.extra, *self.rest) = (0, 1)
        [self.first, self.second] = 1, 2
        for self.cursor in range(3):
            pass
        with open("x") as self.handle:
            pass
        self.typed: int = 0
        self.__private = 1

    def _factory(self):
        return 1, 2
