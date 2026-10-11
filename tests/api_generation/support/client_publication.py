"""Report generated client publication profiles from the files their command-line runs write and print."""

from __future__ import annotations

import json
import shlex
import shutil
from typing import TYPE_CHECKING

from tests.api_generation.support.client_generation import SOURCE, client_options, copy_references

if TYPE_CHECKING:
    from pathlib import Path

PROFILES = json.loads((SOURCE / "publication" / "profiles.json").read_text(encoding="utf-8"))
PACKAGE = "publication_client"


def capability_arguments(profile: str, root: Path) -> list[str]:
    """Copy a profile's inputs under a root and spell its settings as the command line that generates them.

    The helper file is given by its path, so its documents resolve against its directory.
    """
    case = PROFILES[profile]
    (root / case["input"]).parent.mkdir(parents=True, exist_ok=True)
    source = shutil.copy2(SOURCE / case["input"], root / case["input"])
    copy_references(case, root)
    config = case.get("config", {})
    arguments = [
        *("--input", str(source), "--input-file-type", "openapi", "--output", str(root / f"{PACKAGE}_models.py")),
        *("--target-python-version", "3.11", "--openapi-scopes", "schemas", "api"),
        *("--output-model-type", case.get("backend", "pydantic_v2.BaseModel")),
        *("--formatters", "builtin", "--disable-timestamp", "--generate-client", "httpx2"),
        *("--client-output", str(root / PACKAGE), "--client-package", PACKAGE),
        *("--client-model-package", f"{PACKAGE}_models"),
    ]
    if paths := case.get("model", {}).get("openapi_include_paths"):
        arguments.extend(("--openapi-include-paths", *paths))
    if "protocols" in config:
        arguments.extend(("--client-protocols", str(root / config["protocols"])))
    if "operations" in config:
        operations = client_options({"operations": config["operations"]}, root, PACKAGE)["client_operations"]
        arguments.extend(("--client-operations", json.dumps(operations)))
    return arguments


def client_capability_report(profile: str, root: Path, stderr: str) -> str:
    """List the dependencies a profile's run asks to add, its public files and its copied-runtime module lines.

    The run writes the only Python files under the root.
    """
    backend = PROFILES[profile].get("backend", "pydantic_v2.BaseModel")
    added = next(line for line in stderr.splitlines() if line.lstrip().startswith("uv add "))
    runtime: dict[str, int] = {}
    public: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "_runtime" in (relative := path.relative_to(root)).parts:
            runtime[relative.relative_to(f"{PACKAGE}/_runtime").as_posix()] = len(path.read_bytes().splitlines())
        elif all(not part.startswith("_") or part == "__init__.py" for part in relative.parts):
            public.append(relative.as_posix())
    return "\n".join([
        f"# {profile}",
        f"backend {backend}",
        f"dependencies {' '.join(shlex.split(added)[2:])}",
        "copied runtime",
        *(f"  {path}: {lines}" for path, lines in sorted(runtime.items())),
        f"total {len(runtime)} modules, {sum(runtime.values())} lines",
        "public files",
        *(f"  {path}" for path in sorted(public)),
        "",
    ])
