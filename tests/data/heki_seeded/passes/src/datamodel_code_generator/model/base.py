class DataModelFieldBase:
    def __init__(self) -> None:
        self.read_only = False
        self.extras: dict[str, object] = {}
