from datamodel_code_generator.model.base import DataModelFieldBase
from datamodel_code_generator.parser.generation import GenerationStore


class Parser:
    def __init__(self) -> None:
        self.fields: list[DataModelFieldBase] = []
        self.store = GenerationStore()

    def _process_single_module(self) -> None:
        for field in self.fields:
            field.read_only = True
