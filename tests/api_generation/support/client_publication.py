"""Report generated client publication profiles from the files their command-line runs write and print."""

from __future__ import annotations

import json
import re
import shlex
import shutil
from typing import TYPE_CHECKING

from tests.api_generation.support.backends import backend_arguments
from tests.api_generation.support.client_generation import SOURCE, client_options, copy_references
from tests.api_generation.support.model_codec_reports import third_party_imports

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
    backend = case.get("backend", "pydantic_v2.BaseModel")
    arguments = [
        *("--input", str(source), "--input-file-type", "openapi", "--output", str(root / f"{PACKAGE}_models.py")),
        *("--target-python-version", "3.11", "--openapi-scopes", "schemas", "api"),
        *("--output-model-type", backend),
        *("--formatters", "builtin", "--disable-timestamp", "--generate-client", "httpx2"),
        *("--client-output", str(root / PACKAGE), "--client-package", PACKAGE),
        *("--client-model-package", f"{PACKAGE}_models"),
    ]
    arguments.extend(backend_arguments(backend))
    if paths := case.get("model", {}).get("openapi_include_paths"):
        arguments.extend(("--openapi-include-paths", *paths))
    if "protocols" in config:
        arguments.extend(("--client-protocols", str(root / config["protocols"])))
    if "operations" in config:
        operations = client_options({"operations": config["operations"]}, root, PACKAGE)["client_operations"]
        arguments.extend(("--client-operations", json.dumps(operations)))
    return arguments


def client_capability_report(profile: str, root: Path, stderr: str) -> str:
    """List the dependencies a profile's run asks to add, its public files and its copied-runtime modules.

    The modules the written files import from outside the standard library follow: those of the dependencies, then
    the others, which only the dependencies' own requirements may provide.
    Line counts are listed only for `minimal`. The run writes the only Python files under the root.
    """
    backend = PROFILES[profile].get("backend", "pydantic_v2.BaseModel")
    added = next(line for line in stderr.splitlines() if line.lstrip().startswith("uv add "))
    dependencies = shlex.split(added)[2:]
    provided = {re.split(r"[^\w-]", dependency, maxsplit=1)[0].replace("-", "_") for dependency in dependencies}
    imported = third_party_imports(root)
    runtime: dict[str, int] = {}
    public: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "_runtime" in (relative := path.relative_to(root)).parts:
            runtime[relative.relative_to(f"{PACKAGE}/_runtime").as_posix()] = len(path.read_bytes().splitlines())
        elif all(not part.startswith("_") or part == "__init__.py" for part in relative.parts):
            public.append(relative.as_posix())
    counted = profile == "minimal"
    return "\n".join([
        f"# {profile}",
        f"backend {backend}",
        f"dependencies {' '.join(dependencies)}",
        f"imports of the dependencies {' '.join(name for name in imported if name in provided)}",
        f"other third-party imports {' '.join(name for name in imported if name not in provided)}",
        "copied runtime",
        *(f"  {path}: {lines}" if counted else f"  {path}" for path, lines in sorted(runtime.items())),
        f"total {len(runtime)} modules, {sum(runtime.values())} lines" if counted else f"total {len(runtime)} modules",
        "public files",
        *(f"  {path}" for path in sorted(public)),
        "",
    ])
