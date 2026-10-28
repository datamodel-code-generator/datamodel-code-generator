from collections.abc import Callable

import importlib as import_provider


CONCRETE_BACKEND = "datamodel_code_generator.model.pydantic_v2.base_model"

load_module = import_provider.import_module


def use_shadowed_alias(load_module: Callable[[str], None]) -> None:
    load_module(CONCRETE_BACKEND)


class NeutralImporter:
    def import_module(self, module: str) -> None:
        pass


neutral_provider = NeutralImporter()
neutral_loader = neutral_provider.import_module
neutral_loader(CONCRETE_BACKEND)
NeutralImporter().import_module(CONCRETE_BACKEND)

def use_shadowed_provider(import_provider: NeutralImporter) -> None:
    import_provider.import_module(CONCRETE_BACKEND)


import_provider = NeutralImporter()
import_provider.import_module(CONCRETE_BACKEND)
