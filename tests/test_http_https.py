"""Deterministic HTTPS end-to-end tests for every supported HTTP backend."""

from __future__ import annotations

import os
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import trustme

from datamodel_code_generator import HTTPBackend, SchemaFetchError
from datamodel_code_generator.http import _get_http_response, _get_http_stack, get_body
from tests.conftest import create_assert_file_content
from tests.main.conftest import run_main_url_and_assert

if TYPE_CHECKING:
    from collections.abc import Iterator

    from _pytest.monkeypatch import MonkeyPatch

HTTP_DATA_PATH = Path(__file__).parent / "data"
PET_SCHEMA_PATH = HTTP_DATA_PATH / "jsonschema" / "pet_simple.json"
assert_https_generated_file = create_assert_file_content(HTTP_DATA_PATH / "expected" / "http")
needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None, reason="OpenSSL is needed to hash a CA directory")


def _selected_http_backend() -> HTTPBackend:
    """Return the explicit backend requested by an HTTP backend E2E environment."""
    return HTTPBackend(os.environ.get("DATAMODEL_CODE_GENERATOR_TEST_HTTP_BACKEND", "auto"))


def _selected_http_backend_cli_args() -> list[str]:
    """Return CLI arguments for an explicitly selected E2E backend."""
    if (backend := _selected_http_backend()) is HTTPBackend.AUTO:
        return []
    return ["--http-backend", backend.value]


def _get_pinned_https_body(url: str, *, verify: bool) -> str:
    """Fetch through the real DNS-pinned transport used for public HTTPS URLs."""
    return _get_http_response(
        _get_http_stack(_selected_http_backend()),
        url,
        headers=None,
        verify=verify,
        follow_redirects=False,
        query_parameters=None,
        timeout=5.0,
        pinned_host="127.0.0.1",
        pinned_ips=(IPv4Address("127.0.0.1"),),
    ).text


class _HttpsSchemaHandler(BaseHTTPRequestHandler):
    """Serve a schema over a real TLS socket without external network access."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        body = PET_SCHEMA_PATH.read_bytes()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def local_https_server(tmp_path: Path, monkeypatch: MonkeyPatch) -> Iterator[tuple[str, Path]]:
    """Run a local TLS server and expose its private CA certificate."""
    for environment_variable in (
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "all_proxy",
        "https_proxy",
        "http_proxy",
    ):
        monkeypatch.delenv(environment_variable, raising=False)

    ca = trustme.CA()
    certificate = ca.issue_cert("127.0.0.1")
    ca_path = tmp_path / "ca.pem"
    certificate_path = tmp_path / "server.pem"
    private_key_path = tmp_path / "server.key"
    ca.cert_pem.write_to_path(ca_path)
    certificate.cert_chain_pems[0].write_to_path(certificate_path)
    certificate.private_key_pem.write_to_path(private_key_path)

    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
    tls_context.load_cert_chain(certificate_path, private_key_path)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _HttpsSchemaHandler)
    server.daemon_threads = True
    server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_port}", ca_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_https_rejects_untrusted_certificate(
    local_https_server: tuple[str, Path],
) -> None:
    """Reject a real HTTPS response whose private CA is not trusted."""
    server_url, _ = local_https_server

    with pytest.raises(SchemaFetchError, match=r"Failed to fetch .*pet.json"):
        get_body(
            f"{server_url}/pet.json",
            allow_private_network=True,
            http_backend=_selected_http_backend(),
        )


def test_pinned_https_rejects_untrusted_certificate(local_https_server: tuple[str, Path]) -> None:
    """Keep certificate verification enabled on the DNS-pinned path."""
    server_url, _ = local_https_server
    with pytest.raises(Exception, match=r"(?i)certificate|cert_verify|trust"):
        _get_pinned_https_body(f"{server_url}/pet.json", verify=True)


def test_https_ignore_tls_fetches_real_schema(
    local_https_server: tuple[str, Path],
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fetch through a real TLS handshake when certificate checks are explicitly disabled."""
    server_url, _ = local_https_server
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing-ca.pem"))
    output_path = tmp_path / "pet.txt"

    output_path.write_text(
        _get_pinned_https_body(f"{server_url}/pet.json", verify=False),
        encoding="utf-8",
        newline="",
    )

    assert_https_generated_file(output_path, "pet_schema.txt")


@pytest.mark.cli_doc(
    options=["--http-backend"],
    option_description="""Select the HTTP client backend for remote schemas.

`--http-backend auto` selects stable HTTPX when its client module is installed
and selects experimental HTTPX2 only when that module is absent. Explicit
`httpx` or `httpx2` selections require that exact backend. Explicit selections
and paired dependency errors do not fall back.""",
    input_schema="jsonschema/pet_simple.json",
    cli_args=["--http-backend", "auto"],
    golden_output="http/backend.py",
)
def test_cli_https_ignore_tls_generates_model(
    local_https_server: tuple[str, Path],
    tmp_path: Path,
) -> None:
    """Exercise the public CLI HTTPS opt-out through model generation."""
    server_url, _ = local_https_server
    schema_url = f"{server_url}/pet.json"

    run_main_url_and_assert(
        url=schema_url,
        output_path=tmp_path / "output.py",
        input_file_type="jsonschema",
        assert_func=assert_https_generated_file,
        expected_file="backend.py",
        extra_args=[
            "--allow-private-network",
            "--http-ignore-tls",
            "--disable-timestamp",
            *_selected_http_backend_cli_args(),
        ],
        transform=lambda output: output.replace(schema_url, "http://localhost/schema.json"),
    )


