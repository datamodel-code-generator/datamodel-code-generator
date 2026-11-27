"""Shared Pydantic v2 ConfigDict generation helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from datamodel_code_generator._requirements import pydantic_target_meets
from datamodel_code_generator.enums import TargetPydanticVersion
from datamodel_code_generator.model._decisions.extra_keys import CONFIG_EXTRA_KEYS, ConfigExtra, get_config_extra

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from datamodel_code_generator.types import DataType


class ConfigAttribute(NamedTuple):
    """Configuration attribute mapping for ConfigDict conversion."""

    from_: str
    to: str
    invert: bool


ConfigParameterValue = bool | ConfigExtra | Literal['"python-re"'] | dict[str, Any]


def config_attributes(*, by_name: str, frozen: bool) -> list[ConfigAttribute]:
    """Return the template-data keys a ``ConfigDict`` takes, writing population by field name as ``by_name``.

    A Pydantic dataclass takes ``frozen`` as a decorator argument (``frozen=False``) instead.
    """
    return [
        ConfigAttribute("allow_population_by_field_name", by_name, invert=False),
        ConfigAttribute("populate_by_name", by_name, invert=False),
        *(
            (
                ConfigAttribute("allow_mutation", "frozen", invert=True),
                ConfigAttribute("frozen", "frozen", invert=False),
            )
            if frozen
            else ()
        ),
        ConfigAttribute("use_attribute_docstrings", "use_attribute_docstrings", invert=False),
    ]


def get_config_attributes(
    extra_template_data: dict[str, Any],
    *,
    config_attributes_v2: list[ConfigAttribute],
    config_attributes_v2_11: list[ConfigAttribute],
) -> list[ConfigAttribute]:
    """Get config attributes based on target Pydantic version."""
    target_version = extra_template_data.get("target_pydantic_version")
    if isinstance(target_version, TargetPydanticVersion | str) and pydantic_target_meets(
        "pydantic.validate_by_name", target_version
    ):
        return config_attributes_v2_11
    return config_attributes_v2


ARBITRARY_TYPES_ALLOWED_KEY = "arbitrary_types_allowed"
_LEADING_CONFIG_KEYS: frozenset[str] = frozenset({
    "extra",
    "populate_by_name",
    "validate_by_name",
    "frozen",
    "use_attribute_docstrings",
})
"""The items that ``build_base_config_parameters`` writes before ``arbitrary_types_allowed``."""


def names_class_by_text(data_type: DataType) -> bool:
    """Return whether the data type is a class named by text (``customTypePath``).

    Its class is unknown without importing it, so the model that declares it assumes the class needs
    ``arbitrary_types_allowed``; a model that builds the declaration again from a base answers the same.
    """
    return data_type.is_custom_type


def with_arbitrary_types_allowed(config_parameters: Mapping[str, object]) -> dict[str, object]:
    """Return the config items with ``arbitrary_types_allowed`` where ``build_base_config_parameters`` puts it.

    It comes after the items of the model's own settings (``extra``, population by name, ``frozen``, attribute
    docstrings) and before the others, where a dataclass, which keeps the items in order, writes it.
    """
    items = list(config_parameters.items())
    index = next((index for index, (key, _) in enumerate(items) if key not in _LEADING_CONFIG_KEYS), len(items))
    return {**dict(items[:index]), ARBITRARY_TYPES_ALLOWED_KEY: True, **dict(items[index:])}


def build_base_config_parameters(
    *,
    extra_template_data: dict[str, Any],
    all_data_types: Iterable[DataType],
    config_attributes_v2: list[ConfigAttribute],
    config_attributes_v2_11: list[ConfigAttribute],
    include_extra: bool = True,
) -> dict[str, ConfigParameterValue]:
    """Build shared ConfigDict parameters for Pydantic v2 models.

    A class named by text (``names_class_by_text``, a ``customTypePath`` type) in the fields at ``__init__`` writes
    ``arbitrary_types_allowed``, and the config keeps it where ``--type-overrides`` later replaces the type (row
    ``legacy-custom-type-config-at-init``): the class is unknown, so the model assumes it needs the key. A dataclass
    that builds such a declaration again from a base, with the config it applies, gets the key once the field types
    are final, from the same reading; so do the classes of an input model, which the model's own config allows
    (``_PydanticV2Model.finalize_type_alias_users``).
    """
    config_attributes = get_config_attributes(
        extra_template_data,
        config_attributes_v2=config_attributes_v2,
        config_attributes_v2_11=config_attributes_v2_11,
    )
    if (
        all_data_types == ()
        and not (include_extra and not CONFIG_EXTRA_KEYS.isdisjoint(extra_template_data))
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

    if any(names_class_by_text(data_type) for data_type in all_data_types):
        config_parameters[ARBITRARY_TYPES_ALLOWED_KEY] = True

    return config_parameters
