"""Call generated clients through HTTPX2: parameters, bodies, responses, headers, options, and failures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("httpx2")

from tests.api_generation.scenarios.client_allowreserved import reserved_version_report
from tests.api_generation.support.client_generation import client_readme_report
from tests.api_generation.support.client_runtime import client_copied_runtime_report
from tests.api_generation.support.client_scenarios import BACKENDS, SCENARIOS, client_runtime_report
from tests.conftest import assert_generated_modules_output, assert_output

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/client/runtime"
README_EXAMPLES = Path(__file__).parents[1] / "data/expected/main/generation_platform/client/readme-examples"


def test_client_copied_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make a real TLS call and locate the imported runtime inside the generated package."""
    monkeypatch.setenv("DATAMODEL_CODE_GENERATOR_CLIENT_COPIED_RUNTIME_E2E", "1")
    assert_output(client_copied_runtime_report(tmp_path), EXPECTED / "copied-runtime.txt")


@pytest.mark.parametrize(
    "case",
    [*SCENARIOS, "allowreserved-path-30-shorthand", "allowreserved-path-31-shorthand", "allowreserved-path-32-unknown"],
)
def test_client_runtime(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate each client and exercise real TLS exchanges and injected failures in both execution modes."""
    isolated = {"http_proxy", "https_proxy", "all_proxy", "no_proxy", "ssl_cert_file", "ssl_cert_dir", "request_method"}
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "ambient-ca-does-not-exist.pem"))
    for key in tuple(os.environ):
        if key.casefold() in isolated:
            monkeypatch.delenv(key)
    monkeypatch.setenv("NO_PROXY", "*")
    if case.endswith(("-shorthand", "-unknown")):
        case, _, variant = case.rpartition("-")
        version = "3.10.0" if variant == "unknown" else f"3.{case[-1]}"
        modules, report = reserved_version_report(case, version, BACKENDS, tmp_path)
        assert_generated_modules_output(modules, EXPECTED.parent / "packages" / case / "pydantic_v2_BaseModel")
        assert_output(report, EXPECTED / f"{case}.txt")
        return
    assert_output(client_runtime_report(case, tmp_path), EXPECTED / f"{case}.txt")


@pytest.mark.parametrize("case", ["pets", "auth", "streams", "sockets", "ndjson-split"])
def test_client_readme_examples(case: str, tmp_path: Path) -> None:
    """Run the quick start, asynchronous, and error examples of a client README as far as they run without an API.

    The quick start calls the first operation that is a plain request and response; a client without one opens the
    first operation that has a WebSocket or stream helper through that helper, as WebSockets and streams are read.
    """
    assert_output(client_readme_report(case, tmp_path), README_EXAMPLES / f"{case}.txt")
