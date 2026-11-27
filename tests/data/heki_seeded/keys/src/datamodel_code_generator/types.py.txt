_LISTED_KEY = "_listed"
_BOOKKEEPING_KEY = "_bookkeeping"
_GONE_KEY = "_gone"
_GONE_BOOKKEEPING_KEY = "_gone_bookkeeping"
_READ_ONLY_KEY = "_read_only"
_MODULE_KEY = "_new_module"


class DataType:
    def __init__(self) -> None:
        self.type = ""

    def read(self) -> object:
        return self.__dict__.get(_READ_ONLY_KEY)
