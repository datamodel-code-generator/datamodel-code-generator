class Row:
    def __init__(self, name: str, applies: object, build: object) -> None:
        self.build = build


class Default:
    def __init__(self, name: str, build: object) -> None:
        self.build = build


class DispatchTable:
    def __init__(self, entries: tuple[Row, ...], default: Default) -> None:
        self.entries = entries

    def choose(self, parser: object, site: object) -> object:
        return self.entries[0].build

    def typed_children(self, parser: object, site: object) -> tuple[Row, ...]:
        return self.entries
