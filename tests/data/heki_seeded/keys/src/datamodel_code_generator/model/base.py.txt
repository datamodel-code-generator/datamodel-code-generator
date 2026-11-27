class DataModel:
    def __init__(self) -> None:
        self.fields: list[str] = []


class DataModelFieldBase:
    def __init__(self) -> None:
        self.name = ""
