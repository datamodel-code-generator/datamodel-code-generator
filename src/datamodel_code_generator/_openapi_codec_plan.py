"""Shared native backend names and model artifact import locations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from datamodel_code_generator._target_contract import ModelArtifactAddress

PydanticBackend: TypeAlias = Literal["pydantic_v2.BaseModel", "pydantic_v2.dataclass"]


def artifact_module(artifact: ModelArtifactAddress) -> str:
    """Return the dotted module path of a model artifact below its model package."""
    *parents, name = artifact.relative_path
    modules = () if artifact.result_key == "single" else (*parents, name.removesuffix(".py"))
    return ".".join((artifact.model_package, *modules)).removesuffix(".__init__")
