class Schema:
    def __init__(self) -> None:
        self.title = ""


class SchemaGraphBuilder:
    def __init__(self) -> None:
        self.shared = Schema()

    def schema(self) -> Schema:
        return self.shared
