"""Shared Pydantic v2 ConfigDict generation helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from datamodel_code_generator.enums import TargetPydanticVersion, _is_pydantic_version_at_least

if TYPE_CHECKING:
    from collections.abc import Iterable

    from datamodel_code_generator.types import DataType


class ConfigAttribute(NamedTuple):
    """Configuration attribute mapping for ConfigDict conversion."""

    from_: str
    to: str
    invert: bool


ConfigExtra = Literal["'allow'", "'forbid'", "'ignore'"]
ConfigParameterValue = bool | ConfigExtra | Literal['"python-re"'] | dict[str, Any]
_CONFIG_EXTRA_KEYS: frozenset[str] = frozenset({
    "additionalProperties",
    "allow_extra_fields",
    "extra_fields",
    "force_extra_allow",
    "unevaluatedProperties",
})


def get_config_extra(extra_template_data: dict[str, Any]) -> ConfigExtra | None:
    """Get extra field configuration for ConfigDict."""
    if extra_template_data.get("force_extra_allow"):
        return "'allow'"

    additional_properties = extra_template_data.get("additionalProperties")
    unevaluated_properties = extra_template_data.get("unevaluatedProperties")
    allow_extra_fields = extra_template_data.get("allow_extra_fields")
    extra_fields = extra_template_data.get("extra_fields")

    config_extra: ConfigExtra | None = None
    if allow_extra_fields or extra_fields == "allow":
        config_extra = "'allow'"
    elif extra_fields == "forbid":
        config_extra = "'forbid'"
    elif extra_fields == "ignore":
        config_extra = "'ignore'"
    elif additional_properties is True:
        config_extra = "'allow'"
    elif additional_properties is False:
        config_extra = "'forbid'"
    elif unevaluated_properties is True:
        config_extra = "'allow'"
    elif unevaluated_properties is False:
        config_extra = "'forbid'"
    return config_extra


def get_config_attributes(
    extra_template_data: dict[str, Any],
    *,
    config_attributes_v2: list[ConfigAttribute],
    config_attributes_v2_11: list[ConfigAttribute],
) -> list[ConfigAttribute]:
    """Get config attributes based on target Pydantic version."""
    target_version = extra_template_data.get("target_pydantic_version")
    match target_version:
        case TargetPydanticVersion() | str() if _is_pydantic_version_at_least(
            target_version, TargetPydanticVersion.V2_11
        ):
            return config_attributes_v2_11
        case _:
            return config_attributes_v2


def build_base_config_parameters(
    *,
    extra_template_data: dict[str, Any],
    all_data_types: Iterable[DataType],
    config_attributes_v2: list[ConfigAttribute],
    config_attributes_v2_11: list[ConfigAttribute],
    include_extra: bool = True,
) -> dict[str, ConfigParameterValue]:
    """Build shared ConfigDict parameters for Pydantic v2 models."""
    config_attributes = get_config_attributes(
        extra_template_data,
        config_attributes_v2=config_attributes_v2,
        config_attributes_v2_11=config_attributes_v2_11,
    )
    if (
        all_data_types == ()
        and not (include_extra and not _CONFIG_EXTRA_KEYS.isdisjoint(extra_template_data))
        and not any(from_ in extra_template_data for from_, _, _ in config_attributes)
    ):
        return {}

    config_parameters: dict[str, ConfigParameterValue] = {}

    if include_extra and (extra := get_config_extra(extra_template_data)):
        config_parameters["extra"] = extra

    for from_, to, invert in config_attributes:
        if from_ in extra_template_data:
            value: bool = extra_template_data[from_]
            config_parameters[to] = not value if invert else value

    for data_type in all_data_types:
        if data_type.is_custom_type:  # pragma: no cover
            config_parameters["arbitrary_types_allowed"] = True
            break

    return config_parameters
