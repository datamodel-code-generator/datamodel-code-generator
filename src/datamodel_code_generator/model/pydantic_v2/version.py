"""Pydantic v2 feature boundaries keyed on the target Pydantic version, never on the installed one.

The minimum of each construct and what an unset target means for it are rows of the requirement table in
``datamodel_code_generator._requirements``; this module reads the target recorded for a model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator._requirements import pydantic_target_meets

if TYPE_CHECKING:
    from datamodel_code_generator._requirements import PydanticConstruct
    from datamodel_code_generator.model.base import DataModel

_PYDANTIC_V2_MODEL_MODULE_PREFIX = "datamodel_code_generator.model.pydantic_v2."


def model_target_meets(model: DataModel | None, construct: PydanticConstruct) -> bool:
    """Return whether the target Pydantic version recorded for ``model`` has ``construct``."""
    return pydantic_target_meets(
        construct, None if model is None else model.extra_template_data.get("target_pydantic_version")
    )


def _includes_dict_key_reference_classes(model: DataModel) -> bool:
    """Order dict-key references first for built-in models whose target predates forward-ref dict keys."""
    return type(model).__module__.startswith(_PYDANTIC_V2_MODEL_MODULE_PREFIX) and not model_target_meets(
        model, "pydantic.dict_key_forward_ref"
    )
