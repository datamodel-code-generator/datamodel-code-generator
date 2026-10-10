"""Exchange independent HTTP inputs with generated servers and clients at URL and charset boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from tests.api_generation.support.fastapi_server import _generate
from tests.api_generation.support.generated_packages import forget_generated, import_generated

if TYPE_CHECKING:
    import pytest

SOURCE = Path(__file__).parents[2] / "data" / "generation_platform/fastapi"


def text_charsets(root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Report handler values, actual headers, and raw bytes independently of the generated client."""
    cases = json.loads((SOURCE / "http-boundaries.json").read_text(encoding="utf-8"))
    lines: list[str] = []
    monkeypatch.syspath_prepend(str(root))
    for backend in ("pydantic_v2.BaseModel", "pydantic_v2.dataclass"):
        package = f"charsets_{backend.rpartition('.')[2].lower()}"
        _generate({"input": "http-charsets.json", "config": {"server_layout": "single"}}, backend, root, package)
        try:
            server = import_generated(package)

            class Service:
                def receive(self, *, body: object, media_type: str) -> None:
                    value = getattr(body, "value", body)
                    lines.append(f"  receive {media_type} {getattr(value, 'root', value)!r}")

                def send(self, *, choice: str) -> object:
                    return choice

                def label(self, *, label: object) -> None:
                    lines.append(f"  label {getattr(label, 'root', label)!r}")

                def primary(self) -> object:
                    return "café"

            lines.append(f"# {backend}")
            with TestClient(server.create_app(service=Service())) as api:
                for case in cases["inputs"]:
                    lines.append(f"> {case['media']} {case['hex']}")
                    response = api.post("/input", headers={"Content-Type": case["media"]}, content=bytes.fromhex(case["hex"]))
                    lines.append(f"< {response.status_code}")
                for label in cases["labels"]:
                    lines.append(f"> label {label}")
                    response = api.get(f"/labels/{label}")
                    lines.append(f"< {response.status_code}")
                response = api.get("/primary")
                lines.append(f"< primary {response.status_code} {response.headers['content-type']} {response.content!r}")
        finally:
            forget_generated(package)
    return "\n".join(lines) + "\n"
