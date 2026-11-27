from datamodel_code_generator.types import DataType


class GenerationStore:
    def mark(self, data_type: DataType) -> None:
        data_type.__dict__["_store_key"] = 1
