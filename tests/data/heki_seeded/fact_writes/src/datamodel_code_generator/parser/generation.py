from datamodel_code_generator.model.base import DataModel


class GenerationStore:
    def set_fields(self, model: DataModel, fields: list[str]) -> None:
        model.fields = fields

    def reset_base_classes(self, model: DataModel) -> None:
        model.set_base_class()
