from pydantic import RootModel

HeaderRoot = RootModel[str]
_PrivateRoot = RootModel[bool]


class HeaderContainer:
    ImportedIntegerRoot = RootModel[bytes]


HeaderContainer.marker = 1


from pydantic import Field, RootModel

from tests.data.dynamic_models.root_model_alias_imports import ImportedIntegerRoot

GeneratedRoot = RootModel[int]