import importlib

import datamodel_code_generator as dcg
from datamodel_code_generator import _source
import datamodel_code_generator.model.typed_dict
from datamodel_code_generator.model import msgspec
from importlib import import_module as load_backend
from ..model.pydantic_v2 import RootModel

BACKEND_MODULE = "datamodel_code_generator.model.dataclass"
BACKEND_MODULE_ALIAS = BACKEND_MODULE
TYPED_BACKEND_MODULE_ALIAS: str = BACKEND_MODULE
OVERWRITTEN_BACKEND_MODULE = "datamodel_code_generator.model.dataclass"
importlib.import_module(OVERWRITTEN_BACKEND_MODULE)
OVERWRITTEN_BACKEND_MODULE = None


def load_backends():
    importlib.import_module(BACKEND_MODULE_ALIAS)
    load_backend(TYPED_BACKEND_MODULE_ALIAS)
    return __import__("datamodel_code_generator.model.pydantic_base")


def load_private_facade_helper():
    return dcg._source


def inspect_backend(value):
    direct = value.is_pydantic_extra_field
    helpers = getattr(value, "SCHEMA_RUNTIME_VALIDATION_HELPERS_TEMPLATE_FILE_PATH", None)
    identity = value.__module__ == "datamodel_code_generator.model.pydantic_v2.base_model" and value.__name__ == (
        "DataModelField"
    )
    return direct, helpers, identity, msgspec, RootModel


async def inspect_async_backend(value):
    return value.is_pydantic_extra_field


def build_backend(value):
    return value(module=BACKEND_MODULE)


def inspect_output_metadata(model, python_version):
    model._append_internal_template_data("class_body_lines", "__hash__ = object.__hash__")
    return (
        model._internal_template_data,
        getattr(model, "_internal_template_data", {}),
        model.PLAIN_PATTERN_ROOT_TYPES,
        python_version.has_typed_dict_closed,
        getattr(python_version, "has_typed_dict_non_required"),
    )


def select_output_policy():
    from datamodel_code_generator.reference import ModelType as NamingType
    from datamodel_code_generator.enums import DataModelType as OutputType

    return NamingType.PYDANTIC, NamingType.MSGSPEC, OutputType.MsgspecStruct


def neutral_output_capabilities(model, field):
    from datamodel_code_generator.reference import ModelType

    return (
        ModelType.CLASS,
        ModelType.ENUM,
        model.has_model_config,
        model.has_runtime_object_validation,
        model.schema_runtime_validation,
        model.FIELDS_CREATE_CLASS_DESCRIPTORS,
        model.PLAIN_PATTERN_ROOT_CHECKER,
        field.data_type,
    )
