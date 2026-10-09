"""Install embedded client wheels and sdists using only the dependency command generation prints."""

from __future__ import annotations

import gzip
import json
import os
import shlex
import shutil
import ssl
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import assert_output
from tests.main.conftest import run_main_and_assert

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform" / "client"
PUBLICATION = SOURCE / "publication"
EXPECTED = DATA / "expected/main/generation_platform/client/install"
BACKENDS = (
    "pydantic_v2.BaseModel",
    "pydantic_v2.dataclass",
    "dataclasses.dataclass",
    "typing.TypedDict",
    "msgspec.Struct",
)
CASES = [("minimal", backend) for backend in BACKENDS] + [
    (profile, backend)
    for profile in ("api-key", "oauth2-client-credentials", "all")
    for backend in ("pydantic_v2.BaseModel", "dataclasses.dataclass", "msgspec.Struct")
]


@pytest.mark.parametrize(("profile", "backend"), CASES)
def test_client_install(
    profile: str,
    backend: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build once, install both distributions at their direct dependency floors, and make sync/async TLS calls."""
    if not os.environ.get("DATAMODEL_CODE_GENERATOR_CLIENT_INSTALL_E2E"):
        pytest.skip("DATAMODEL_CODE_GENERATOR_CLIENT_INSTALL_E2E enables installing generated clients")
    import httpx2

    from tests.data.python.fixture_server import FixtureServer, _contexts, stop_servers

    case = json.loads((PUBLICATION / "profiles.json").read_text(encoding="utf-8"))[profile]
    shutil.copy2(PUBLICATION / "pyproject-host.toml", tmp_path / "pyproject.toml")
    for name in (case["input"], *case.get("references", ())):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE / name, tmp_path / name)
    source = tmp_path / case["input"]
    arguments = [
        "--generate-client",
        "httpx2",
        "--client-output",
        "publication_client",
        "--client-package",
        "publication_client",
        "--client-model-package",
        "publication_client_models",
        "--target-python-version",
        "3.11",
        "--openapi-scopes",
        "schemas",
        "api",
        "--output-model-type",
        backend,
        "--formatters",
        "builtin",
        "--disable-timestamp",
    ]
    if protocols := case.get("config", {}).get("protocols"):
        arguments.extend(("--client-protocols", str(tmp_path / protocols)))
    if operations := case.get("config", {}).get("operations"):
        arguments.extend((
            "--client-operations",
            json.dumps({
                item["ref"]: {key: value for key, value in item.items() if key != "ref"} for item in operations
            }),
        ))
    monkeypatch.chdir(tmp_path)
    dependency_kind = (
        "pydantic" if backend.startswith("pydantic") else "msgspec" if backend.startswith("msgspec") else "stdlib"
    )
    notice = (EXPECTED / f"{profile}-{dependency_kind}-dependencies.txt").read_text(encoding="utf-8")
    run_main_and_assert(
        input_path=source,
        output_path=Path("publication_client_models.py"),
        input_file_type="openapi",
        extra_args=arguments,
        capsys=capsys,
        expected_stderr=notice,
    )
    command = shlex.split(notice.splitlines()[-1])
    subprocess.run(
        [*command, "--no-sync", "--resolution", "lowest-direct", "--python", sys.executable],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["uv", "build", "--quiet", "--sdist", "--wheel"], cwd=tmp_path, check=True, capture_output=True, text=True
    )
    authority = tmp_path / "authority.pem"
    authority.write_text(
        "".join(ssl.DER_cert_to_PEM_cert(cert) for cert in _contexts()[1].get_ca_certs(binary_form=True)),
        encoding="ascii",
    )
    requests: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        request.read()
        requests.append(
            f"{request.method} {request.url.path} key={request.headers.get('X-API-Key')} "
            f"auth={request.headers.get('Authorization')}"
        )
        if request.url.path == "/token":
            requests.append(f"token body {request.content.decode()}")
            return httpx2.Response(
                200, json={"access_token": "install-token", "token_type": "Bearer", "expires_in": 3600}
            )
        body = json.loads(
            gzip.decompress(request.content) if request.headers.get("content-encoding") == "gzip" else request.content
        )
        requests.append(f"request body {json.dumps(body, sort_keys=True)}")
        return httpx2.Response(201 if profile == "all" else 200, json={"id": 7, "name": body["name"]})

    server = FixtureServer(respond)
    lines: list[str] = []
    try:
        for suffix in (".whl", ".tar.gz"):
            distribution = next((tmp_path / "dist").glob(f"*{suffix}"))
            environment = tmp_path / ("wheel-environment" if suffix == ".whl" else "sdist-environment")
            subprocess.run(["uv", "venv", "--quiet", "--python", sys.executable, environment], check=True)
            python = environment / ("Scripts" if os.name == "nt" else "bin") / "python"
            subprocess.run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--quiet",
                    "--python",
                    python,
                    "--resolution",
                    "lowest-direct",
                    distribution,
                    *command[2:],
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            called = subprocess.run(
                [
                    python,
                    "-I",
                    PUBLICATION / "app.py",
                    profile,
                    backend,
                    f"https://localhost:{server.server_port}",
                    authority,
                ],
                cwd=environment,
                capture_output=True,
                text=True,
                check=True,
            )
            lines.extend((f"distribution {suffix}", called.stdout + called.stderr))
        lines.extend(requests)
        lines.append(f"server failures {server.failures}")
    finally:
        stop_servers()
    assert_output("\n".join(lines) + "\n", EXPECTED / f"{profile}-{dependency_kind}-served.txt")
