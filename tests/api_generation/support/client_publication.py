"""Report generated client publication profiles from their complete rendered artifacts."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from tests.api_generation.support.client_generation import SOURCE, publication_client

if TYPE_CHECKING:
    from pathlib import Path

PROFILES = json.loads((SOURCE / "publication" / "profiles.json").read_text(encoding="utf-8"))


def client_capability_report(profile: str, root: Path) -> str:
    """List dependency specifiers, public files and physical copied-runtime lines for one profile."""
    case = PROFILES[profile]
    backend = case.get("backend", "pydantic_v2.BaseModel")
    project = publication_client(case, root, backend)
    runtime: dict[str, int] = {}
    public: list[str] = []
    for artifact in project.artifacts:
        path = artifact.path.relative_to(root)
        if path.suffix != ".py":
            continue
        if "_runtime" in path.parts:
            runtime[path.relative_to("publication_client/_runtime").as_posix()] = len(
                (artifact.content or b"").splitlines()
            )
        elif all(not part.startswith("_") or part == "__init__.py" for part in path.parts):
            public.append(path.as_posix())
    return "\n".join([
        f"# {profile}",
        f"backend {backend}",
        "dependencies",
        *(f"  {dependency}" for dependency in project.dependencies),
        "copied runtime",
        *(f"  {path}: {lines}" for path, lines in sorted(runtime.items())),
        f"total {len(runtime)} modules, {sum(runtime.values())} lines",
        "public files",
        *(f"  {path}" for path in sorted(public)),
        "",
    ])
