import sys

CONCRETE_BACKEND = "datamodel_code_generator.model.pydantic_v2.base_model"
registry = {}


def rebuild_namespace(model_type: type[object]) -> dict[str, object]:
    return vars(sys.modules[model_type.__module__])


def parameter_shadow(registry: object, concrete_backend: str) -> None:
    registry[concrete_backend]


def conditional_neutral_backend(enabled: bool) -> None:
    local_registry = sys.modules
    local_backend = "neutral.module"
    if enabled:
        local_backend = "another.neutral.module"
    local_registry[local_backend]


def shadowed_import_aliases() -> None:
    import other as sys

    sys.modules[CONCRETE_BACKEND]
    from other import modules as loaded_modules

    loaded_modules[CONCRETE_BACKEND]


def first_sibling() -> None:
    local_registry = sys.modules


def second_sibling() -> None:
    local_registry[CONCRETE_BACKEND]


class ClassScope:
    registry = sys.modules

    def method(self) -> None:
        registry.get(CONCRETE_BACKEND)
