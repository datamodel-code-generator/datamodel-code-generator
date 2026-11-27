from datamodel_code_generator.model.base import DataModel


class Enum(DataModel):
    def mark(self) -> None:
        self.__dict__["_new_own"] = 1
