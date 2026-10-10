"""Report generated client publication profiles from the files their runs write."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from tests.api_generation.support.client_generation import SOURCE, publication_client

if TYPE_CHECKING:
    from pathlib import Path

PROFILES = json.loads((SOURCE / "publication" / "profiles.json").read_text(encoding="utf-8"))


def client_capability_report(profile: str, root: Path) -> str:
    """List the public files and copied-runtime modules one profile writes, with line counts only for `minimal`."""
    case = PROFILES[profile]
    backend = case.get("backend", "pydantic_v2.BaseModel")
    runtime: dict[str, int] = {}
    public: list[str] = []
    for path in publication_client(case, root, backend):
        if path.suffix != ".py":
            continue
        if "_runtime" in path.parts:
            runtime[path.relative_to("publication_client/_runtime").as_posix()] = len(
                (root / path).read_bytes().splitlines()
            )
        elif all(not part.startswith("_") or part == "__init__.py" for part in path.parts):
            public.append(path.as_posix())
    counted = profile == "minimal"
    return "\n".join([
        f"# {profile}",
        f"backend {backend}",
        "copied runtime",
        *(f"  {path}: {lines}" if counted else f"  {path}" for path, lines in sorted(runtime.items())),
        f"total {len(runtime)} modules, {sum(runtime.values())} lines" if counted else f"total {len(runtime)} modules",
        "public files",
        *(f"  {path}" for path in sorted(public)),
        "",
    ])
