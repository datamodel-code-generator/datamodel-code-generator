from pydantic import RootModel

HeaderRoot = RootModel[str]
_PrivateRoot = RootModel[bool]


class HeaderContainer:
    ImportedIntegerRoot = RootModel[bytes]


HeaderContainer.marker = 1
