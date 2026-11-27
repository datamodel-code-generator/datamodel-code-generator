class DataModel:
    def __init__(self) -> None:
        self.fields: list[str] = []
        self.set_base_class()

    def set_base_class(self) -> None:
        self.fields = []


class DataModelFieldBase:
    def __init__(self) -> None:
        self.extras: dict[str, object] = {}
