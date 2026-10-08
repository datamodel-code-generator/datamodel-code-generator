"""Model generation module.

Provides factory functions and classes for generating different output formats
(Pydantic, dataclasses, TypedDict, msgspec) based on configuration.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, NamedTuple

from datamodel_code_generator._format_types import PythonVersion

from .base import UNDEFINED, ConstraintsBase, DataModel, DataModelFieldBase, _rebuild_model_with_datamodel_namespace

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from datamodel_code_generator import DataModelType
    from datamodel_code_generator.enums import TargetPydanticVersion
    from datamodel_code_generator.types import DataTypeManager as DataTypeManagerABC

DEFAULT_TARGET_PYTHON_VERSION = PythonVersion(f"{sys.version_info.major}.{sys.version_info.minor}")


class DataModelSet(NamedTuple):
    """Collection of model types needed for a specific output format."""

    data_model: type[DataModel]
    root_model: type[DataModel]
    field_model: type[DataModelFieldBase]
    data_type_manager: type[DataTypeManagerABC]
    dump_resolve_reference_action: Callable[[Iterable[str]], str] | None
    scalar_model: type[DataModel]
    union_model: type[DataModel]
    known_third_party: list[str] | None = None


def get_data_model_types(  # noqa: PLR0912, PLR0913, PLR0917
    data_model_type: DataModelType,
    target_python_version: PythonVersion = DEFAULT_TARGET_PYTHON_VERSION,
    use_type_alias: bool = False,  # noqa: FBT001, FBT002
    use_root_model_type_alias: bool = False,  # noqa: FBT001, FBT002
    include_graphql_models: bool = True,  # noqa: FBT001, FBT002
    use_type_alias_type: bool = False,  # noqa: FBT001, FBT002
    target_pydantic_version: TargetPydanticVersion | None = None,
) -> DataModelSet:
    """Get the model types for the output format, target Python version, and target Pydantic version."""
    from datamodel_code_generator import DataModelType  # noqa: PLC0415

    pydantic_v2_models = {DataModelType.PydanticV2BaseModel, DataModelType.PydanticV2Dataclass}

    def get_auxiliary_model_types() -> tuple[type[DataModel], type[DataModel], type[DataModel]]:
        from . import scalar, type_alias, union  # noqa: PLC0415

        if target_python_version.has_type_statement:
            return type_alias.TypeStatement, scalar.DataTypeScalarTypeStatement, union.DataTypeUnionTypeStatement
        if use_type_alias_type or data_model_type in pydantic_v2_models:
            return type_alias.TypeAliasTypeBackport, scalar.DataTypeScalarTypeBackport, union.DataTypeUnionTypeBackport
        return type_alias.TypeAlias, scalar.DataTypeScalar, union.DataTypeUnion

    match data_model_type:
        case DataModelType.PydanticV2BaseModel:
            from . import pydantic_v2  # noqa: PLC0415

            if (
                (uses_alias := use_type_alias or use_type_alias_type)
                or include_graphql_models
                or use_root_model_type_alias
            ):
                type_alias_class, scalar_class, union_class = get_auxiliary_model_types()
            else:
                scalar_class = union_class = pydantic_v2.RootModel

            if uses_alias:
                root_model_class: type[DataModel] = type_alias_class
            elif use_root_model_type_alias:
                root_model_class = pydantic_v2.RootModelTypeAlias
            else:
                root_model_class = pydantic_v2.RootModel
            return DataModelSet(
                data_model=pydantic_v2.BaseModel,
                root_model=root_model_class,
                field_model=pydantic_v2.DataModelField,
                data_type_manager=pydantic_v2.DataTypeManager,
                dump_resolve_reference_action=pydantic_v2.dump_resolve_reference_action,
                scalar_model=scalar_class,
                union_model=union_class,
            )
        case DataModelType.PydanticV2Dataclass:
            from . import pydantic_v2  # noqa: PLC0415
            from .pydantic_v2 import dataclass as pydantic_v2_dataclass  # noqa: PLC0415
            from .pydantic_v2.version import (  # noqa: PLC0415
                PYDANTIC_V2_DATACLASS_ALIAS_MINIMUM,
                PYDANTIC_V2_DATACLASS_TYPE_ALIAS_MINIMUM,
                target_supports,
            )

            type_alias_class, scalar_class, union_class = get_auxiliary_model_types()
            if not target_supports(target_pydantic_version, PYDANTIC_V2_DATACLASS_TYPE_ALIAS_MINIMUM):
                from .pydantic_v2.type_alias import TypeAlias as CompatibleTypeAlias  # noqa: PLC0415

                type_alias_class = CompatibleTypeAlias
            return DataModelSet(
                data_model=pydantic_v2_dataclass.DataClass,
                root_model=type_alias_class,
                field_model=pydantic_v2_dataclass.DataModelField
                if target_supports(target_pydantic_version, PYDANTIC_V2_DATACLASS_ALIAS_MINIMUM)
                else pydantic_v2_dataclass.DataModelFieldBackport,
                data_type_manager=pydantic_v2.DataTypeManager,
                dump_resolve_reference_action=None,
                scalar_model=scalar_class,
                union_model=union_class,
            )
        case DataModelType.DataclassesDataclass:
            from . import dataclass  # noqa: PLC0415

            type_alias_class, scalar_class, union_class = get_auxiliary_model_types()
            return DataModelSet(
                data_model=dataclass.DataClass,
                root_model=type_alias_class,
                field_model=dataclass.DataModelField,
                data_type_manager=dataclass.DataTypeManager,
                dump_resolve_reference_action=None,
                scalar_model=scalar_class,
                union_model=union_class,
            )
        case DataModelType.TypingTypedDict:
            from . import typed_dict  # noqa: PLC0415
            from .types import DataTypeManager  # noqa: PLC0415

            type_alias_class, scalar_class, union_class = get_auxiliary_model_types()
            if target_python_version.has_typed_dict_read_only:
                typed_dict_field_model: type[DataModelFieldBase] = typed_dict.DataModelField
            elif target_python_version.has_typed_dict_non_required:
                typed_dict_field_model = typed_dict.DataModelFieldReadOnlyBackport
            else:
                typed_dict_field_model = typed_dict.DataModelFieldBackport
            return DataModelSet(
                data_model=typed_dict.TypedDict,
                root_model=type_alias_class,
                field_model=typed_dict_field_model,
                data_type_manager=DataTypeManager,
                dump_resolve_reference_action=None,
                scalar_model=scalar_class,
                union_model=union_class,
            )
        case DataModelType.MsgspecStruct:
            from . import msgspec  # noqa: PLC0415

            type_alias_class, scalar_class, union_class = get_auxiliary_model_types()
            return DataModelSet(
                data_model=msgspec.Struct,
                root_model=type_alias_class,
                field_model=msgspec.DataModelField,
                data_type_manager=msgspec.DataTypeManager,
                dump_resolve_reference_action=None,
                known_third_party=["msgspec"],
                scalar_model=scalar_class,
                union_model=union_class,
            )
    msg = f"{data_model_type} is unsupported data model type"  # pragma: no cover
    raise ValueError(msg)  # pragma: no cover


__all__ = [
    "DEFAULT_TARGET_PYTHON_VERSION",
    "UNDEFINED",
    "ConstraintsBase",
    "DataModel",
    "DataModelFieldBase",
    "DataModelSet",
    "_rebuild_model_with_datamodel_namespace",
    "get_data_model_types",
]
