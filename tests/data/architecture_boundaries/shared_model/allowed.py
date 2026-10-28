import sys

CONCRETE_BACKEND = "datamodel_code_generator.model.pydantic_v2.base_model"
registry = {}
CLEARED_MODULE_BACKEND = CONCRETE_BACKEND
CLEARED_MODULE_BACKEND = object()
REASSIGNED_MODULE_BACKEND = CONCRETE_BACKEND


def rebuild_namespace(model_type: type[object]) -> dict[str, object]:
    return vars(sys.modules[model_type.__module__])


def parameter_shadow(registry: object, concrete_backend: str) -> None:
    registry[concrete_backend]


def reassigned_registry() -> None:
    local_registry = sys.modules
    local_registry = {}
    local_registry[CONCRETE_BACKEND]


def reassigned_string() -> None:
    local_registry: object = sys.modules
    local_backend = CONCRETE_BACKEND
    local_backend = None
    local_registry[local_backend]


def cleared_module_constant() -> None:
    local_registry = sys.modules
    local_registry[CLEARED_MODULE_BACKEND]


def conditional_neutral_backend(enabled: bool) -> None:
    local_registry = sys.modules
    local_backend = "neutral.module"
    if enabled:
        local_backend = "another.neutral.module"
    local_registry[local_backend]


def reassigned_module_constant() -> None:
    local_registry = sys.modules
    local_registry[REASSIGNED_MODULE_BACKEND]


def cleared_later_module_constant() -> None:
    local_registry = sys.modules
    local_registry[CLEARED_LATER_BACKEND]


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


REASSIGNED_MODULE_BACKEND = object()
CLEARED_LATER_BACKEND = CONCRETE_BACKEND
CLEARED_LATER_BACKEND = None
