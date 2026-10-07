"""Pydantic v2 feature boundaries keyed on the target Pydantic version, never on the installed one."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from datamodel_code_generator.enums import TargetPydanticVersion, _is_pydantic_version_at_least, _pydantic_version_key

if TYPE_CHECKING:
    from datamodel_code_generator.model.base import DataModel

_PYDANTIC_V2_MODEL_MODULE_PREFIX = "datamodel_code_generator.model.pydantic_v2."
NEWEST_TARGET_PYDANTIC_VERSION: Final = max(TargetPydanticVersion, key=_pydantic_version_key)
PYDANTIC_V2_STRING_CONSTRAINTS_MINIMUM: Final = "2.1"
PYDANTIC_V2_DATACLASS_ALIAS_MINIMUM: Final = "2.4"
PYDANTIC_V2_FIELD_DEPRECATED_MINIMUM: Final = "2.7"
PYDANTIC_V2_DICT_KEY_FORWARD_REF_MINIMUM: Final = "2.8"
PYDANTIC_V2_DATACLASS_TYPE_ALIAS_MINIMUM: Final = "2.10"
PYDANTIC_V2_PROTECTED_NAMESPACES_MINIMUM: Final = "2.10"
PYDANTIC_V2_VALIDATE_BY_NAME_MINIMUM: Final = "2.11"


def target_supports(target_version: TargetPydanticVersion | str | None, minimum: str) -> bool:
    """Return whether every Pydantic the target allows has a feature added in ``minimum``; unset is the newest."""
    return _is_pydantic_version_at_least(
        NEWEST_TARGET_PYDANTIC_VERSION if target_version is None else target_version, minimum
    )


def model_target_supports(model: DataModel | None, minimum: str) -> bool:
    """Return whether the target recorded for ``model`` has a feature added in ``minimum``."""
    return target_supports(None if model is None else model.extra_template_data.get("target_pydantic_version"), minimum)


def _includes_dict_key_reference_classes(model: DataModel) -> bool:
    """Order dict-key references first for built-in models whose target predates forward-ref dict keys."""
    return type(model).__module__.startswith(_PYDANTIC_V2_MODEL_MODULE_PREFIX) and not model_target_supports(
        model, PYDANTIC_V2_DICT_KEY_FORWARD_REF_MINIMUM
    )
