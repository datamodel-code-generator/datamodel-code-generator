"""Check that a partial update of some router groups leaves every other group of the previous generation unchanged."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from typing_extensions import TypeIs

from datamodel_code_generator._api_manifest import runtime_revision, sha256
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic

if TYPE_CHECKING:
    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_manifest import JSONObject
    from datamodel_code_generator._api_types import DiagnosticStage
    from datamodel_code_generator._fastapi.config import FastAPIConfig
    from datamodel_code_generator._fastapi.plan import ServerPlan

_SELECTED: Final = "/selection/selected_operations/"
_FINGERPRINTS: Final = ("plan_sha256", "signature_sha256", "codec_sha256", "docs_sha256")


def check_partial(plan: ServerPlan, config: FastAPIConfig, request: TargetRequest, data: JSONObject) -> None:
    """Reject a partial update that names unknown groups, has no earlier generation, or changes another group."""
    groups = config.update_groups
    assert groups is not None
    known = {group.key for group in plan.groups}
    manifest = request.state.manifest
    unknown = [group for group in groups if group not in known]
    if problems := [
        message
        for failed, message in (
            (not groups, "update_groups names no group"),
            (bool(unknown), f"update_groups names groups this server does not have: {', '.join(unknown)}"),
            (manifest is None, "update_groups needs an earlier generation of this target"),
        )
        if failed
    ]:
        raise APIGenerationError(
            tuple(_diagnostic("F_SELECTION_INVALID", "selection", message, request) for message in problems)
        )
    assert manifest is not None
    if problems := list(_inconsistencies(plan, config, request, data, manifest, groups=frozenset(groups))):
        raise APIGenerationError(
            tuple(_diagnostic("F_PARTIAL_UPDATE_INCONSISTENT", "ownership", message, request) for message in problems)
        )


def _inconsistencies(  # noqa: PLR0913
    plan: ServerPlan,
    config: FastAPIConfig,
    request: TargetRequest,
    data: JSONObject,
    manifest: JSONObject,
    *,
    groups: frozenset[str],
) -> list[str]:
    selected = _list(_at(manifest, "selection", "selected_operations"))
    fastapi = _at(manifest, "target_data", "fastapi")
    problems: list[str] = []
    if _at(fastapi, "layout") != config.layout:
        problems.append("The layout setting changed; update every group")
    if [_pointer(item) for item in selected] != [operation.id.use_site.pointer for operation in request.operations]:
        problems.append("The selected operations changed; update every group")
    if _at(manifest, "generator", "runtime_revision") != runtime_revision():
        problems.append("The private runtime changed; update every group")
    previous = {
        _pointer(selected[int(index)]): record
        for record in _list(_at(fastapi, "operations"))
        if isinstance(pointer := _at(record, "operation"), str)
        and (index := pointer.removeprefix(_SELECTED)).isdecimal()
        and int(index) < len(selected)
    }
    current = dict(zip((spec.key for spec in plan.operations), _list(data["operations"]), strict=True))
    problems.extend(
        f"{key} changed outside the updated groups"
        for key, record in current.items()
        if _at(record, "group_key") not in groups
        and any(_at(record, name) != _at(previous.get(key), name) for name in _FINGERPRINTS)
    )
    problems.extend(
        f"{key} disappeared outside the updated groups"
        for key, record in previous.items()
        if key not in current and _at(record, "group_key") not in groups
    )
    recorded = {_at(entry, "path"): _at(entry, "sha256") for entry in _list(_at(manifest, "model", "artifacts"))}
    problems.extend(
        f"The model artifact {name} changed; update every group"
        for model in request.models
        if recorded.get(name := "/".join(model.path)) != sha256(model.content)
    )
    return problems


def _at(value: object, *keys: str) -> object:
    for key in keys:
        value = value.get(key) if _is_dict(value) else None
    return value


def _list(value: object) -> list[object]:
    return value if _is_list(value) else []


def _is_dict(value: object) -> TypeIs[dict[object, object]]:
    return isinstance(value, dict)


def _is_list(value: object) -> TypeIs[list[object]]:
    return isinstance(value, list)


def _pointer(reference: object) -> object:
    return _at(reference, "pointer") if _at(reference, "document") == "/inputs/root" else None


def _diagnostic(code: str, stage: DiagnosticStage, message: str, request: TargetRequest) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity="error",
        stage=stage,
        message=message,
        option_path="update_groups",
        target_id=request.target_id,
    )
