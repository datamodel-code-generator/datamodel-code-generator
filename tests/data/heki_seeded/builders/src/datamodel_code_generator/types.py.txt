class DataType:
    def __init__(self, *, is_dict: bool = False, is_list: bool = False) -> None:
        self.is_dict = is_dict
        self.is_list = is_list
