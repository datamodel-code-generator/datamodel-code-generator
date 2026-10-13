"""Model settings that select a backend for a server or client target."""

from __future__ import annotations

from datamodel_code_generator import DataModelType, TargetPydanticVersion


def backend_settings(backend: str | DataModelType) -> dict[str, object]:
    """Return the model type of a backend, with the MISSING sentinel that a target needs for Pydantic dataclasses.

    The sentinel is imported from pydantic, as a target imports it, so that model-only runs write the same models.
    """
    if (model_type := DataModelType(backend)) is DataModelType.PydanticV2Dataclass:
        return {
            "output_model_type": model_type,
            "use_missing_sentinel": True,
            "target_pydantic_version": TargetPydanticVersion.V2_14,
        }
    return {"output_model_type": model_type}
