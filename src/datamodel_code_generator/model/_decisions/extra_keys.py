"""Extra keys: what a model does with the keys it does not declare, read from its template data.

The functions read only their argument: the template data of a model (``DataModel.extra_template_data``), where the
parser stored what the schema's ``additionalProperties`` and ``unevaluatedProperties`` say. ``extra_fields`` and the
options that set ``allow_extra_fields`` come before the schema's keywords.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["CONFIG_EXTRA_KEYS", "ConfigExtra", "get_config_extra", "keeps_extra_keys"]

ConfigExtra: TypeAlias = Literal["'allow'", "'forbid'", "'ignore'"]
CONFIG_EXTRA_KEYS: Final[frozenset[str]] = frozenset({
    "additionalProperties",
    "allow_extra_fields",
    "extra_fields",
    "force_extra_allow",
    "unevaluatedProperties",
})
"""The template-data keys ``get_config_extra`` reads."""


def get_config_extra(extra_template_data: Mapping[str, object]) -> ConfigExtra | None:
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


def keeps_extra_keys(extra_template_data: Mapping[str, object]) -> bool:
    """Return whether a model with this template data keeps the keys it does not declare (``extra='allow'``)."""
    return get_config_extra(extra_template_data) == "'allow'"
