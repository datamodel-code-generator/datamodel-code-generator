from datamodel_code_generator.model.base import DataModelFieldBase


class GenerationStore:
    def __init__(self) -> None:
        self.phase = 0

    def enter_phase(self, phase: int) -> None:
        self.phase = phase

    def set_field_extra(self, field: DataModelFieldBase, key: str, value: object) -> None:
        field.extras[key] = value
