"""Model settings that select a backend for a server or client target."""

from __future__ import annotations

from datamodel_code_generator import DataModelType, TargetPydanticVersion


def backend_settings(backend: str | DataModelType) -> dict[str, object]:
    """Return the model type of a backend, with the settings that a target needs for it.

    Pydantic dataclasses need the MISSING sentinel, imported from pydantic as a target imports it, and msgspec structs
    need tagged unions, so that model-only runs write the same models.
    """
    match model_type := DataModelType(backend):
        case DataModelType.PydanticV2Dataclass:
            return {
                "output_model_type": model_type,
                "use_missing_sentinel": True,
                "target_pydantic_version": TargetPydanticVersion.V2_14,
            }
        case DataModelType.MsgspecStruct:
            return {"output_model_type": model_type, "use_msgspec_tagged_unions": True}
    return {"output_model_type": model_type}


def backend_arguments(backend: str | DataModelType) -> list[str]:
    """Return the command-line flags of the switched-on settings that a target needs for a backend."""
    return [f"--{name.replace('_', '-')}" for name, value in backend_settings(backend).items() if value is True]
