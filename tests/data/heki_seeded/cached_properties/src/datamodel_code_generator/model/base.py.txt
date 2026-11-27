from functools import cached_property


class DataModel:
    @cached_property
    def path(self) -> str:
        return ""


class DataModelFieldBase:
    pass
