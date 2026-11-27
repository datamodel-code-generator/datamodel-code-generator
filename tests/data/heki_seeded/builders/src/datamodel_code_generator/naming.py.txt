from datamodel_code_generator.model import Ref as Named


def rename() -> Named:
    return Named()


def construct() -> Named:
    return Named.model_construct()
