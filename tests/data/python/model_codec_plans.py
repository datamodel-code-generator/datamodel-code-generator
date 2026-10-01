"""Generate client packages from wire plan fixtures, reporting refusals and validating instances offline."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from tests.data.python.model_codec_builtin import failure, generate_package, imported

PLANS = Path(__file__).parents[1] / "generation_platform" / "codecs" / "plan"
BACKEND = "pydantic_v2.BaseModel"
PINNED = (("_generated", "model_bindings.py"), ("_operations.py",))


def _validation(package: Any, case: dict[str, Any]) -> str:
    """Validate one instance against its schema in each direction's generated bundle."""
    outcomes = []
    for direction in ("request", "response"):
        bundle = getattr(package.bindings, f"{direction}_bundle")()
        try:
            issues = bundle.validator(case["schema"]).validate(package.public.freeze_wire(case["value"]))
        except package.public.CodecError as error:
            outcomes.append(f"{direction} {failure(package, error)}")
            continue
        outcomes.append(f"{direction} {','.join(f'{item.code}@{item.instance_pointer}' for item in issues) or 'valid'}")
    return f"{case['schema']} {package.json(case['value'])} -> {' | '.join(outcomes)}"


def wire_plan_report(name: str, root: Path) -> tuple[str, dict[tuple[str, ...], str]]:
    """Generate one fixture's client package, reporting a refusal or instances validated by its bundles.

    A fixture's `refusal` configuration first reports the diagnostics of a generation the target refuses; its
    `config` then generates the package whose bindings and operations the second value holds.
    """
    case = json.loads((PLANS / "cases.json").read_text(encoding="utf-8"))[name]
    shutil.copytree(PLANS, root / "inputs")
    source = root / "inputs" / f"{name}.yaml"
    fixture = {"package": (package := f"plan_{name}"), "options": case.get("model", {})}
    lines = [f"# {name}"]
    if isinstance(refused := case.get("refusal"), dict):
        refusal = generate_package(source, fixture, root / "refused", BACKEND, refused)
        lines.extend(f"refused {line}" for line in refusal or ["nothing"])
    if refusal := generate_package(source, fixture, root / "accepted", BACKEND, case.get("config", {})):
        return "\n".join([*lines, *refusal]) + "\n", {}
    directory = root / "accepted" / package
    modules = {parts: directory.joinpath(*parts).read_text(encoding="utf-8") for parts in PINNED}
    if "instances" in case:
        with imported(root / "accepted", package) as generated:
            lines.extend(_validation(generated, item) for item in generated.load(PLANS / case["instances"]))
    return "\n".join(lines) + "\n", modules
