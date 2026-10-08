"""Generate FastAPI server packages from wire plan fixtures, reporting refusals and native model validation."""

from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from tests.data.python.generated_packages import generated_root
from tests.data.python.model_codec_builtin import generate_package

PLANS = Path(__file__).parents[1] / "generation_platform" / "codecs" / "plan"
BACKEND = "pydantic_v2.BaseModel"
PINNED = (("application.py",), ("_generated", "contract.py"))
PARAMETER_MODELS = {
    "/paths/~1items/get/parameters/0/schema": "FieldItemsGetQueryLimitParameter",
    "/paths/~1pets~1%7BpetId%7D/get/parameters/1/schema": "Filter",
    "/paths/~1pets~1%7BpetId%7D/parameters/0/schema": "FieldPetsPetIdGetPathPetIdParameter",
}


def _validation(models: Any, case: dict[str, Any]) -> str:
    """Validate the fixed instance with the generated native model used in either direction."""
    pointer = case["schema"].partition("#")[2]
    name = PARAMETER_MODELS[pointer] if pointer in PARAMETER_MODELS else pointer.rsplit("/", 1)[1]
    adapter = TypeAdapter(getattr(models, name))
    content = json.dumps(case["value"], separators=(",", ":"))
    outcomes = []
    for direction in ("request", "response"):
        try:
            adapter.dump_json(adapter.validate_json(content), by_alias=True, exclude_unset=True)
        except ValidationError as error:
            issues = ",".join(
                f"{item['type']}@/{'/'.join(str(part) for part in item['loc'])}"
                for item in error.errors(include_url=False)
            )
            outcomes.append(f"{direction} NativeValidationError {issues}")
            continue
        outcomes.append(f"{direction} valid")
    return f"{case['schema']} {content} -> {' | '.join(outcomes)}"


def wire_plan_report(name: str, root: Path) -> tuple[str, dict[tuple[str, ...], str]]:
    """Generate one fixture's server package, reporting a refusal or instances validated by its native models.

    A fixture's `refusal` configuration first reports the diagnostics of a generation the target refuses; its
    `config` then generates the package whose public application and route contract the second value holds.
    """
    case = json.loads((PLANS / "cases.json").read_text(encoding="utf-8"))[name]
    shutil.copytree(PLANS, root / "inputs")
    source = root / "inputs" / f"{name}.yaml"
    fixture = {"package": (package := f"plan_{name.replace('-', '_')}"), "options": case.get("model", {})}
    lines = [f"# {name}"]
    if isinstance(refusal_config := case.get("refusal"), dict):
        refused = generate_package(source, fixture, root / "refused", BACKEND, refusal_config, server=True)
        lines.extend(f"refused {line}" for line in refused or ["nothing"])
    accepted = root / "accepted"
    if diagnostics := generate_package(source, fixture, accepted, BACKEND, case.get("config", {}), server=True):
        return "\n".join([*lines, *diagnostics]) + "\n", {}
    directory = accepted / package
    modules = {parts: directory.joinpath(*parts).read_text(encoding="utf-8") for parts in PINNED}
    if "instances" in case:
        with generated_root(accepted, package):
            models = importlib.import_module(f"{package}_models")
            instances = json.loads((PLANS / case["instances"]).read_text(encoding="utf-8"))
            lines.extend(_validation(models, item) for item in instances)
    return "\n".join(lines) + "\n", modules
