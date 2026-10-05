"""Render body field arguments for the retained acyclic shared-ancestor allOf schema."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from tests.data.python.client_generation import SOURCE, render_client

if TYPE_CHECKING:
    from pathlib import Path


def shared_fields_report(root: Path) -> str:
    """Report the generated models and sync/async field methods of a shared allOf request body."""
    root.mkdir(parents=True, exist_ok=True)
    source = shutil.copy2(SOURCE / "fields-shared-refs.yaml", root / "fields-shared-refs.yaml")
    diagnostics, modules = render_client(
        source,
        root,
        "pydantic_v2.BaseModel",
        {},
        {"body_arguments": "both", "server_base_url": "https://api.example.com"},
    )
    lines = ["# fields-shared-refs", f"diagnostics {diagnostics}", f"generated modules {len(modules)}"]
    for parts, content in sorted(modules.items()):
        if parts == ("models.py",) or ("resources" in parts and parts[-1] in {"_sync.py", "_async.py"}):
            lines.extend((f"## {'/'.join(parts)}", content.rstrip()))
    return "\n".join(lines) + "\n"