@pytest.mark.allow_direct_assert
def test_cli_https_updates_and_verifies_remote_lock(
    local_https_server: tuple[str, Path],
    tmp_path: Path,
) -> None:
    """Lock a real TLS response through the same selected backend as normal generation."""
    server_url, _ = local_https_server
    schema_url = f"{server_url}/pet.json"
    lockfile = tmp_path / "remote.lock"
    update_args = [
        "--allow-private-network",
        "--http-ignore-tls",
        "--disable-timestamp",
        "--update-lock",
        "--lockfile",
        str(lockfile),
        *_selected_http_backend_cli_args(),
    ]
    verify_args = [
        "--allow-private-network",
        "--http-ignore-tls",
        "--disable-timestamp",
        "--locked",
        "--lockfile",
        str(lockfile),
        *_selected_http_backend_cli_args(),
    ]

    run_main_url_and_assert(
        url=schema_url,
        output_path=tmp_path / "output.py",
        input_file_type="jsonschema",
        assert_func=assert_https_generated_file,
        expected_file="backend.py",
        extra_args=update_args,
        transform=lambda output: output.replace(schema_url, "http://localhost/schema.json"),
    )
    lock_content = lockfile.read_text(encoding="utf-8")
    assert f'"url": "{server_url}"' in lock_content
    assert "/pet.json" not in lock_content

    run_main_url_and_assert(
        url=schema_url,
        output_path=tmp_path / "verified.py",
        input_file_type="jsonschema",
        assert_func=assert_https_generated_file,
        expected_file="backend.py",
        extra_args=verify_args,
        transform=lambda output: output.replace(schema_url, "http://localhost/schema.json"),
    )
    assert lockfile.read_text(encoding="utf-8") == lock_content


@pytest.mark.parametrize("ca_source", ["file", pytest.param("directory", marks=needs_openssl)])
def test_https_trusted_ca_verifies_and_generates_model(
    local_https_server: tuple[str, Path],
    ca_source: str,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """Trust the generated CA and verify a real TLS connection without disabling checks."""
    server_url, ca_path = local_https_server
    schema_url = f"{server_url}/pet.json"
    match ca_source:
        case "file":
            monkeypatch.setenv("SSL_CERT_FILE", str(ca_path))
        case _:
            monkeypatch.setenv("SSL_CERT_DIR", str(request.getfixturevalue("ca_directory")))
    pinned_output_path = tmp_path / "pinned-pet.txt"
    pinned_output_path.write_text(
        _get_pinned_https_body(schema_url, verify=True),
        encoding="utf-8",
        newline="",
    )
    assert_https_generated_file(pinned_output_path, "pet_schema.txt")

    run_main_url_and_assert(
        url=schema_url,
        output_path=tmp_path / "output.py",
        input_file_type="jsonschema",
        assert_func=assert_https_generated_file,
        expected_file="backend.py",
        extra_args=["--allow-private-network", "--disable-timestamp", *_selected_http_backend_cli_args()],
        transform=lambda output: output.replace(schema_url, "http://localhost/schema.json"),
    )


@pytest.fixture
def ca_directory(local_https_server: tuple[str, Path], tmp_path: Path) -> Path:
    """Install the local server's CA in an OpenSSL hashed certificate directory."""
    _, ca_path = local_https_server
    result = subprocess.run(
        ["openssl", "x509", "-in", str(ca_path), "-subject_hash", "-noout"],
        capture_output=True,
        text=True,
        check=True,
    )
    directory = tmp_path / "ca-directory"
    directory.mkdir()
    shutil.copyfile(ca_path, directory / f"{result.stdout.strip()}.0")
    return directory


@needs_openssl
def test_https_explicit_ca_file_excludes_ca_directory(
    local_https_server: tuple[str, Path],
    ca_directory: Path,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An explicit CA file must replace other roots, just as it does in HTTPX."""
    server_url, _ = local_https_server
    unrelated_ca = tmp_path / "unrelated-ca.pem"
    trustme.CA().cert_pem.write_to_path(unrelated_ca)
    monkeypatch.setenv("SSL_CERT_FILE", str(unrelated_ca))
    monkeypatch.setenv("SSL_CERT_DIR", str(ca_directory))

    with pytest.raises(Exception, match="CERTIFICATE_VERIFY_FAILED"):
        _get_pinned_https_body(f"{server_url}/pet.json", verify=True)
