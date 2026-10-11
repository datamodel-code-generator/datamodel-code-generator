"""Tests for HTTP utilities."""

from __future__ import annotations

import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address, IPv6Address, ip_address
from operator import itemgetter
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest

from datamodel_code_generator import (
    Error,
    GenerateConfig,
    HTTPBackend,
    InputFileType,
    ModuleSplitMode,
    SchemaFetchError,
    chdir,
    generate,
)
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.http import (
    MAX_HTTP_REDIRECTS,
    _create_ssl_context,
    _embedded_ipv4,
    _get_addr_info_ip,
    _get_http_response,
    _get_http_stack,
    _get_httpx,
    _get_redirect_headers,
    _get_url_origin,
    _HTTPFetchSession,
    _is_safe_ip,
    _load_http_stack,
    _normalize_dns_host,
    _PinnedNetworkBackend,
    get_body,
)
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from tests.conftest import assert_output, create_assert_file_content
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    _assert_file_does_not_exist,
    run_main_url_and_assert,
    run_main_with_args,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pytest_mock import MockerFixture

HTTP_E2E_DATA_PATH = Path(__file__).parent / "data"
assert_http_e2e_file = create_assert_file_content(HTTP_E2E_DATA_PATH / "expected" / "http")


@pytest.fixture(autouse=True)
def block_dns_by_default(mocker: MockerFixture) -> None:
    """Keep tests that mock HTTP requests independent from external DNS."""
    mocker.patch("socket.getaddrinfo", side_effect=OSError)


class _SchemaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    client_connections: ClassVar[set[tuple[str, int]]] = set()
    connections_closed: ClassVar[threading.Event] = threading.Event()
    cookie_redirect_target: ClassVar[str | None] = None
    received_cookies: ClassVar[list[str | None]] = []
    routes: ClassVar[dict[str, tuple[int, dict[str, str], bytes]]] = {
        "/schema.json": (200, {"content-type": "application/json"}, b'{"type":"object"}'),
    }

    def setup(self) -> None:
        """Record each accepted TCP connection."""
        super().setup()
        self.client_connections.add(self.client_address)

    def do_GET(self) -> None:
        if self.path.startswith("/echo"):
            body = json.dumps({
                "path": self.path,
                "test_header": self.headers.get("X-Test-Header"),
            }).encode()
            status, headers = 200, {"content-type": "application/json"}
        elif self.path == "/cookie-redirect" and self.cookie_redirect_target is not None:
            status, headers, body = (
                302,
                {
                    "location": self.cookie_redirect_target,
                    "set-cookie": "session=secret; Path=/",
                },
                b"",
            )
        elif self.path == "/cookie-target":
            cookie = self.headers.get("Cookie")
            self.received_cookies.append(cookie)
            body = json.dumps({"cookie": cookie}).encode()
            status, headers = 200, {"content-type": "application/json"}
        else:
            status, headers, body = self.routes.get(
                self.path.partition("?")[0],
                (404, {"content-type": "application/json"}, b'{"error":"not found"}'),
            )

        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def finish(self) -> None:
        try:
            super().finish()
        finally:
            self.connections_closed.set()

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def local_http_server() -> Iterator[str]:
    """Run a local HTTP server for transport-level tests."""
    _SchemaHandler.client_connections.clear()
    _SchemaHandler.connections_closed.clear()
    _SchemaHandler.routes["/pet.json"] = (
        200,
        {"content-type": "application/json"},
        (HTTP_E2E_DATA_PATH / "jsonschema" / "pet_simple.json").read_bytes(),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SchemaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://localhost:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        del _SchemaHandler.routes["/pet.json"]


@pytest.fixture
def cross_port_cookie_redirect_server() -> Iterator[str]:
    """Run a real redirect across two local ports."""
    _SchemaHandler.client_connections.clear()
    _SchemaHandler.connections_closed.clear()
    _SchemaHandler.received_cookies.clear()
    target_server = ThreadingHTTPServer(("127.0.0.1", 0), _SchemaHandler)
    source_server = ThreadingHTTPServer(("127.0.0.1", 0), _SchemaHandler)
    _SchemaHandler.cookie_redirect_target = f"http://localhost:{target_server.server_port}/cookie-target"
    servers = (target_server, source_server)
    threads = tuple(threading.Thread(target=server.serve_forever, daemon=True) for server in servers)
    for thread in threads:
        thread.start()
    try:
        yield f"http://localhost:{source_server.server_port}/cookie-redirect"
    finally:
        _SchemaHandler.cookie_redirect_target = None
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2.0)


def test_get_body_raises_on_http_error(mocker: MockerFixture) -> None:
    """Test that get_body raises on HTTP error status codes."""
    mock_response = Mock()
    mock_response.status_code = 404
    mock_response.headers = {"content-type": "text/html"}
    mocker.patch.object(_get_httpx(), "get", return_value=mock_response)

    with pytest.raises(SchemaFetchError, match="HTTP 404 error fetching"):
        get_body("https://example.com/missing.json", allow_private_network=True)


def test_get_body_raises_on_html_response(mocker: MockerFixture) -> None:
    """Test that get_body raises when response is HTML instead of JSON/YAML."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html; charset=utf-8"}
    mocker.patch.object(_get_httpx(), "get", return_value=mock_response)

    with pytest.raises(SchemaFetchError, match="Unexpected HTML response"):
        get_body("https://example.com/schema.json", allow_private_network=True)


def test_get_body_succeeds_with_json_response(mocker: MockerFixture) -> None:
    """Test that get_body returns text for valid JSON responses."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/json"}
    mock_response.content = b'{"type": "object"}'
    mocker.patch.object(_get_httpx(), "get", return_value=mock_response)

    result = get_body("https://example.com/schema.json", allow_private_network=True)
    assert result == '{"type": "object"}'


def test_get_body_succeeds_without_content_type(mocker: MockerFixture) -> None:
    """Test that get_body returns text when no Content-Type header is present."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.content = b'{"type": "object"}'
    mocker.patch.object(_get_httpx(), "get", return_value=mock_response)

    result = get_body("https://example.com/schema.json", allow_private_network=True)
    assert result == '{"type": "object"}'


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/schema.json",
        "http://127.1/schema.json",
        "http://2130706433/schema.json",
        "http://0x7f000001/schema.json",
        "http://0177.0.0.1/schema.json",
        "http://[::1]/schema.json",
        "http://[::7f00:1]/schema.json",
        "http://[::ffff:0:7f00:1]/schema.json",
        "http://[64:ff9b::7f00:1]/schema.json",
        "http://[64:ff9b::a9fe:a9fe]/schema.json",
        "http://169.254.169.254/latest/meta-data",
        "http://localhost/schema.json",
        "http://schema.localhost/schema.json",
    ],
)
def test_get_body_blocks_unsafe_url_hosts(mocker: MockerFixture, url: str) -> None:
    """Block local and private network targets before fetching."""
    mock_get = mocker.patch.object(_get_httpx(), "get")

    with pytest.raises(SchemaFetchError, match="--allow-private-network"):
        get_body(url)
    assert mock_get.call_count == 0


@pytest.mark.parametrize(
    ("addr", "expected"),
    [
        ("8.8.8.8", None),
        ("2606:4700::1", None),
        ("::ffff:127.0.0.1", "127.0.0.1"),
        ("::7f00:1", "127.0.0.1"),
        ("::ffff:0:7f00:1", "127.0.0.1"),
        ("64:ff9b::a9fe:a9fe", "169.254.169.254"),
    ],
)
def test_embedded_ipv4(addr: str, expected: str | None) -> None:
    """Return embedded IPv4 addresses from IPv6 wrapper forms."""
    result = _embedded_ipv4(ip_address(addr))
    assert (str(result) if result is not None else None) == expected


@pytest.mark.parametrize(
    ("addr", "safe"),
    [
        ("8.8.8.8", True),
        ("10.0.0.1", False),
        ("::ffff:8.8.8.8", True),
        ("64:ff9b::a9fe:a9fe", False),
        ("64:ff9b::808:808", True),
        ("::1", False),
    ],
)
def test_is_safe_ip(addr: str, safe: bool) -> None:
    """Validate embedded IPv4 addresses before trusting an IPv6 address."""
    assert _is_safe_ip(ip_address(addr)) is safe


@pytest.mark.parametrize(
    "url",
    [
        "http://127.1/schema.json",
        "http://2130706433/schema.json",
        "http://0x7f000001/schema.json",
        "http://0177.0.0.1/schema.json",
    ],
)
def test_get_body_blocks_unsafe_ipv4_literals_without_dns(mocker: MockerFixture, url: str) -> None:
    """Block legacy IPv4 literals without depending on platform DNS behavior."""
    mocker.patch("socket.getaddrinfo", side_effect=OSError)
    mock_get = mocker.patch.object(_get_httpx(), "get")

    with pytest.raises(SchemaFetchError, match="--allow-private-network"):
        get_body(url)
    assert mock_get.call_count == 0


@pytest.mark.parametrize(
    "url",
    [
        "http://1.2.3.4.5/schema.json",
        "http://256.0.0.1/schema.json",
    ],
)
def test_get_body_blocks_unresolvable_ipv4_like_hosts(mocker: MockerFixture, url: str) -> None:
    """Fail closed when an IPv4-like host is neither a valid IP literal nor resolvable."""
    mocker.patch("socket.getaddrinfo", side_effect=OSError)
    mock_get = mocker.patch.object(_get_httpx(), "get")

    with pytest.raises(SchemaFetchError, match="could not be resolved to a public IP address"):
        get_body(url)
    assert mock_get.call_count == 0


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.1/schema.json",
        "http://1.2.3.4.5/schema.json",
        "http://256.0.0.1/schema.json",
    ],
)
def test_get_body_handles_legacy_ipv4_literal_boundaries(mocker: MockerFixture, url: str) -> None:
    """Cover legacy IPv4 parser boundary cases without platform DNS behavior."""
    mocker.patch("socket.getaddrinfo", side_effect=OSError)
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/json"}
    mock_response.content = b'{"type": "object"}'
    mock_get = mocker.patch.object(_get_httpx(), "get", return_value=mock_response)

    if url == "http://127.0.1/schema.json":
        with pytest.raises(SchemaFetchError, match="--allow-private-network"):
            get_body(url)
        assert mock_get.call_count == 0
    else:
        result = get_body(url, allow_private_network=True)
        assert result == '{"type": "object"}'
        assert mock_get.call_count == 1


def test_get_body_allows_unsafe_url_host_with_explicit_opt_in(mocker: MockerFixture) -> None:
    """Allow trusted private network targets only when explicitly requested."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/json"}
    mock_response.content = b'{"type": "object"}'
    mock_get = mocker.patch.object(_get_httpx(), "get", return_value=mock_response)

    result = get_body("http://127.0.0.1/schema.json", allow_private_network=True)

    assert result == '{"type": "object"}'
    assert mock_get.call_count == 1


def test_get_body_blocks_hostname_resolving_to_unsafe_ip(mocker: MockerFixture) -> None:
    """Block hostnames that resolve to local or private network targets."""
    mocker.patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))],
    )
    mock_get = mocker.patch.object(_get_httpx(), "get")

    with pytest.raises(SchemaFetchError, match="--allow-private-network"):
        get_body("https://metadata.example.com/schema.json")
    assert mock_get.call_count == 0


def test_get_body_blocks_unresolved_hostname(mocker: MockerFixture) -> None:
    """Block unresolved hostnames when the public address cannot be validated."""
    mock_fetch = mocker.patch("datamodel_code_generator.http._get_http_response")

    with pytest.raises(SchemaFetchError, match="could not be resolved to a public IP address"):
        get_body("https://missing.example.com/schema.json")
    assert mock_fetch.call_count == 0


def test_get_body_blocks_invalid_idn_hostname(mocker: MockerFixture) -> None:
    """Block hostnames that cannot be normalized to an ASCII DNS name."""
    mock_fetch = mocker.patch("datamodel_code_generator.http._get_http_response")

    with pytest.raises(SchemaFetchError, match="Invalid URL host"):
        get_body(f"https://{chr(0xD800)}.example/schema.json")
    assert mock_fetch.call_count == 0


def test_get_body_reports_resolved_ips_in_dns_order(mocker: MockerFixture) -> None:
    """Preserve DNS result order when reporting unsafe resolved addresses."""
    mocker.patch(
        "socket.getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
        ],
    )
    mock_get = mocker.patch.object(_get_httpx(), "get")

    with pytest.raises(SchemaFetchError, match=r"Resolved IPs: 93\.184\.216\.34, 127\.0\.0\.1"):
        get_body("https://metadata.example.com/schema.json")
    assert mock_get.call_count == 0


def test_get_body_ignores_malformed_addrinfo_records(mocker: MockerFixture) -> None:
    """Ignore malformed resolver records instead of failing before the fetch."""
    mocker.patch(
        "socket.getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 0, ""),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
        ],
    )
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/json"}
    mock_response.content = b'{"type": "object"}'
    mock_fetch = mocker.patch("datamodel_code_generator.http._get_http_response", return_value=mock_response)

    result = get_body("https://metadata.example.com/schema.json")

    assert result == '{"type": "object"}'
    assert mock_fetch.call_count == 1


def test_get_body_pins_validated_dns_resolution(mocker: MockerFixture) -> None:
    """Use the DNS result validated by the SSRF guard for the actual HTTP connection."""
    validated_ip = "93.184.216.34"
    mocker.patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", (validated_ip, 0))],
    )
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/json"}
    mock_response.content = b'{"type": "object"}'
    mock_fetch = mocker.patch("datamodel_code_generator.http._get_http_response", return_value=mock_response)

    result = get_body("https://metadata.example.com/schema.json")

    assert result == '{"type": "object"}'
    assert mock_fetch.call_args.kwargs["pinned_host"] == "metadata.example.com"
    assert mock_fetch.call_args.kwargs["pinned_ips"] == (ip_address(validated_ip),)


@pytest.mark.parametrize(
    ("url", "expected_host"),
    [
        ("https://bücher.example/schema.json", "xn--bcher-kva.example"),
        ("https://faß.example/schema.json", "xn--fa-hia.example"),
    ],
)
def test_get_body_pins_idn_hostname_as_canonical_dns_name(
    mocker: MockerFixture,
    url: str,
    expected_host: str,
) -> None:
    """Pin IDN hosts using the same ASCII DNS name that the selected HTTP core connects to."""
    validated_ip = "93.184.216.34"
    mock_getaddrinfo = mocker.patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", (validated_ip, 0))],
    )
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/json"}
    mock_response.content = b'{"type": "object"}'
    mock_fetch = mocker.patch("datamodel_code_generator.http._get_http_response", return_value=mock_response)

    result = get_body(url)

    assert result == '{"type": "object"}'
    assert mock_getaddrinfo.call_args.args == (expected_host, None)
    assert mock_fetch.call_args.kwargs["pinned_host"] == expected_host
    assert mock_fetch.call_args.kwargs["pinned_ips"] == (ip_address(validated_ip),)


@pytest.mark.parametrize("verify", [True, False])
def test_get_http_response_uses_pinned_backend_with_real_local_http(
    mocker: MockerFixture,
    local_http_server: str,
    *,
    verify: bool,
) -> None:
    """Exercise the selected pinned backend with a real local HTTP connection."""
    mocker.stopall()

    response = _get_http_response(
        _get_http_stack(),
        f"{local_http_server}/echo",
        headers=[("X-Test-Header", "yes")],
        verify=verify,
        follow_redirects=False,
        query_parameters=[("q", "1")],
        timeout=5.0,
        pinned_host="localhost",
        pinned_ips=(ip_address("127.0.0.1"),),
    )

    assert response.status_code == 200
    assert json.loads(response.text) == {"path": "/echo?q=1", "test_header": "yes"}

    schema_response = _get_http_response(
        _get_http_stack(),
        f"{local_http_server}/schema.json",
        headers=None,
        verify=verify,
        follow_redirects=False,
        query_parameters=None,
        timeout=5.0,
        pinned_host="localhost",
        pinned_ips=(ip_address("127.0.0.1"),),
    )

    assert schema_response.status_code == 200
    assert schema_response.text == '{"type":"object"}'


@pytest.mark.parametrize("joined", [False, True], ids=("url-argument", "url-equals"))
def test_cli_header_names_the_url_without_credentials_or_query(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    local_http_server: str,
    tmp_path: Path,
    joined: bool,
) -> None:
    """Name a fetched input in the generated header, command and metadata without userinfo, query or fragment."""
    mocker.stopall()
    monkeypatch.chdir(tmp_path)
    url = f"http://user:secret@{local_http_server.removeprefix('http://')}/pet.json?token=SECRET#/x"

    def normalize(text: str) -> str:
        return text.replace(local_http_server, "http://localhost")

    run_main_with_args([
        *([f"--url={url}"] if joined else ["--url", url]),
        "--output",
        "output.py",
        "--input-file-type",
        "jsonschema",
        "--allow-private-network",
        "--disable-timestamp",
        "--enable-command-header",
        "--emit-model-metadata",
        "metadata.json",
        "--formatters",
        "builtin",
    ])
    assert_http_e2e_file(
        tmp_path / "output.py", f"url_header_redacted{'_equals' if joined else ''}.py", transform=normalize
    )
    assert_http_e2e_file(tmp_path / "metadata.json", "url_header_redacted_metadata.txt", transform=normalize)


def test_cli_fetches_external_schema_with_selected_backend(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
) -> None:
    """Fetch, parse, and generate from a real server through the selected backend."""
    mocker.stopall()
    schema_url = f"{local_http_server}/pet.json"

    selected_backend = os.environ.get("DATAMODEL_CODE_GENERATOR_TEST_HTTP_BACKEND", "auto")
    extra_args = ["--allow-private-network", "--disable-timestamp"]
    if selected_backend != "auto":
        extra_args.extend(("--http-backend", selected_backend))

    run_main_url_and_assert(
        url=schema_url,
        output_path=tmp_path / "output.py",
        input_file_type="jsonschema",
        assert_func=assert_http_e2e_file,
        expected_file="backend.py",
        extra_args=extra_args,
        transform=lambda output: output.replace(schema_url, "http://localhost/schema.json"),
    )

    if (expected_backend := os.environ.get("DATAMODEL_CODE_GENERATOR_EXPECTED_HTTP_BACKEND")) is not None:
        expected_auto_backend = os.environ.get(
            "DATAMODEL_CODE_GENERATOR_EXPECTED_AUTO_HTTP_BACKEND",
            expected_backend,
        )
        assert _get_http_stack().backend == expected_auto_backend
        assert _get_http_stack(HTTPBackend(selected_backend)).backend == expected_backend


def test_cli_updates_and_verifies_remote_lock_for_root_and_nested_refs(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
) -> None:
    """Lock the top-level URL and every fetched HTTP reference using a real server."""
    mocker.stopall()
    root_url = f"{local_http_server}/root.json"
    child_url = f"{local_http_server}/child.json"
    _SchemaHandler.routes["/root.json"] = (
        200,
        {"content-type": "application/json"},
        json.dumps({
            "title": "Root",
            "type": "object",
            "properties": {"child": {"$ref": child_url}},
        }).encode(),
    )
    _SchemaHandler.routes["/child.json"] = (
        200,
        {"content-type": "application/json"},
        b'{"title":"Child","type":"object","properties":{"name":{"type":"string"}}}',
    )
    lockfile = tmp_path / "remote.lock"
    update_args = [
        "--allow-private-network",
        "--disable-timestamp",
        "--http-headers",
        "Authorization:Bearer lock-secret",
        "--update-lock",
        "--lockfile",
        str(lockfile),
    ]
    verify_args = [
        "--allow-private-network",
        "--disable-timestamp",
        "--http-headers",
        "Authorization:Bearer lock-secret",
        "--locked",
        "--lockfile",
        str(lockfile),
    ]

    try:
        run_main_url_and_assert(
            url=root_url,
            output_path=tmp_path / "output.py",
            input_file_type="jsonschema",
            assert_func=assert_http_e2e_file,
            expected_file="remote_lock_nested.py",
            extra_args=update_args,
            transform=lambda output: output.replace(root_url, "root.json"),
        )
        lock_content = lockfile.read_text(encoding="utf-8")
        lock_data = json.loads(lock_content)
        assert lock_data["version"] == 1
        assert sorted(resource["url"] for resource in lock_data["resources"]) == [
            local_http_server,
            local_http_server,
        ]
        assert "Authorization" not in lock_content
        assert "Bearer lock-secret" not in lock_content
        assert "?" not in lock_content

        run_main_url_and_assert(
            url=root_url,
            output_path=tmp_path / "verified.py",
            input_file_type="jsonschema",
            assert_func=assert_http_e2e_file,
            expected_file="remote_lock_nested.py",
            extra_args=verify_args,
            transform=lambda output: output.replace(root_url, "root.json"),
        )
        assert lockfile.read_text(encoding="utf-8") == lock_content
    finally:
        del _SchemaHandler.routes["/root.json"]
        del _SchemaHandler.routes["/child.json"]


def test_cli_locked_remote_lock_rejects_changed_real_response(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject upstream body drift until an explicit lock update succeeds."""
    mocker.stopall()
    schema_url = f"{local_http_server}/pet.json"
    lockfile = tmp_path / "remote.lock"
    output_path = tmp_path / "output.py"
    generation_args = [
        "--url",
        schema_url,
        "--output",
        str(output_path),
        "--input-file-type",
        "jsonschema",
        "--allow-private-network",
        "--disable-timestamp",
    ]
    update_args = [
        *generation_args,
        "--update-lock",
        "--lockfile",
        str(lockfile),
    ]
    run_main_with_args(update_args)
    original_lock = lockfile.read_text(encoding="utf-8")
    original_response = _SchemaHandler.routes["/pet.json"]
    _SchemaHandler.routes["/pet.json"] = (
        200,
        {"content-type": "application/json"},
        b"\xff",
    )
    try:
        run_main_with_args(
            [
                *generation_args,
                "--locked",
                "--lockfile",
                str(lockfile),
            ],
            expected_exit=Exit.ERROR,
        )
        stderr = capsys.readouterr().err
        assert "content does not match lock" in stderr
        assert "Traceback" not in stderr
        assert lockfile.read_text(encoding="utf-8") == original_lock
    finally:
        _SchemaHandler.routes["/pet.json"] = original_response


def test_cli_locks_final_real_redirect_response_under_the_original_request(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
) -> None:
    """Persist only the final body, under the logical request that redirected."""
    from datamodel_code_generator.remote_lock import _request_sha256, _sha256

    mocker.stopall()
    redirect_url = f"{local_http_server}/redirect.json"
    final_url = f"{local_http_server}/final.json"
    _SchemaHandler.routes["/redirect.json"] = (
        302,
        {"content-type": "application/json", "location": final_url},
        b'{"redirect":"body-is-locked-too"}',
    )
    _SchemaHandler.routes["/final.json"] = _SchemaHandler.routes["/pet.json"]
    lockfile = tmp_path / "remote.lock"
    update_args = ["--allow-private-network", "--disable-timestamp", "--update-lock", "--lockfile", str(lockfile)]

    try:
        run_main_url_and_assert(
            url=redirect_url,
            output_path=tmp_path / "output.py",
            input_file_type="jsonschema",
            assert_func=assert_http_e2e_file,
            expected_file="backend.py",
            extra_args=update_args,
            transform=lambda output: output.replace(redirect_url, "http://localhost/schema.json"),
        )
        lock_data = json.loads(lockfile.read_text(encoding="utf-8"))
        assert [resource["url"] for resource in lock_data["resources"]] == [local_http_server]
        assert lock_data["resources"][0]["body_sha256"] == _sha256(_SchemaHandler.routes["/pet.json"][2])
        assert lock_data["resources"][0]["request_sha256"] == _request_sha256(redirect_url, None, None)

        run_main_url_and_assert(
            url=redirect_url,
            output_path=tmp_path / "verified.py",
            input_file_type="jsonschema",
            assert_func=assert_http_e2e_file,
            expected_file="backend.py",
            extra_args=["--allow-private-network", "--disable-timestamp", "--locked", "--lockfile", str(lockfile)],
            transform=lambda output: output.replace(redirect_url, "http://localhost/schema.json"),
        )
    finally:
        del _SchemaHandler.routes["/redirect.json"]
        del _SchemaHandler.routes["/final.json"]


def test_get_body_uses_configured_encoding_despite_response_charset(
    mocker: MockerFixture,
    local_http_server: str,
) -> None:
    """Decode exactly the locked bytes using the requested encoding, not HTTPX heuristics."""
    mocker.stopall()
    path = "/latin-1.json"
    _SchemaHandler.routes[path] = (200, {"content-type": "application/json; charset=utf-8"}, b'{"title":"caf\xe9"}')

    try:
        body = get_body(f"{local_http_server}{path}", allow_private_network=True, encoding="latin-1")
        assert body == '{"title":"caf\xe9"}'
    finally:
        del _SchemaHandler.routes[path]


def test_cli_reports_undecodable_real_response_without_a_lock(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A raw decode failure is a concise schema-fetch error even without locking."""
    mocker.stopall()
    path = "/undecodable.json"
    _SchemaHandler.routes[path] = (200, {"content-type": "application/json"}, b"\xff")

    try:
        run_main_with_args(
            [
                "--url",
                f"{local_http_server}{path}",
                "--output",
                str(tmp_path / "output.py"),
                "--input-file-type",
                "jsonschema",
                "--allow-private-network",
            ],
            expected_exit=Exit.ERROR,
        )
        stderr = capsys.readouterr().err
        assert "Unable to decode response" in stderr
        assert "Traceback" not in stderr
    finally:
        del _SchemaHandler.routes[path]


@pytest.mark.benchmark
def test_generate_without_a_lock_does_not_copy_public_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    """The common local-input path allocates no defensive remote-lock config copy."""
    (tmp_path / "schema.json").write_text('{"title":"Model","type":"object"}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    config = GenerateConfig(
        disable_timestamp=True,
        formatters=[],
        input_file_type=InputFileType.JsonSchema,
    )

    model_copy = mocker.patch.object(
        GenerateConfig,
        "model_copy",
        side_effect=AssertionError("no-lock generation copied its public config"),
    )
    result = generate(Path("schema.json"), config=config)

    assert isinstance(result, str)
    model_copy.assert_not_called()


@pytest.mark.benchmark
@pytest.mark.skipif(os.name == "nt", reason="the shared local mirror uses symlinks")
def test_no_lock_shared_local_mirror_keeps_one_cache_entry(tmp_path: Path) -> None:
    """No-lock aliases retain the shared physical-file cache key and object."""
    mirror_root = tmp_path / "schemas"
    shared_path = mirror_root / "shared.json"
    shared_path.parent.mkdir()
    shared_path.write_text('{"title":"Shared","type":"object"}', encoding="utf-8")
    for host in ("first.example", "second.example"):
        host_path = mirror_root / host
        host_path.mkdir()
        (host_path / "shared.json").symlink_to(shared_path)
    parser = JsonSchemaParser("", allow_remote_refs=False, http_local_ref_path=mirror_root)

    first = parser._get_ref_body_from_url("https://first.example/shared.json")
    second = parser._get_ref_body_from_url("https://second.example/shared.json")

    assert first is second
    assert len(parser.remote_object_cache) == 1


def test_cli_locks_local_http_reference_mirrors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Treat a local HTTP-reference mirror as the same locked remote identity."""
    root_path = tmp_path / "root.json"
    mirror_path = tmp_path / "schemas" / "registry.example" / "child.json"
    root_path.write_text(
        json.dumps({
            "title": "Root",
            "type": "object",
            "properties": {"child": {"$ref": "https://registry.example/child.json"}},
        }),
        encoding="utf-8",
    )
    mirror_path.parent.mkdir(parents=True)
    mirror_path.write_text(
        '{"title":"Child","type":"object","properties":{"name":{"type":"string"}}}', encoding="utf-8"
    )
    lockfile = tmp_path / "remote.lock"
    output_path = tmp_path / "output.py"
    common_args = [
        "--input",
        str(root_path),
        "--output",
        str(output_path),
        "--input-file-type",
        "jsonschema",
        "--http-local-ref-path",
        str(tmp_path / "schemas"),
        "--disable-timestamp",
        "--lockfile",
        str(lockfile),
    ]

    run_main_with_args([*common_args, "--update-lock"])
    assert_http_e2e_file(output_path, "remote_lock_nested.py")
    lock_content = lockfile.read_text(encoding="utf-8")
    assert '"url": "https://registry.example"' in lock_content

    mirror_path.write_text(
        '{"title":"Child","type":"object","properties":{"age":{"type":"integer"}}}', encoding="utf-8"
    )
    run_main_with_args(
        [*common_args, "--locked"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="content does not match lock",
    )


@pytest.mark.parametrize(
    ("mirror_content", "expected_error"),
    [
        (b"\xff", "UnicodeDecodeError"),
        (b"[]", "TypeError: Expected dict, got list"),
    ],
)
def test_cli_reports_invalid_locked_local_http_mirror_content(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mirror_content: bytes,
    expected_error: str,
) -> None:
    """Local mirror decode and root-shape failures use clean source-aware diagnostics."""
    root_path = tmp_path / "root.json"
    root_path.write_text(
        json.dumps({
            "title": "Root",
            "type": "object",
            "properties": {"child": {"$ref": "https://registry.example/child.json"}},
        }),
        encoding="utf-8",
    )
    mirror_path = tmp_path / "schemas" / "registry.example" / "child.json"
    mirror_path.parent.mkdir(parents=True)
    mirror_path.write_bytes(mirror_content)
    output_path = tmp_path / "output.py"
    lockfile = tmp_path / "remote.lock"

    run_main_with_args(
        [
            "--input",
            str(root_path),
            "--output",
            str(output_path),
            "--input-file-type",
            "jsonschema",
            "--http-local-ref-path",
            str(tmp_path / "schemas"),
            "--update-lock",
            "--lockfile",
            str(lockfile),
        ],
        expected_exit=Exit.ERROR,
    )
    stderr = capsys.readouterr().err
    assert f"Invalid file format for jsonschema at {mirror_path}" in stderr
    assert expected_error in stderr
    assert "Traceback" not in stderr
    assert not output_path.exists()
    assert not lockfile.exists()


@pytest.mark.skipif(os.name == "nt", reason="the shared local mirror uses symlinks")
def test_cli_locks_each_remote_identity_that_uses_the_same_local_mirror(
    tmp_path: Path,
) -> None:
    """A mirror cache entry cannot hide a second remote identity from lock verification."""
    root_path = tmp_path / "root.json"
    root_path.write_text(
        json.dumps({
            "title": "Root",
            "type": "object",
            "properties": {
                "first": {"$ref": "https://first.example/shared.json"},
                "second": {"$ref": "https://second.example/shared.json"},
            },
        }),
        encoding="utf-8",
    )
    mirror_root = tmp_path / "schemas"
    shared_path = mirror_root / "shared.json"
    shared_path.parent.mkdir()
    shared_path.write_text('{"title":"Shared","type":"object"}', encoding="utf-8")
    for host in ("first.example", "second.example"):
        host_path = mirror_root / host
        host_path.mkdir()
        (host_path / "shared.json").symlink_to(shared_path)

    lockfile = tmp_path / "remote.lock"
    common_args = [
        "--input",
        str(root_path),
        "--output",
        str(tmp_path / "output.py"),
        "--input-file-type",
        "jsonschema",
        "--http-local-ref-path",
        str(mirror_root),
        "--disable-timestamp",
        "--lockfile",
        str(lockfile),
    ]
    run_main_with_args([*common_args, "--update-lock"])

    resources = json.loads(lockfile.read_text(encoding="utf-8"))["resources"]
    assert len(resources) == 2
    assert {resource["url"] for resource in resources} == {"https://first.example", "https://second.example"}

    run_main_with_args([*common_args, "--locked"])


@pytest.mark.skipif(os.name == "nt", reason="the raw host:port local-mirror path is not a valid Windows path")
def test_cli_verifies_an_http_lock_through_an_equivalent_local_mirror(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
) -> None:
    """An HTTP-created lock remains portable when the same URL is read from a mirror."""
    mocker.stopall()
    path = "/portable-child.json"
    child_url = f"{local_http_server}{path}"
    child_body = b'{"title":"Child","type":"object"}'
    _SchemaHandler.routes[path] = (200, {"content-type": "application/json"}, child_body)
    root_path = tmp_path / "root.json"
    root_path.write_text(
        json.dumps({
            "title": "Root",
            "type": "object",
            "properties": {"child": {"$ref": child_url}},
        }),
        encoding="utf-8",
    )
    lockfile = tmp_path / "remote.lock"
    common_args = [
        "--input",
        str(root_path),
        "--output",
        str(tmp_path / "output.py"),
        "--input-file-type",
        "jsonschema",
        "--allow-remote-refs",
        "--allow-private-network",
        "--disable-timestamp",
        "--lockfile",
        str(lockfile),
    ]

    try:
        run_main_with_args([*common_args, "--update-lock"])
        mirror_path = tmp_path / "schemas" / local_http_server.removeprefix("http://") / path.removeprefix("/")
        mirror_path.parent.mkdir(parents=True)
        mirror_path.write_bytes(child_body)
        run_main_with_args([
            *common_args,
            "--http-local-ref-path",
            str(tmp_path / "schemas"),
            "--locked",
        ])
    finally:
        del _SchemaHandler.routes[path]


def test_cli_uses_default_lockfile_beside_project_pyproject(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    local_http_server: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An existing default project lock verifies ordinary generation automatically."""
    mocker.stopall()
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / "pyproject.toml").write_text("[tool.datamodel-codegen]\n", encoding="utf-8")
    monkeypatch.chdir(project_path)
    schema_url = f"{local_http_server}/pet.json"
    output_path = tmp_path / "output.py"
    common_args = [
        "--url",
        schema_url,
        "--output",
        str(output_path),
        "--input-file-type",
        "jsonschema",
        "--allow-private-network",
        "--disable-timestamp",
    ]
    lockfile = project_path / "datamodel-codegen.lock"

    run_main_with_args([*common_args, "--update-lock"])
    assert lockfile.is_file()
    lock_content = lockfile.read_text(encoding="utf-8")
    run_main_with_args(common_args)

    original_response = _SchemaHandler.routes["/pet.json"]
    _SchemaHandler.routes["/pet.json"] = (
        200,
        {"content-type": "application/json"},
        b'{"title":"ChangedPet","type":"object"}',
    )
    try:
        run_main_with_args(
            common_args,
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="content does not match lock",
        )
        assert lockfile.read_text(encoding="utf-8") == lock_content
    finally:
        _SchemaHandler.routes["/pet.json"] = original_response


def test_cli_selected_lockfile_verifies_only_when_it_exists_and_update_creates_it(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Selecting a missing lock is a no-lock run; an existing one verifies automatically."""
    mocker.stopall()
    path = "/selected-lock.json"
    url = f"{local_http_server}{path}"
    lockfile = tmp_path / "selected.lock"
    output = tmp_path / "output.py"
    original_body = b'{"title":"Selected","type":"object"}'
    _SchemaHandler.routes[path] = (200, {"content-type": "application/json"}, original_body)
    common_args = [
        "--url",
        url,
        "--output",
        str(output),
        "--input-file-type",
        "jsonschema",
        "--allow-private-network",
        "--disable-timestamp",
        "--lockfile",
        str(lockfile),
    ]

    try:
        run_main_with_args(common_args)
        assert not lockfile.exists()

        run_main_with_args([*common_args, "--update-lock"])
        recorded_lock = lockfile.read_text(encoding="utf-8")
        run_main_with_args(common_args)
        assert lockfile.read_text(encoding="utf-8") == recorded_lock

        _SchemaHandler.routes[path] = (
            200,
            {"content-type": "application/json"},
            b'{"title":"Changed","type":"object"}',
        )
        run_main_with_args(
            common_args,
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="content does not match lock",
        )

        missing_locked = tmp_path / "missing.lock"
        run_main_with_args(
            [*common_args, "--locked", "--lockfile", str(missing_locked)],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="Remote lock file not found",
        )
    finally:
        del _SchemaHandler.routes[path]


def test_cli_lock_flags_override_pyproject_lock_mode(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    local_http_server: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each explicit CLI lock mode replaces the opposite pyproject default."""
    mocker.stopall()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    path = "/lock-precedence.json"
    url = f"{local_http_server}{path}"
    lockfile = project / "datamodel-codegen.lock"
    _SchemaHandler.routes[path] = (200, {"content-type": "application/json"}, b'{"title":"First","type":"object"}')
    common_args = [
        "--url",
        url,
        "--output",
        str(project / "output.py"),
        "--input-file-type",
        "jsonschema",
        "--allow-private-network",
        "--disable-timestamp",
    ]

    try:
        (project / "pyproject.toml").write_text("[tool.datamodel-codegen]\nupdate-lock = true\n", encoding="utf-8")
        run_main_with_args(common_args)
        original_lock = lockfile.read_text(encoding="utf-8")

        _SchemaHandler.routes[path] = (200, {"content-type": "application/json"}, b'{"title":"Second","type":"object"}')
        run_main_with_args(
            [*common_args, "--locked"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="content does not match lock",
        )
        assert lockfile.read_text(encoding="utf-8") == original_lock

        (project / "pyproject.toml").write_text("[tool.datamodel-codegen]\nlocked = true\n", encoding="utf-8")
        run_main_with_args([*common_args, "--update-lock"])
        assert lockfile.read_text(encoding="utf-8") != original_lock
    finally:
        del _SchemaHandler.routes[path]


def test_cli_lock_flags_override_selected_profile_lock_mode(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Explicit lock flags take precedence over the selected profile's lock policy."""
    mocker.stopall()
    schema_url = f"{local_http_server}/pet.json"
    lockfile = tmp_path / "remote.lock"
    output_path = tmp_path / "output.py"
    (tmp_path / "pyproject.toml").write_text(
        """[tool.datamodel-codegen]
allow-private-network = true
disable-timestamp = true
input-file-type = "jsonschema"
lockfile = "remote.lock"

[tool.datamodel-codegen.profiles.update]
update-lock = true

[tool.datamodel-codegen.profiles.locked]
locked = true
""",
        encoding="utf-8",
    )
    original_response = _SchemaHandler.routes["/pet.json"]
    try:
        with chdir(tmp_path):
            run_main_with_args(["--url", schema_url, "--output", str(output_path), "--profile", "update"])
            assert_http_e2e_file(
                output_path,
                "backend.py",
                transform=lambda output: output.replace(schema_url, "http://localhost/schema.json"),
            )
            original_lock = lockfile.read_text(encoding="utf-8")
            _SchemaHandler.routes["/pet.json"] = (
                200,
                {"content-type": "application/json"},
                b'{"title":"ChangedPet","type":"object"}',
            )
            run_main_with_args(
                ["--url", schema_url, "--output", str(output_path), "--profile", "update", "--locked"],
                expected_exit=Exit.ERROR,
                capsys=capsys,
                expected_stderr_contains="content does not match lock",
            )
            assert lockfile.read_text(encoding="utf-8") == original_lock
            run_main_with_args([
                "--url",
                schema_url,
                "--output",
                str(output_path),
                "--profile",
                "locked",
                "--update-lock",
            ])
            assert lockfile.read_text(encoding="utf-8") != original_lock
    finally:
        _SchemaHandler.routes["/pet.json"] = original_response


@pytest.mark.allow_direct_assert
def test_remote_lock_transaction_reuses_the_collector_after_deepcopy_and_stages_once(tmp_path: Path) -> None:
    """A deep-copied job resolves the same collector and retains one staged lock file."""
    from datamodel_code_generator.__main__ import Config, _remote_lock_plan, _RemoteLockTransaction

    config = Config(lockfile=tmp_path / "remote.lock", update_lock=True)
    transaction = _RemoteLockTransaction.open((("first", config, None),), (_remote_lock_plan(config, None),))
    assert transaction is not None
    copied_config = config.model_copy(deep=True)
    plan = _remote_lock_plan(config, None)
    collector = transaction.collector_for(plan)
    assert collector is transaction.collector_for(plan)
    assert collector is not None
    copied_config.resolve_remote_lock(collector)
    assert copied_config.remote_lock is collector
    collector.record_response("https://schemas.example/schema.json", None, None, b"schema")
    first_stage = transaction.staged_files()
    second_stage = transaction.staged_files()
    assert len(first_stage) == 1
    assert first_stage == second_stage
    transaction.discard()


@pytest.mark.allow_direct_assert
def test_remote_lock_transaction_attempts_all_cleanup_after_one_staged_source_unlink_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed staged-source unlink does not leak another lock's private directory or anchor."""
    from datamodel_code_generator import _publication as publication_module
    from datamodel_code_generator.__main__ import Config, _remote_lock_plan, _RemoteLockTransaction

    first = Config(lockfile=tmp_path / "first.lock", update_lock=True)
    second = Config(lockfile=tmp_path / "second.lock", update_lock=True)
    entries = (("first", first, None), ("second", second, None))
    plans = tuple(_remote_lock_plan(config, None) for _, config, _ in entries)
    transaction = _RemoteLockTransaction.open(entries, plans)
    assert transaction is not None
    for plan in plans:
        collector = transaction.collector_for(plan)
        assert collector is not None
        collector.record_response("https://schemas.example/schema.json", None, None, plan.path.name.encode())
    transaction.staged_files()
    contexts = tuple(transaction._staging_contexts.values())
    original_unlink = publication_module._unlink
    failed_once = False

    def fail_one_source_unlink(path: str | Path, *args: object, **kwargs: object) -> None:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            msg = "simulated staged cleanup failure"
            raise OSError(msg)
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(publication_module, "_unlink", fail_one_source_unlink)

    with pytest.raises(OSError, match="simulated staged cleanup failure"):
        transaction.discard()

    assert transaction._staging_contexts == {}
    assert transaction._anchors == {}
    assert not any(context.path.exists() for context in contexts)

    monkeypatch.undo()
    transaction = _RemoteLockTransaction.open(entries, plans)
    assert transaction is not None
    contexts = tuple(transaction._staging_contexts.values())
    anchors = tuple(transaction._anchors.values())
    original_cleanup = publication_module.StagingDirectory.cleanup
    original_close_anchor = publication_module.close_anchor

    def fail_after_first_context_cleanup(context: publication_module.StagingDirectory) -> None:
        original_cleanup(context)
        if context is contexts[0]:
            msg = "simulated staging-context cleanup failure"
            raise OSError(msg)

    def fail_after_first_anchor_close(anchor: publication_module.PublicationAnchor | None) -> None:
        original_close_anchor(anchor)
        if anchor is anchors[0]:
            msg = "simulated anchor cleanup failure"
            raise OSError(msg)

    monkeypatch.setattr(publication_module.StagingDirectory, "cleanup", fail_after_first_context_cleanup)
    monkeypatch.setattr(publication_module, "close_anchor", fail_after_first_anchor_close)
    with pytest.raises(OSError, match="simulated staging-context cleanup failure"):
        transaction.discard()

    assert transaction._staging_contexts == {}
    assert transaction._anchors == {}
    assert not any(context.path.exists() for context in contexts)


@pytest.mark.allow_direct_assert
def test_remote_lock_transaction_cleans_open_and_second_stage_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every opened lock resource is released when a later lock cannot open or stage."""
    from datamodel_code_generator import _publication as publication_module
    from datamodel_code_generator.__main__ import Config, _remote_lock_plan, _RemoteLockTransaction
    from datamodel_code_generator.remote_lock import RemoteReferenceLock

    first = Config(lockfile=tmp_path / "first.lock", update_lock=True)
    invalid_second = Config(lockfile=tmp_path / "second.lock", update_lock=True)
    failed_entries = (("first", first, None), ("second", invalid_second, None))
    failed_plans = tuple(_remote_lock_plan(config, None) for _, config, _ in failed_entries)
    original_create = publication_module.StagingDirectory.create
    staging_calls = 0

    def fail_second_staging_directory(
        anchor: publication_module.PublicationAnchor, *, prefix: str
    ) -> publication_module.StagingDirectory:
        nonlocal staging_calls
        staging_calls += 1
        if staging_calls == 2:
            msg = "second staging directory reservation failed"
            raise OSError(msg)
        return original_create(anchor, prefix=prefix)

    monkeypatch.setattr(publication_module.StagingDirectory, "create", fail_second_staging_directory)

    with pytest.raises(Error, match="second staging directory reservation failed"):
        _RemoteLockTransaction.open(failed_entries, failed_plans)

    assert not list(tmp_path.glob(".datamodel-codegen-lock-*"))
    monkeypatch.undo()

    second = Config(lockfile=tmp_path / "second.lock", update_lock=True)
    entries = (("first", first, None), ("second", second, None))
    plans = tuple(_remote_lock_plan(config, None) for _, config, _ in entries)
    transaction = _RemoteLockTransaction.open(entries, plans)
    assert transaction is not None
    contexts = tuple(transaction._staging_contexts.values())
    original_stage = RemoteReferenceLock.stage

    def fail_only_second_stage(lock: RemoteReferenceLock, *args: object, **kwargs: object) -> Path | object:
        if lock.path == second.lockfile:
            msg = "second staged lock write failed"
            raise OSError(msg)
        return original_stage(lock, *args, **kwargs)

    monkeypatch.setattr(RemoteReferenceLock, "stage", fail_only_second_stage)
    with pytest.raises(Error, match="second staged lock write failed"):
        transaction.staged_files()

    assert transaction._staging_contexts == {}
    assert transaction._anchors == {}
    assert not any(context.path.exists() for context in contexts)


def test_generate_reuses_remote_lock_config_without_retaining_a_collector(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    local_http_server: str,
    tmp_path: Path,
) -> None:
    """Every public API invocation opens, commits, and releases its own collector."""
    from datamodel_code_generator.remote_lock import RemoteLockError

    mocker.stopall()
    monkeypatch.chdir(tmp_path)
    schema_url = f"{local_http_server}/pet.json"
    lockfile = Path("remote.lock")
    config = GenerateConfig(
        allow_private_network=True,
        allow_remote_refs=True,
        disable_timestamp=True,
        input_file_type=InputFileType.JsonSchema,
        lockfile=lockfile,
        update_lock=True,
    )
    generate(urlparse(schema_url), config=config)
    first_lock = lockfile.read_text(encoding="utf-8")
    assert config.remote_lock is None
    assert not config.remote_lock_resolved

    original_response = _SchemaHandler.routes["/pet.json"]
    _SchemaHandler.routes["/pet.json"] = (
        200,
        {"content-type": "application/json"},
        b'{"title":"ChangedPet","type":"object"}',
    )
    try:
        generate(urlparse(schema_url), config=config)
        assert lockfile.read_text(encoding="utf-8") != first_lock
        locked_config = config.model_copy(update={"locked": True, "update_lock": False})
        _SchemaHandler.routes["/pet.json"] = (
            200,
            {"content-type": "application/json"},
            b'{"title":"ThirdPet","type":"object"}',
        )
        with pytest.raises(RemoteLockError, match="content does not match lock"):
            generate(urlparse(schema_url), config=locked_config)
    finally:
        _SchemaHandler.routes["/pet.json"] = original_response


def test_generate_publishes_output_metadata_and_remote_lock_as_one_journal(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
) -> None:
    """Public generation publishes every update artifact only after the complete journal is ready."""
    mocker.stopall()
    schema_url = f"{local_http_server}/pet.json"
    output_path = tmp_path / "output.py"
    metadata_path = tmp_path / "metadata.json"
    lockfile = tmp_path / "remote.lock"
    config = GenerateConfig(
        allow_private_network=True,
        disable_timestamp=True,
        input_file_type=InputFileType.JsonSchema,
        output=output_path,
        emit_model_metadata=metadata_path,
        lockfile=lockfile,
        update_lock=True,
    )
    generate(urlparse(schema_url), config=config)
    assert_http_e2e_file(
        output_path,
        "backend.py",
        transform=lambda output: output.replace(schema_url, "http://localhost/schema.json"),
    )
    assert_http_e2e_file(
        metadata_path,
        "remote_lock_stdout_metadata.txt",
        transform=lambda output: output.replace(local_http_server, "http://localhost"),
    )
    original_lock = lockfile.read_text(encoding="utf-8")
    original_response = _SchemaHandler.routes["/pet.json"]
    _SchemaHandler.routes["/pet.json"] = (
        200,
        {"content-type": "application/json"},
        b'{"title":"ChangedPet","type":"object"}',
    )
    try:
        mocker.patch("datamodel_code_generator._publication._replace_source", side_effect=OSError("full"))
        with pytest.raises(OSError, match="full"):
            generate(urlparse(schema_url), config=config)
        assert_http_e2e_file(
            output_path,
            "backend.py",
            transform=lambda output: output.replace(schema_url, "http://localhost/schema.json"),
        )
        assert_http_e2e_file(
            metadata_path,
            "remote_lock_stdout_metadata.txt",
            transform=lambda output: output.replace(local_http_server, "http://localhost"),
        )
        assert lockfile.read_text(encoding="utf-8") == original_lock
    finally:
        _SchemaHandler.routes["/pet.json"] = original_response


def test_generate_rolls_back_output_and_metadata_when_late_lock_publication_and_cleanup_fail(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
) -> None:
    """A late lock replacement failure restores prior artifacts even when lock cleanup also fails."""
    from datamodel_code_generator import _publication as publication_module
    from datamodel_code_generator.remote_lock import RemoteReferenceLock

    mocker.stopall()
    schema_url = f"{local_http_server}/pet.json"
    output_path = tmp_path / "output.py"
    metadata_path = tmp_path / "metadata.json"
    lockfile = tmp_path / "remote.lock"
    config = GenerateConfig(
        allow_private_network=True,
        disable_timestamp=True,
        input_file_type=InputFileType.JsonSchema,
        output=output_path,
        emit_model_metadata=metadata_path,
        lockfile=lockfile,
        update_lock=True,
    )
    generate(urlparse(schema_url), config=config)
    original_lock = lockfile.read_text(encoding="utf-8")
    original_response = _SchemaHandler.routes["/pet.json"]
    _SchemaHandler.routes["/pet.json"] = (
        200,
        {"content-type": "application/json"},
        b'{"title":"ChangedPet","type":"object"}',
    )
    original_replace_source = publication_module._replace_source
    replacement_error = "simulated late lock replacement failure"
    cleanup_error = "simulated lock cleanup failure"

    def fail_lock_replacement(
        file: publication_module.StagedFile,
        destination_name: str | Path,
        destination_fd: int | None,
    ) -> None:
        if file.target == lockfile:
            raise OSError(replacement_error)
        original_replace_source(file, destination_name, destination_fd)

    try:
        mocker.patch.object(publication_module, "_replace_source", side_effect=fail_lock_replacement)
        mocker.patch.object(RemoteReferenceLock, "discard_stage", side_effect=OSError(cleanup_error))
        with pytest.raises(OSError, match=replacement_error):
            generate(urlparse(schema_url), config=config)
        assert_http_e2e_file(
            output_path,
            "backend.py",
            transform=lambda output: output.replace(schema_url, "http://localhost/schema.json"),
        )
        assert_http_e2e_file(
            metadata_path,
            "remote_lock_stdout_metadata.txt",
            transform=lambda output: output.replace(local_http_server, "http://localhost"),
        )
        assert lockfile.read_text(encoding="utf-8") == original_lock
    finally:
        _SchemaHandler.routes["/pet.json"] = original_response


def test_generate_publishes_metadata_and_lock_while_returning_stdout(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
) -> None:
    """Metadata-only updates retain generated stdout while sharing the lock journal."""
    mocker.stopall()
    schema_url = f"{local_http_server}/pet.json"
    metadata_path = tmp_path / "metadata.json"
    lockfile = tmp_path / "remote.lock"
    generated = generate(
        urlparse(schema_url),
        config=GenerateConfig(
            allow_private_network=True,
            disable_timestamp=True,
            input_file_type=InputFileType.JsonSchema,
            emit_model_metadata=metadata_path,
            lockfile=lockfile,
            update_lock=True,
        ),
    )

    assert isinstance(generated, str)
    assert_output(
        f"{generated.replace(schema_url, 'http://localhost/schema.json')}\n",
        HTTP_E2E_DATA_PATH / "expected" / "http" / "backend.py",
    )
    assert_http_e2e_file(
        metadata_path,
        "remote_lock_stdout_metadata.txt",
        transform=lambda output: output.replace(local_http_server, "http://localhost"),
    )
    assert lockfile.is_file()


def test_generate_returns_stdout_and_updates_a_lock_without_artifacts(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
) -> None:
    """A no-output public call returns code and commits its ordinary update lock once."""
    mocker.stopall()
    schema_url = f"{local_http_server}/pet.json"
    lockfile = tmp_path / "remote.lock"
    generated = generate(
        urlparse(schema_url),
        config=GenerateConfig(
            allow_private_network=True,
            disable_timestamp=True,
            input_file_type=InputFileType.JsonSchema,
            lockfile=lockfile,
            update_lock=True,
        ),
    )

    assert isinstance(generated, str)
    assert_output(
        f"{generated.replace(schema_url, 'http://localhost/schema.json')}\n",
        HTTP_E2E_DATA_PATH / "expected" / "http" / "backend.py",
    )
    assert lockfile.is_file()


@pytest.mark.parametrize("output", [None, Path("atomic.py")], ids=["non-atomic", "atomic"])
def test_generate_expands_a_tilde_lockfile_before_resolving_the_caller_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    output: Path | None,
) -> None:
    """Both public update paths expand an explicit lockfile before applying the invocation directory."""
    home = tmp_path / "home"
    caller = tmp_path / "caller"
    home.mkdir()
    caller.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(caller)

    generate(
        JSON_SCHEMA_DATA_PATH / "person.json",
        config=GenerateConfig(
            disable_timestamp=True,
            input_file_type=InputFileType.JsonSchema,
            lockfile=Path("~/remote.lock"),
            output=output,
            update_lock=True,
        ),
    )

    assert (home / "remote.lock").is_file()
    assert not (caller / "~").exists()


def test_generate_publishes_relative_nested_artifacts_and_lock_together(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    local_http_server: str,
    tmp_path: Path,
) -> None:
    """Relative nested output, metadata, and lock paths retain their original caller context."""
    mocker.stopall()
    monkeypatch.chdir(tmp_path)
    schema_url = f"{local_http_server}/pet.json"
    output_path = Path("generated/models.py")
    metadata_path = Path("generated/metadata/model-map.json")
    lockfile = Path("locks/remote.lock")
    generate(
        urlparse(schema_url),
        config=GenerateConfig(
            allow_private_network=True,
            disable_timestamp=True,
            input_file_type=InputFileType.JsonSchema,
            output=output_path,
            emit_model_metadata=metadata_path,
            lockfile=lockfile,
            update_lock=True,
        ),
    )

    assert_http_e2e_file(
        tmp_path / output_path,
        "backend.py",
        transform=lambda output: output.replace(schema_url, "http://localhost/schema.json"),
    )
    assert_http_e2e_file(
        tmp_path / metadata_path,
        "remote_lock_stdout_metadata.txt",
        transform=lambda output: output.replace(local_http_server, "http://localhost"),
    )
    assert (tmp_path / lockfile).is_file()


def test_generate_uses_logical_output_context_for_custom_formatter_and_lock(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """Legacy formatter state resolves resources from the logical output, not its staging path."""
    mocker.stopall()
    output_path = tmp_path / "generated" / "models.py"
    output_path.parent.mkdir()
    license_path = output_path.parent / "license.txt"
    license_path.write_text(
        (DATA_PATH / "python" / "custom_formatters" / "license_example.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    with chdir(tmp_path):
        generate(
            JSON_SCHEMA_DATA_PATH / "person.json",
            config=GenerateConfig(
                disable_timestamp=True,
                input_file_type=InputFileType.JsonSchema,
                output=Path("generated/models.py"),
                custom_formatters=["tests.data.python.custom_formatters.add_license"],
                custom_formatters_kwargs={"license_file": "license.txt"},
                lockfile=Path("locks/remote.lock"),
                update_lock=True,
            ),
        )

    assert_output(
        output_path.read_text(encoding="utf-8"),
        DATA_PATH / "expected" / "main_kr" / "jobs" / "custom_formatter.py",
    )
    assert (tmp_path / "locks/remote.lock").is_file()


def test_generate_publishes_extensionless_file_output_with_remote_lock(tmp_path: Path) -> None:
    """A missing extensionless public output remains a single file during atomic lock updates."""
    output_path = tmp_path / "models"
    lockfile = tmp_path / "remote.lock"

    generate(
        JSON_SCHEMA_DATA_PATH / "person.json",
        config=GenerateConfig(
            disable_timestamp=True,
            input_file_type=InputFileType.JsonSchema,
            output=output_path,
            lockfile=lockfile,
            update_lock=True,
        ),
    )

    assert output_path.is_file()
    assert_output(output_path.read_text(encoding="utf-8"), DATA_PATH / "expected" / "main" / "person.py")
    assert lockfile.is_file()


@pytest.mark.skipif(os.name == "nt", reason="lockfile symlink creation requires elevated privileges")
def test_generate_update_lock_preserves_lockfile_symlink(tmp_path: Path) -> None:
    """Public atomic updates resolve a lockfile symlink and replace its target instead."""
    output_path = tmp_path / "output.py"
    lockfile = tmp_path / "remote.lock"
    lock_target = tmp_path / "remote-target.lock"
    lockfile.symlink_to(lock_target)

    generate(
        JSON_SCHEMA_DATA_PATH / "person.json",
        config=GenerateConfig(
            disable_timestamp=True,
            input_file_type=InputFileType.JsonSchema,
            output=output_path,
            lockfile=lockfile,
            update_lock=True,
        ),
    )

    assert lockfile.is_symlink()
    assert lockfile.readlink() == lock_target
    assert lock_target.is_file()
    assert_output(output_path.read_text(encoding="utf-8"), DATA_PATH / "expected" / "main" / "person.py")


def test_generate_rejects_a_lockfile_inside_a_multimodule_output_before_fetching(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
) -> None:
    """A lock cannot replace a generated module below a suffixless multi-module output root."""
    mocker.stopall()
    output_path = tmp_path / "models"
    lockfile = output_path / "__init__.py"

    with pytest.raises(Error, match="Output and Remote lock paths must not overlap"):
        generate(
            urlparse(f"{local_http_server}/pet.json"),
            config=GenerateConfig(
                allow_private_network=True,
                disable_timestamp=True,
                input_file_type=InputFileType.JsonSchema,
                module_split_mode=ModuleSplitMode.Single,
                output=output_path,
                lockfile=lockfile,
                update_lock=True,
            ),
        )

    assert not output_path.exists()


@pytest.mark.parametrize("output_exists", [False, True])
def test_generate_publishes_multimodule_directory_with_remote_lock(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
    output_exists: bool,
) -> None:
    """Public update-lock generation publishes both new and existing suffixless module directories."""
    mocker.stopall()
    schema_url = f"{local_http_server}/root.json"
    child_url = f"{local_http_server}/child.json"
    output_path = tmp_path / "models"
    lockfile = tmp_path / "remote.lock"
    _SchemaHandler.routes["/root.json"] = (
        200,
        {"content-type": "application/json"},
        json.dumps({
            "title": "Root",
            "type": "object",
            "properties": {"child": {"$ref": child_url}},
        }).encode(),
    )
    _SchemaHandler.routes["/child.json"] = (
        200,
        {"content-type": "application/json"},
        b'{"title":"Child","type":"object","properties":{"name":{"type":"string"}}}',
    )
    if output_exists:
        output_path.mkdir()
    config = GenerateConfig(
        allow_private_network=True,
        allow_remote_refs=True,
        disable_timestamp=True,
        input_file_type=InputFileType.JsonSchema,
        module_split_mode=ModuleSplitMode.Single,
        output=output_path,
        lockfile=lockfile,
        update_lock=True,
    )
    try:
        generate(urlparse(schema_url), config=config)
        assert output_path.is_dir()
        assert_http_e2e_file(
            output_path / "__init__.py",
            "public_remote_module_init.py",
            transform=lambda output: output.replace(schema_url, "http://localhost/root.json"),
        )
        assert_http_e2e_file(
            output_path / "child.py",
            "public_remote_module_child.py",
            transform=lambda output: output.replace(schema_url, "http://localhost/root.json"),
        )
        assert_http_e2e_file(
            output_path / "root.py",
            "public_remote_module_root.py",
            transform=lambda output: output.replace(schema_url, "http://localhost/root.json"),
        )
        assert lockfile.is_file()
    finally:
        del _SchemaHandler.routes["/root.json"]
        del _SchemaHandler.routes["/child.json"]


def test_generate_atomic_remote_update_omits_absent_generated_artifacts(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """An interrupted generator can still publish its lock without creating empty public artifacts."""
    output_path = tmp_path / "output.py"
    metadata_path = tmp_path / "metadata.json"
    lockfile = tmp_path / "remote.lock"
    mocker.patch("datamodel_code_generator._generate", return_value=None)

    generate(
        JSON_SCHEMA_DATA_PATH / "person.json",
        config=GenerateConfig(
            disable_timestamp=True,
            emit_model_metadata=metadata_path,
            input_file_type=InputFileType.JsonSchema,
            output=output_path,
            lockfile=lockfile,
            update_lock=True,
        ),
    )

    _assert_file_does_not_exist(output_path)
    _assert_file_does_not_exist(metadata_path)
    assert_http_e2e_file(lockfile, "remote_lock_empty.txt")


def test_generate_atomic_remote_update_releases_output_resources_after_lock_staging_failure(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """A failed private lock reservation releases the already-open public output anchor."""
    from datamodel_code_generator import _publication as publication_module

    output_path = tmp_path / "output.py"
    lockfile = tmp_path / "remote.lock"
    mocker.patch.object(publication_module.StagingDirectory, "create", side_effect=OSError("lock staging failed"))

    with pytest.raises(OSError, match="lock staging failed"):
        generate(
            JSON_SCHEMA_DATA_PATH / "person.json",
            config=GenerateConfig(
                disable_timestamp=True,
                input_file_type=InputFileType.JsonSchema,
                output=output_path,
                lockfile=lockfile,
                update_lock=True,
            ),
        )

    _assert_file_does_not_exist(output_path)
    _assert_file_does_not_exist(lockfile)


def test_generate_atomic_remote_update_releases_temporary_output_when_anchor_reservation_fails(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """An output-anchor failure does not leak its private temporary directory or lock state."""
    from datamodel_code_generator import _publication as publication_module

    output_path = tmp_path / "output.py"
    lockfile = tmp_path / "remote.lock"
    mocker.patch.object(publication_module, "publication_anchor", side_effect=OSError("output anchor failed"))

    with pytest.raises(OSError, match="output anchor failed"):
        generate(
            JSON_SCHEMA_DATA_PATH / "person.json",
            config=GenerateConfig(
                disable_timestamp=True,
                input_file_type=InputFileType.JsonSchema,
                output=output_path,
                lockfile=lockfile,
                update_lock=True,
            ),
        )

    _assert_file_does_not_exist(output_path)
    _assert_file_does_not_exist(lockfile)


@pytest.mark.skipif(os.name == "nt", reason="descriptor anchor races require POSIX dir_fd support")
def test_generate_rejects_a_changed_public_output_anchor_before_publication(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
) -> None:
    """A destination race aborts the shared public generation journal before replacing artifacts."""
    from datamodel_code_generator import _publication as publication_module

    mocker.stopall()
    schema_url = f"{local_http_server}/pet.json"
    output_path = tmp_path / "output.py"
    metadata_path = tmp_path / "metadata.json"
    lockfile = tmp_path / "remote.lock"
    config = GenerateConfig(
        allow_private_network=True,
        disable_timestamp=True,
        input_file_type=InputFileType.JsonSchema,
        output=output_path,
        emit_model_metadata=metadata_path,
        lockfile=lockfile,
        update_lock=True,
    )
    generate(urlparse(schema_url), config=config)
    original_lock = lockfile.read_text(encoding="utf-8")
    original_response = _SchemaHandler.routes["/pet.json"]
    _SchemaHandler.routes["/pet.json"] = (
        200,
        {"content-type": "application/json"},
        b'{"title":"ChangedPet","type":"object"}',
    )
    mocker.patch.object(publication_module, "_directory_fd_matches_path", return_value=False)
    try:
        with pytest.raises(OSError, match="destination anchor changed"):
            generate(urlparse(schema_url), config=config)
        assert_http_e2e_file(
            output_path,
            "backend.py",
            transform=lambda output: output.replace(schema_url, "http://localhost/schema.json"),
        )
        assert_http_e2e_file(
            metadata_path,
            "remote_lock_stdout_metadata.txt",
            transform=lambda output: output.replace(local_http_server, "http://localhost"),
        )
        assert lockfile.read_text(encoding="utf-8") == original_lock
    finally:
        _SchemaHandler.routes["/pet.json"] = original_response


def test_cli_does_not_fall_back_to_a_subdirectory_lock_when_project_lock_is_absent(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    local_http_server: str,
    tmp_path: Path,
) -> None:
    """The CLI's pyproject-root lock decision is forwarded to public generation once."""
    from datamodel_code_generator.remote_lock import RemoteReferenceLock

    mocker.stopall()
    project_path = tmp_path / "project"
    work_path = project_path / "nested"
    work_path.mkdir(parents=True)
    (project_path / "pyproject.toml").write_text("[tool.datamodel-codegen]\n", encoding="utf-8")
    schema_url = f"{local_http_server}/pet.json"
    nested_lock = RemoteReferenceLock.open(work_path / "datamodel-codegen.lock", update=True, locked=False)
    nested_lock.record_response(schema_url, None, None, b"outdated")
    nested_lock.commit()
    monkeypatch.chdir(work_path)

    run_main_url_and_assert(
        url=schema_url,
        output_path=tmp_path / "output.py",
        input_file_type="jsonschema",
        assert_func=assert_http_e2e_file,
        expected_file="backend.py",
        extra_args=["--allow-private-network", "--disable-timestamp"],
        transform=lambda output: output.replace(schema_url, "http://localhost/schema.json"),
    )


def test_cli_preserves_existing_lock_when_atomic_update_fails(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failed atomic replacement never corrupts the previous complete lock file."""
    mocker.stopall()
    schema_url = f"{local_http_server}/pet.json"
    lockfile = tmp_path / "remote.lock"
    output_path = tmp_path / "output.py"
    common_args = [
        "--url",
        schema_url,
        "--output",
        str(output_path),
        "--input-file-type",
        "jsonschema",
        "--allow-private-network",
        "--disable-timestamp",
        "--update-lock",
        "--lockfile",
        str(lockfile),
    ]
    run_main_with_args(common_args)
    original_lock = lockfile.read_text(encoding="utf-8")
    assert_http_e2e_file(
        output_path,
        "backend.py",
        transform=lambda output: output.replace(schema_url, "http://localhost/schema.json"),
    )
    original_response = _SchemaHandler.routes["/pet.json"]
    _SchemaHandler.routes["/pet.json"] = (
        200,
        {"content-type": "application/json"},
        b'{"title":"ChangedPet","type":"object"}',
    )
    try:
        mocker.patch("datamodel_code_generator._publication._replace_source", side_effect=OSError("full"))
        run_main_with_args(
            common_args,
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="could not publish batch output",
        )
        assert lockfile.read_text(encoding="utf-8") == original_lock
        assert_http_e2e_file(
            output_path,
            "backend.py",
            transform=lambda output: output.replace(schema_url, "http://localhost/schema.json"),
        )
    finally:
        _SchemaHandler.routes["/pet.json"] = original_response


def test_cli_does_not_commit_an_updated_lock_when_check_fails(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
) -> None:
    """A check-mode difference is a failed command and cannot publish a new lock."""
    mocker.stopall()
    output_path = tmp_path / "output.py"
    output_path.write_text("# stale\n", encoding="utf-8")
    lockfile = tmp_path / "remote.lock"

    run_main_with_args(
        [
            "--url",
            f"{local_http_server}/pet.json",
            "--output",
            str(output_path),
            "--input-file-type",
            "jsonschema",
            "--allow-private-network",
            "--disable-timestamp",
            "--check",
            "--update-lock",
            "--lockfile",
            str(lockfile),
        ],
        expected_exit=Exit.DIFF,
    )
    assert not lockfile.exists()


def test_cli_batch_check_update_lock_keeps_the_existing_output_and_discards_the_staged_lock(tmp_path: Path) -> None:
    """A clean batch check cannot publish a newly staged update-mode lock artifact."""
    source_input = JSON_SCHEMA_DATA_PATH / "person.json"
    output_path = tmp_path / "output.py"
    output_path.write_text(
        (DATA_PATH / "expected" / "main" / "person.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    lockfile = tmp_path / "remote.lock"
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
allow-private-network = true
check = true
disable-timestamp = true
input-file-type = "jsonschema"
update-lock = true

[tool.datamodel-codegen.jobs.pet]
input = "{source_input.as_posix()}"
output = "{output_path.as_posix()}"
lockfile = "{lockfile.as_posix()}"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(["--all-jobs", "--formatters", "builtin"])

    assert_output(output_path.read_text(encoding="utf-8"), DATA_PATH / "expected" / "main" / "person.py")
    assert not lockfile.exists()


def test_cli_check_update_lock_keeps_the_existing_output_and_discards_the_staged_lock(tmp_path: Path) -> None:
    """A clean single-command check also discards its active transaction rather than committing a lock."""
    source_input = JSON_SCHEMA_DATA_PATH / "person.json"
    output_path = tmp_path / "output.py"
    output_path.write_text(
        (DATA_PATH / "expected" / "main" / "person.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    lockfile = tmp_path / "remote.lock"

    run_main_with_args([
        "--input",
        str(source_input),
        "--output",
        str(output_path),
        "--input-file-type",
        "jsonschema",
        "--disable-timestamp",
        "--check",
        "--update-lock",
        "--lockfile",
        str(lockfile),
    ])

    assert_output(output_path.read_text(encoding="utf-8"), DATA_PATH / "expected" / "main" / "person.py")
    assert not lockfile.exists()


def test_cli_batch_mixed_check_and_update_lock_publishes_the_shared_write_job_lock(tmp_path: Path) -> None:
    """A check-only job cannot suppress a shared lock's staging for a later updating job."""
    source_input = JSON_SCHEMA_DATA_PATH / "person.json"
    check_output = tmp_path / "check.py"
    check_output.write_text(
        (DATA_PATH / "expected" / "main" / "person.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    write_output = tmp_path / "write.py"
    lockfile = tmp_path / "remote.lock"
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs.check]
input = "{source_input.as_posix()}"
output = "{check_output.as_posix()}"
check = true
update-lock = true
lockfile = "{lockfile.as_posix()}"

[tool.datamodel-codegen.jobs.write]
input = "{source_input.as_posix()}"
output = "{write_output.as_posix()}"
update-lock = true
lockfile = "{lockfile.as_posix()}"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(["--all-jobs", "--formatters", "builtin"])

    expected_output = DATA_PATH / "expected" / "main" / "person.py"
    assert_output(check_output.read_text(encoding="utf-8"), expected_output)
    assert_output(write_output.read_text(encoding="utf-8"), expected_output)
    assert_http_e2e_file(lockfile, "remote_lock_empty.txt")


@pytest.mark.allow_direct_assert
def test_cli_batch_reports_lock_cleanup_failure_after_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A committed batch reports lock cleanup separately from publication."""
    from datamodel_code_generator import __main__ as main_module

    source_input = JSON_SCHEMA_DATA_PATH / "person.json"
    output_path = tmp_path / "output.py"
    lockfile = tmp_path / "remote.lock"
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs.person]
input = "{source_input.as_posix()}"
output = "{output_path.as_posix()}"
update-lock = true
lockfile = "{lockfile.as_posix()}"
""",
        encoding="utf-8",
    )
    close_anchors = main_module._RemoteLockTransaction._close_anchors
    cleanup_message = "simulated lock cleanup failure"

    def close_anchors_then_fail(transaction: main_module._RemoteLockTransaction) -> None:
        close_anchors(transaction)
        raise OSError(cleanup_message)

    monkeypatch.setattr(main_module._RemoteLockTransaction, "_close_anchors", close_anchors_then_fail)
    with chdir(tmp_path):
        run_main_with_args(["--all-jobs", "--formatters", "builtin"], expected_exit=Exit.ERROR)
    captured = capsys.readouterr()

    assert "could not clean batch output staging: simulated lock cleanup failure" in captured.err
    assert "could not publish batch output" not in captured.err
    assert_output(output_path.read_text(encoding="utf-8"), DATA_PATH / "expected" / "main" / "person.py")
    assert_http_e2e_file(lockfile, "remote_lock_empty.txt")


@pytest.mark.allow_direct_assert
def test_cli_remote_lock_transaction_reports_command_spool_failure(
    mocker: MockerFixture,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A command-spool allocation failure discards the pre-opened lock transaction without publishing."""
    source_input = JSON_SCHEMA_DATA_PATH / "person.json"
    output_path = tmp_path / "output.py"
    lockfile = tmp_path / "remote.lock"
    mocker.patch(
        "datamodel_code_generator.__main__.tempfile.TemporaryFile",
        side_effect=OSError("simulated command spool failure"),
    )

    run_main_with_args(
        [
            "--input",
            str(source_input),
            "--output",
            str(output_path),
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--update-lock",
            "--lockfile",
            str(lockfile),
        ],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="could not prepare command output staging",
    )

    assert not output_path.exists()
    assert not lockfile.exists()


@pytest.mark.allow_direct_assert
def test_cli_remote_lock_transaction_reports_cleanup_failure_after_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A cleanup-only failure is a clean command error after the journal has committed."""
    from datamodel_code_generator import __main__ as main_module

    source_input = JSON_SCHEMA_DATA_PATH / "person.json"
    output_path = tmp_path / "output.py"
    lockfile = tmp_path / "remote.lock"
    real_cleanup = main_module._cleanup_staged_job_plans
    cleanup_message = "simulated output cleanup failure"

    def cleanup_with_error(staged_plans: object) -> tuple[OSError, ...]:
        return (*real_cleanup(staged_plans), OSError(cleanup_message))

    monkeypatch.setattr(main_module, "_cleanup_staged_job_plans", cleanup_with_error)
    run_main_with_args(
        [
            "--input",
            str(source_input),
            "--output",
            str(output_path),
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--update-lock",
            "--lockfile",
            str(lockfile),
        ],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="could not clean up command transaction",
    )

    assert_output(output_path.read_text(encoding="utf-8"), DATA_PATH / "expected" / "main" / "person.py")
    assert_http_e2e_file(lockfile, "remote_lock_empty.txt")


@pytest.mark.allow_direct_assert
def test_cli_remote_lock_transaction_reports_anchor_cleanup_after_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A committed command reports anchor cleanup separately from publication."""
    from datamodel_code_generator import __main__ as main_module

    output_path = tmp_path / "output.py"
    lockfile = tmp_path / "remote.lock"
    close_anchors = main_module._RemoteLockTransaction._close_anchors
    cleanup_message = "simulated lock cleanup failure"

    def close_anchors_then_fail(transaction: main_module._RemoteLockTransaction) -> None:
        close_anchors(transaction)
        raise OSError(cleanup_message)

    monkeypatch.setattr(main_module._RemoteLockTransaction, "_close_anchors", close_anchors_then_fail)
    run_main_with_args(
        [
            "--input",
            str(JSON_SCHEMA_DATA_PATH / "person.json"),
            "--output",
            str(output_path),
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--update-lock",
            "--lockfile",
            str(lockfile),
        ],
        expected_exit=Exit.ERROR,
    )
    captured = capsys.readouterr()

    assert "could not clean up command transaction: simulated lock cleanup failure" in captured.err
    assert "could not publish batch output" not in captured.err
    assert_output(output_path.read_text(encoding="utf-8"), DATA_PATH / "expected" / "main" / "person.py")
    assert_http_e2e_file(lockfile, "remote_lock_empty.txt")


@pytest.mark.allow_direct_assert
def test_cli_remote_lock_transaction_keeps_primary_error_when_cleanup_also_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Generation errors remain visible while every output and lock cleanup is attempted."""
    from datamodel_code_generator import __main__ as main_module

    invalid_schema = tmp_path / "invalid.json"
    invalid_schema.write_text('{"title":"123InvalidName","type":"object"}', encoding="utf-8")
    output_path = tmp_path / "output.py"
    lockfile = tmp_path / "remote.lock"
    real_cleanup = main_module._cleanup_staged_job_plans
    real_discard = main_module._RemoteLockTransaction.discard
    cleanup_message = "simulated output cleanup failure"
    lock_cleanup_message = "simulated lock cleanup failure"
    discard_attempted = False

    def cleanup_with_error(staged_plans: object) -> tuple[OSError, ...]:
        return (*real_cleanup(staged_plans), OSError(cleanup_message))

    def discard_then_fail(transaction: object) -> None:
        nonlocal discard_attempted
        discard_attempted = True
        real_discard(transaction)
        raise OSError(lock_cleanup_message)

    monkeypatch.setattr(main_module, "_cleanup_staged_job_plans", cleanup_with_error)
    monkeypatch.setattr(main_module._RemoteLockTransaction, "discard", discard_then_fail)
    run_main_with_args(
        [
            "--input",
            str(invalid_schema),
            "--output",
            str(output_path),
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--update-lock",
            "--lockfile",
            str(lockfile),
        ],
        expected_exit=Exit.ERROR,
    )
    captured = capsys.readouterr()

    assert "You have to set `--class-name` option" in captured.err
    assert "could not clean up command transaction" in captured.err
    assert discard_attempted
    _assert_file_does_not_exist(output_path)
    _assert_file_does_not_exist(lockfile)


def test_cli_batch_shares_the_default_remote_lock_and_verifies_the_union(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
) -> None:
    """One batch update publishes the shared observed closure only after both jobs succeed."""
    mocker.stopall()
    first_reference = f"{local_http_server}/first.json"
    second_reference = f"{local_http_server}/second.json"
    _SchemaHandler.routes["/first.json"] = (
        200,
        {"content-type": "application/json"},
        b'{"type":"string"}',
    )
    _SchemaHandler.routes["/second.json"] = (
        200,
        {"content-type": "application/json"},
        b'{"type":"integer"}',
    )
    first_input = tmp_path / "first.json"
    second_input = tmp_path / "second.json"
    first_input.write_text(
        json.dumps({"type": "object", "properties": {"first": {"$ref": first_reference}}}), encoding="utf-8"
    )
    second_input_content = json.dumps({"type": "object", "properties": {"second": {"$ref": second_reference}}})
    second_input.write_text(second_input_content, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
allow-private-network = true
allow-remote-refs = true
disable-timestamp = true
input-file-type = "jsonschema"
update-lock = true

[tool.datamodel-codegen.jobs.first]
input = "{first_input.as_posix()}"
output = "{(tmp_path / "first.py").as_posix()}"

[tool.datamodel-codegen.jobs.second]
input = "{second_input.as_posix()}"
output = "{(tmp_path / "second.py").as_posix()}"
""",
        encoding="utf-8",
    )
    lockfile = tmp_path / "datamodel-codegen.lock"

    def normalize_lockfile(content: str) -> str:
        lock = json.loads(content)
        resources = sorted(
            (
                {
                    "body_sha256": resource["body_sha256"],
                    "request_sha256": "<normalized>",
                    "url": resource["url"].replace(local_http_server, "http://localhost"),
                }
                for resource in lock["resources"]
            ),
            key=itemgetter("body_sha256"),
        )
        return json.dumps({"resources": resources, "version": lock["version"]}, indent=2, sort_keys=True) + "\n"

    try:
        with chdir(tmp_path):
            run_main_with_args(["--all-jobs", "--formatters", "builtin"])
            assert_http_e2e_file(tmp_path / "first.py", "remote_lock_batch_first.py")
            assert_http_e2e_file(tmp_path / "second.py", "remote_lock_batch_second.py")
            assert_http_e2e_file(lockfile, "remote_lock_batch_union.txt", transform=normalize_lockfile)
            second_input.write_text("{", encoding="utf-8")
            run_main_with_args(["--all-jobs", "--formatters", "builtin"], expected_exit=Exit.ERROR)
            assert_http_e2e_file(tmp_path / "first.py", "remote_lock_batch_first.py")
            assert_http_e2e_file(tmp_path / "second.py", "remote_lock_batch_second.py")
            assert_http_e2e_file(lockfile, "remote_lock_batch_union.txt", transform=normalize_lockfile)
            second_input.write_text(second_input_content, encoding="utf-8")
            _SchemaHandler.routes["/second.json"] = (
                200,
                {"content-type": "application/json"},
                b'{"type":"number"}',
            )
            run_main_with_args(["--all-jobs", "--locked", "--formatters", "builtin"], expected_exit=Exit.ERROR)
            _SchemaHandler.routes["/second.json"] = (
                200,
                {"content-type": "application/json"},
                b'{"type":"integer"}',
            )
            first_input.write_text('{"type":"object"}', encoding="utf-8")
            run_main_with_args(["--all-jobs", "--formatters", "builtin"])
            assert_http_e2e_file(lockfile, "remote_lock_batch_pruned.txt", transform=normalize_lockfile)
            run_main_with_args(["--all-jobs", "--locked", "--formatters", "builtin"])
    finally:
        del _SchemaHandler.routes["/first.json"]
        del _SchemaHandler.routes["/second.json"]


def test_cli_batch_rejects_shared_lock_policy_conflicts_before_generation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A shared lock cannot receive incompatible policies from two jobs."""
    first_input = tmp_path / "first.json"
    second_input = tmp_path / "second.json"
    first_input.write_text('{"type":"object"}', encoding="utf-8")
    second_input.write_text('{"type":"object"}', encoding="utf-8")
    lockfile = tmp_path / "remote.lock"
    first_output = tmp_path / "first.py"
    second_output = tmp_path / "second.py"
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen.jobs.first]
input = "{first_input.as_posix()}"
output = "{first_output.as_posix()}"
input-file-type = "jsonschema"
update-lock = true
lockfile = "{lockfile.as_posix()}"

[tool.datamodel-codegen.jobs.second]
input = "{second_input.as_posix()}"
output = "{second_output.as_posix()}"
input-file-type = "jsonschema"
locked = true
lockfile = "{lockfile.as_posix()}"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(
            ["--all-jobs", "--formatters", "builtin"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="Remote lock policy conflict",
        )

    assert not first_output.exists()
    assert not second_output.exists()
    assert not lockfile.exists()


def test_cli_batch_ignores_inactive_lock_paths_that_share_a_generated_artifact(tmp_path: Path) -> None:
    """Inactive lock settings neither reserve nor publish their otherwise unsafe paths."""
    source_input = JSON_SCHEMA_DATA_PATH / "person.json"
    first_output = tmp_path / "first.py"
    second_output = tmp_path / "second.py"
    active_output = tmp_path / "active.py"
    active_lock = tmp_path / "active.lock"
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen.jobs.first]
input = "{source_input.as_posix()}"
output = "{first_output.as_posix()}"
input-file-type = "jsonschema"
disable-timestamp = true
lockfile = "{first_output.as_posix()}"

[tool.datamodel-codegen.jobs.second]
input = "{source_input.as_posix()}"
output = "{second_output.as_posix()}"
input-file-type = "jsonschema"
disable-timestamp = true
lockfile = "{first_output.as_posix()}"

[tool.datamodel-codegen.jobs.active]
input = "{source_input.as_posix()}"
output = "{active_output.as_posix()}"
input-file-type = "jsonschema"
disable-timestamp = true
update-lock = true
lockfile = "{active_lock.as_posix()}"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(["--all-jobs", "--formatters", "builtin"])

    expected_output = DATA_PATH / "expected" / "main" / "person.py"
    assert_output(first_output.read_text(encoding="utf-8"), expected_output)
    assert_output(second_output.read_text(encoding="utf-8"), expected_output)
    assert_output(active_output.read_text(encoding="utf-8"), expected_output)
    assert_http_e2e_file(active_lock, "remote_lock_empty.txt")


@pytest.mark.skipif(os.name == "nt", reason="the alias fixture requires POSIX symlinks")
def test_cli_batch_rejects_update_lock_aliases_before_generation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Distinct lock spellings for one replacement target fail before either job runs."""
    source_input = JSON_SCHEMA_DATA_PATH / "person.json"
    locks = tmp_path / "locks"
    locks.mkdir()
    lockfile = locks / "remote.lock"
    alias = locks / "remote-alias.lock"
    alias.symlink_to(lockfile.name)
    first_output = tmp_path / "first.py"
    second_output = tmp_path / "second.py"
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen.jobs.first]
input = "{source_input.as_posix()}"
output = "{first_output.as_posix()}"
input-file-type = "jsonschema"
update-lock = true
lockfile = "{lockfile.as_posix()}"

[tool.datamodel-codegen.jobs.second]
input = "{source_input.as_posix()}"
output = "{second_output.as_posix()}"
input-file-type = "jsonschema"
update-lock = true
lockfile = "{alias.as_posix()}"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(
            ["--all-jobs", "--formatters", "builtin"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="aliases with ambiguous replacement semantics",
        )

    assert not first_output.exists()
    assert not second_output.exists()
    assert not lockfile.exists()
    assert alias.is_symlink()


def test_cli_batch_rejects_parent_child_lock_paths_before_generation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Nested lock destinations cannot make one batch publish into another lock path."""
    source_input = JSON_SCHEMA_DATA_PATH / "person.json"
    lock_parent = tmp_path / "locks"
    first_output = tmp_path / "first.py"
    second_output = tmp_path / "second.py"
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen.jobs.first]
input = "{source_input.as_posix()}"
output = "{first_output.as_posix()}"
input-file-type = "jsonschema"
update-lock = true
lockfile = "{lock_parent.as_posix()}"

[tool.datamodel-codegen.jobs.second]
input = "{source_input.as_posix()}"
output = "{second_output.as_posix()}"
input-file-type = "jsonschema"
update-lock = true
lockfile = "{(lock_parent / "nested.lock").as_posix()}"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(
            ["--all-jobs", "--formatters", "builtin"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="Remote lock paths for 'first' and 'second' overlap",
        )

    assert not first_output.exists()
    assert not second_output.exists()
    assert not lock_parent.exists()


def test_cli_diff_locks_old_and_new_remote_closures_without_committing_on_difference(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Diff compares both remote closures, but DIFF and ERROR leave the prior lock untouched."""
    mocker.stopall()
    old_url = f"{local_http_server}/old/shared.json"
    new_url = f"{local_http_server}/new/shared.json"
    shared_body = b'{"title":"Shared","type":"object"}'
    _SchemaHandler.routes["/old/shared.json"] = (200, {"content-type": "application/json"}, shared_body)
    _SchemaHandler.routes["/new/shared.json"] = (200, {"content-type": "application/json"}, shared_body)
    old_input = tmp_path / "old" / "schema.json"
    new_input = tmp_path / "new" / "schema.json"
    old_input.parent.mkdir()
    new_input.parent.mkdir()
    old_input.write_text(json.dumps({"$ref": old_url}), encoding="utf-8")
    new_input.write_text(json.dumps({"$ref": new_url}), encoding="utf-8")
    lockfile = tmp_path / "remote.lock"
    virtual_output = tmp_path / "virtual.py"
    common_args = [
        "--input",
        str(new_input),
        "--diff-against",
        str(old_input),
        "--output",
        str(virtual_output),
        "--input-file-type",
        "jsonschema",
        "--allow-remote-refs",
        "--allow-private-network",
        "--disable-timestamp",
        "--lockfile",
        str(lockfile),
    ]

    def normalize_lockfile(content: str) -> str:
        lock = json.loads(content)
        resources = [
            {
                "body_sha256": resource["body_sha256"],
                "request_sha256": "<normalized>",
                "url": resource["url"].replace(local_http_server, "http://localhost"),
            }
            for resource in lock["resources"]
        ]
        return json.dumps({"resources": resources, "version": lock["version"]}, indent=2, sort_keys=True) + "\n"

    try:
        run_main_with_args([*common_args, "--update-lock"])
        assert not virtual_output.exists()
        assert_http_e2e_file(lockfile, "remote_lock_diff_union.txt", transform=normalize_lockfile)

        _SchemaHandler.routes["/new/shared.json"] = (
            200,
            {"content-type": "application/json"},
            b'{"title":"Changed","type":"object","properties":{"age":{"type":"integer"}}}',
        )
        run_main_with_args([*common_args, "--update-lock"], expected_exit=Exit.DIFF, capsys=capsys)
        assert_output(
            capsys.readouterr().out.replace(" \n", "\n"),
            HTTP_E2E_DATA_PATH / "expected/http/remote_lock_diff_changed.txt",
        )
        assert_http_e2e_file(lockfile, "remote_lock_diff_union.txt", transform=normalize_lockfile)
        run_main_with_args(
            [*common_args, "--locked"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="content does not match lock",
        )
        assert_http_e2e_file(lockfile, "remote_lock_diff_union.txt", transform=normalize_lockfile)
    finally:
        del _SchemaHandler.routes["/old/shared.json"]
        del _SchemaHandler.routes["/new/shared.json"]


def test_cli_rolls_back_new_lock_parent_and_stdout_when_publication_fails(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failed first lock publication leaves neither stdout nor transaction-created parents behind."""
    mocker.stopall()
    lockfile = tmp_path / "locks" / "nested" / "remote.lock"
    mocker.patch("datamodel_code_generator._publication._replace_source", side_effect=OSError("full"))

    run_main_with_args(
        [
            "--url",
            f"{local_http_server}/pet.json",
            "--input-file-type",
            "jsonschema",
            "--allow-private-network",
            "--disable-timestamp",
            "--update-lock",
            "--lockfile",
            str(lockfile),
        ],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="could not publish batch output",
    )

    captured = capsys.readouterr()
    assert not captured.out
    assert not lockfile.parent.exists()


@pytest.mark.skipif(os.name == "nt", reason="the race fixture requires POSIX directory symlinks")
def test_cli_does_not_write_through_a_lock_parent_symlink_swap(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Lock staging rejects an attacker-owned name and stays bound after a parent swap."""
    from datamodel_code_generator import __main__ as main_module
    from datamodel_code_generator import _publication as publication_module

    mocker.stopall()
    lock_parent = tmp_path / "locks"
    moved_parent = tmp_path / "locks-before-swap"
    outside = tmp_path / "outside"
    lock_parent.mkdir()
    outside.mkdir()
    attacker_staging = lock_parent / ".datamodel-codegen-lock-attacker"
    attacker_staging.symlink_to(outside, target_is_directory=True)
    original_generate = main_module.run_generate_from_config

    def swap_lock_parent(*args: object, **kwargs: object) -> object:
        lock_parent.rename(moved_parent)
        lock_parent.symlink_to(outside, target_is_directory=True)
        return original_generate(*args, **kwargs)

    mocker.patch("datamodel_code_generator.__main__.run_generate_from_config", side_effect=swap_lock_parent)
    mocker.patch.object(
        publication_module,
        "_private_name",
        side_effect=(
            ".datamodel-codegen-lock-attacker",
            ".datamodel-codegen-lock-owned",
            ".remote.lock-owned",
        ),
    )
    try:
        run_main_with_args(
            [
                "--url",
                f"{local_http_server}/pet.json",
                "--output",
                str(tmp_path / "output.py"),
                "--input-file-type",
                "jsonschema",
                "--allow-private-network",
                "--disable-timestamp",
                "--update-lock",
                "--lockfile",
                str(lock_parent / "remote.lock"),
            ],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="could not publish batch output",
        )
        assert not (outside / "remote.lock").exists()
        assert (moved_parent / attacker_staging.name).is_symlink()
        assert not (moved_parent / ".datamodel-codegen-lock-owned").exists()
    finally:
        lock_parent.unlink()
        moved_parent.rename(lock_parent)


def test_cli_rolls_back_stdout_metadata_and_lock_when_publication_fails(
    mocker: MockerFixture,
    local_http_server: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Metadata-only staging shares the lock publication rollback journal."""
    mocker.stopall()
    metadata_path = tmp_path / "metadata.json"
    lockfile = tmp_path / "remote.lock"
    common_args = [
        "--url",
        f"{local_http_server}/pet.json",
        "--input-file-type",
        "jsonschema",
        "--allow-private-network",
        "--disable-timestamp",
        "--emit-model-metadata",
        str(metadata_path),
        "--update-lock",
        "--lockfile",
        str(lockfile),
    ]

    def normalize_metadata(content: str) -> str:
        return content.replace(local_http_server, "http://localhost")

    run_main_with_args(common_args)
    assert_http_e2e_file(metadata_path, "remote_lock_stdout_metadata.txt", transform=normalize_metadata)
    original_lock = lockfile.read_text(encoding="utf-8")
    original_response = _SchemaHandler.routes["/pet.json"]
    _SchemaHandler.routes["/pet.json"] = (
        200,
        {"content-type": "application/json"},
        b'{"title":"ChangedPet","type":"object"}',
    )
    try:
        mocker.patch("datamodel_code_generator._publication._replace_source", side_effect=OSError("full"))
        run_main_with_args(
            common_args,
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="could not publish batch output",
        )
        assert lockfile.read_text(encoding="utf-8") == original_lock
        assert_http_e2e_file(metadata_path, "remote_lock_stdout_metadata.txt", transform=normalize_metadata)
    finally:
        _SchemaHandler.routes["/pet.json"] = original_response


def test_load_http_stack_rejects_unknown_backend() -> None:
    """Reject backend names outside the exhaustive internal selector."""
    with pytest.raises(AssertionError, match="invalid"):
        _load_http_stack("invalid")  # type: ignore[arg-type]


def test_auto_http_stack_falls_back_and_caches_when_httpx_is_absent(mocker: MockerFixture) -> None:
    """Use experimental HTTPX2 only when the stable top-level package is absent."""
    missing_httpx = ModuleNotFoundError("No module named 'httpx'", name="httpx")
    experimental_stack = Mock()
    load_stack = mocker.patch(
        "datamodel_code_generator.http._load_http_stack",
        side_effect=[missing_httpx, experimental_stack],
    )
    mocker.patch("datamodel_code_generator.http._HTTP_STACKS", {})
    mocker.patch("datamodel_code_generator.http._AUTO_HTTP_STACK", None)

    assert _get_http_stack() is experimental_stack
    assert _get_http_stack() is experimental_stack
    assert [called.args for called in load_stack.call_args_list] == [("httpx",), ("httpx2",)]


def test_http_stack_propagates_broken_backend_import(mocker: MockerFixture) -> None:
    """Do not hide a selected backend whose own dependency import is broken."""
    missing_internal_dependency = ModuleNotFoundError("No module named 'sniffio'", name="sniffio")
    mocker.patch("datamodel_code_generator.http._load_http_stack", side_effect=missing_internal_dependency)
    mocker.patch("datamodel_code_generator.http._HTTP_STACKS", {})
    mocker.patch("datamodel_code_generator.http._AUTO_HTTP_STACK", None)

    with pytest.raises(ModuleNotFoundError, match="sniffio"):
        _get_http_stack()


def test_http_stack_reports_both_install_options_when_no_backend_exists(mocker: MockerFixture) -> None:
    """Explain stable and experimental installation choices when neither backend exists."""
    mocker.patch(
        "datamodel_code_generator.http._load_http_stack",
        side_effect=[
            ModuleNotFoundError("No module named 'httpx'", name="httpx"),
            ModuleNotFoundError("No module named 'httpx2'", name="httpx2"),
        ],
    )
    mocker.patch("datamodel_code_generator.http._HTTP_STACKS", {})
    mocker.patch("datamodel_code_generator.http._AUTO_HTTP_STACK", None)

    with pytest.raises(Exception, match=r"datamodel-code-generator\[httpx2\]"):
        _get_http_stack()


def test_explicit_http_stack_does_not_fall_back_when_selected_client_is_absent(mocker: MockerFixture) -> None:
    """Report the selected extra without probing another backend."""
    missing_httpx2 = ModuleNotFoundError("No module named 'httpx2'", name="httpx2")
    load_stack = mocker.patch("datamodel_code_generator.http._load_http_stack", side_effect=missing_httpx2)
    mocker.patch("datamodel_code_generator.http._HTTP_STACKS", {})

    with pytest.raises(ModuleNotFoundError, match=r"datamodel-code-generator\[httpx2\]"):
        _get_http_stack(HTTPBackend.HTTPX2)

    load_stack.assert_called_once_with("httpx2")


def test_http_fetch_session_reuses_successful_dns_result(mocker: MockerFixture) -> None:
    """Reuse successful DNS validation only within one fetch session."""
    mocker.stopall()

    with _HTTPFetchSession() as session:
        first_result = session.get_ips_from_host("localhost")
        second_result = session.get_ips_from_host("localhost")

    assert first_result
    assert second_result is first_result


def test_http_fetch_session_retries_failed_dns_result(mocker: MockerFixture) -> None:
    """Do not retain failed DNS lookups that may recover during a parser run."""
    public_ip = ip_address("93.184.216.34")
    resolver = mocker.patch(
        "datamodel_code_generator.http._get_ips_from_host",
        side_effect=[(), (public_ip,)],
    )

    with _HTTPFetchSession() as session:
        assert session.get_ips_from_host("schema.example") == ()
        assert session.get_ips_from_host("schema.example") == (public_ip,)

    assert resolver.call_count == 2


def test_http_fetch_session_bounds_successful_dns_cache_with_lru_eviction(mocker: MockerFixture) -> None:
    """Retain only the most recently used successful DNS results."""
    public_ip = ip_address("93.184.216.34")
    mocker.patch("datamodel_code_generator.http._HTTP_FETCH_DNS_CACHE_MAX_SIZE", 2)
    resolver = mocker.patch("datamodel_code_generator.http._get_ips_from_host", return_value=(public_ip,))

    with _HTTPFetchSession() as session:
        assert session.get_ips_from_host("one.example") == (public_ip,)
        assert session.get_ips_from_host("two.example") == (public_ip,)
        assert session.get_ips_from_host("one.example") == (public_ip,)
        assert session.get_ips_from_host("three.example") == (public_ip,)
        assert session.get_ips_from_host("two.example") == (public_ip,)

    assert [call.args[0] for call in resolver.call_args_list] == [
        "one.example",
        "two.example",
        "three.example",
        "two.example",
    ]


def test_http_fetch_session_closes_evicted_and_remaining_clients_despite_errors(
    mocker: MockerFixture,
) -> None:
    """Bound pooled clients and isolate cleanup failures."""
    mocker.patch("datamodel_code_generator.http._HTTP_FETCH_CLIENT_CACHE_MAX_SIZE", 2)
    clients = [Mock() for _ in range(3)]
    clients[1].close.side_effect = RuntimeError("eviction close failed")
    clients[2].cookies.clear.side_effect = RuntimeError("cookie cleanup failed")
    httpx_module = Mock()
    httpx_module.Client.side_effect = clients
    http_stack = Mock(httpx=httpx_module)
    session = _HTTPFetchSession()

    for timeout in (1.0, 2.0, 1.0, 3.0):
        session.get_response(
            http_stack,
            "https://schema.example/schema.json",
            headers=None,
            verify=True,
            follow_redirects=False,
            query_parameters=None,
            timeout=timeout,
            pinned_host=None,
            pinned_ips=(),
        )

    assert len(session._clients) == 1
    clients[0].close.assert_not_called()
    clients[1].close.assert_called_once_with()
    clients[2].close.assert_called_once_with()
    clients[0].close.side_effect = RuntimeError("session close failed")

    session.close()

    for client in clients:
        client.close.assert_called_once_with()
    assert [client.cookies.clear.call_count for client in clients] == [2, 1, 1]
    assert not session._clients


def test_http_fetch_session_cookie_cleanup_does_not_remove_replacement_client() -> None:
    """Keep a replacement pool entry when stale-client cookie cleanup fails."""
    client = Mock()
    replacement_client = Mock()
    response = Mock()
    httpx_module = Mock()
    httpx_module.Client.return_value = client
    http_stack = Mock(httpx=httpx_module)
    session = _HTTPFetchSession()

    def replace_cached_client(*_args: object, **_kwargs: object) -> Mock:
        key = next(iter(session._clients))
        session._clients[key] = replacement_client
        return response

    client.get.side_effect = replace_cached_client
    client.cookies.clear.side_effect = RuntimeError("cookie cleanup failed")

    assert (
        session.get_response(
            http_stack,
            "https://schema.example/schema.json",
            headers=None,
            verify=True,
            follow_redirects=False,
            query_parameters=None,
            timeout=1.0,
            pinned_host=None,
            pinned_ips=(),
        )
        is response
    )
    assert list(session._clients.values()) == [replacement_client]
    client.close.assert_called_once_with()

    session.close()

    replacement_client.close.assert_called_once_with()


@pytest.mark.parametrize(
    ("pinned_host", "pinned_ips"),
    [
        ("localhost", (ip_address("127.0.0.1"),)),
        (None, ()),
    ],
    ids=["pinned", "trusted-private"],
)
def test_http_fetch_session_reuses_real_connection(
    mocker: MockerFixture,
    local_http_server: str,
    pinned_host: str | None,
    pinned_ips: tuple[IPv4Address | IPv6Address, ...],
) -> None:
    """Reuse one HTTP connection for distinct URLs on the same host."""
    mocker.stopall()

    with _HTTPFetchSession() as session:
        echo_response = session.get_response(
            _get_http_stack(),
            f"{local_http_server}/echo",
            headers=None,
            verify=True,
            follow_redirects=False,
            query_parameters=None,
            timeout=5.0,
            pinned_host=pinned_host,
            pinned_ips=pinned_ips,
        )
        schema_response = session.get_response(
            _get_http_stack(),
            f"{local_http_server}/schema.json",
            headers=None,
            verify=True,
            follow_redirects=False,
            query_parameters=None,
            timeout=5.0,
            pinned_host=pinned_host,
            pinned_ips=pinned_ips,
        )

    assert echo_response.status_code == 200
    assert schema_response.text == '{"type":"object"}'
    assert len(_SchemaHandler.client_connections) == 1


def test_http_fetch_session_does_not_replay_response_cookie_across_ports(
    mocker: MockerFixture,
    cross_port_cookie_redirect_server: str,
) -> None:
    """Do not turn response cookies into implicit headers on another origin."""
    mocker.stopall()

    with _HTTPFetchSession() as session:
        result = session.get_body(
            cross_port_cookie_redirect_server,
            timeout=5.0,
            allow_private_network=True,
        )

    assert json.loads(result) == {"cookie": None}
    assert _SchemaHandler.received_cookies == [None]


def test_parser_parse_closes_real_http_session(
    mocker: MockerFixture,
    local_http_server: str,
) -> None:
    """Close network resources for direct Parser.parse() users."""
    mocker.stopall()
    source = json.dumps({
        "title": "Root",
        "type": "object",
        "properties": {
            "child": {"$ref": f"{local_http_server}/schema.json"},
        },
    })
    parser = JsonSchemaParser(
        source,
        allow_remote_refs=True,
        allow_private_network=True,
    )

    result = parser.parse(format_=False)

    assert isinstance(result, str)
    assert "class Root" in result
    assert parser._http_fetch_session is None
    assert _SchemaHandler.connections_closed.wait(timeout=2.0)


def test_parser_http_cleanup_errors_do_not_mask_generation_or_skip_disposal() -> None:
    """Ignore HTTP cleanup errors while still returning output and disposing the graph."""
    parser = JsonSchemaParser(
        json.dumps({
            "title": "Model",
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }),
    )
    parse_session = Mock()
    parse_session.close.side_effect = RuntimeError("parse cleanup failed")
    parser._http_fetch_session = parse_session

    result = parser.parse(format_=False)

    assert isinstance(result, str)
    assert "class Model" in result
    parse_session.close.assert_called_once_with()
    assert parser._http_fetch_session is None
    assert parser.model_resolver.references

    dispose_session = Mock()
    dispose_session.close.side_effect = RuntimeError("dispose cleanup failed")
    parser._http_fetch_session = dispose_session
    parser._dispose()

    dispose_session.close.assert_called_once_with()
    assert parser._http_fetch_session is None
    assert not parser.model_resolver.references


def test_parser_http_cleanup_does_not_mask_parse_error(mocker: MockerFixture) -> None:
    """Keep the original parsing error when HTTP cleanup also fails."""
    parser = JsonSchemaParser("")
    session = Mock()
    session.close.side_effect = RuntimeError("cleanup failed")
    parser._http_fetch_session = session
    mocker.patch.object(parser, "parse_raw", side_effect=ValueError("parse failed"))

    with pytest.raises(ValueError, match="parse failed"):
        parser.parse(format_=False)

    session.close.assert_called_once_with()
    assert parser._http_fetch_session is None


def test_create_ssl_context_verify_modes() -> None:
    """Build an SSL context only when certificate verification is disabled."""
    assert _create_ssl_context(verify=True) is None

    context = _create_ssl_context(verify=False)

    assert context is not None
    assert context.check_hostname is False
    assert context.verify_mode == 0


@pytest.mark.parametrize(
    ("addr_info", "expected"),
    [
        (("short",), None),
        ((socket.AF_INET, socket.SOCK_STREAM, 0, "", ()), None),
        ((socket.AF_INET, socket.SOCK_STREAM, 0, "", ("not-an-ip", 0)), None),
        ((socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("2001:db8::1%eth0", 0, 0, 0)), ip_address("2001:db8::1")),
    ],
)
def test_get_addr_info_ip_handles_invalid_records(addr_info: object, expected: object) -> None:
    """Ignore malformed resolver records and strip IPv6 zone identifiers."""
    assert _get_addr_info_ip(addr_info) == expected


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        (None, None),
        (b"Example.COM.", "example.com"),
        (b"xn--bcher-kva.example.", "xn--bcher-kva.example"),
        (b"\xff", None),
        ("Example.COM.", "example.com"),
        ("bücher.example.", "xn--bcher-kva.example"),
        ("faß.example.", "xn--fa-hia.example"),
        ("xn--bcher-kva.example.", "xn--bcher-kva.example"),
    ],
)
def test_normalize_dns_host(host: bytes | str | None, expected: str | None) -> None:
    """Normalize DNS names before comparing pinned hosts."""
    assert _normalize_dns_host(host) == expected


class _FakeNetworkBackend:
    def __init__(self, *, fail_hosts: set[str] | None = None) -> None:
        self.calls: list[tuple[str, int]] = []
        self.fail_hosts = fail_hosts or set()
        self.unix_socket_calls: list[str] = []
        self.sleep_calls: list[float] = []

    def connect_tcp(self, host: str, port: int, **_kwargs: object) -> str:
        self.calls.append((host, port))
        if host in self.fail_hosts:
            raise OSError(host)
        return f"stream:{host}:{port}"

    def connect_unix_socket(self, path: str, **_kwargs: object) -> str:
        self.unix_socket_calls.append(path)
        return f"unix:{path}"

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)


def test_pinned_network_backend_connects_to_validated_ip() -> None:
    """Connect through the IP address that was validated before the HTTP request."""
    backend = _FakeNetworkBackend()
    pinned_backend = _PinnedNetworkBackend(
        pinned_host="metadata.example.com",
        pinned_ips=(ip_address("93.184.216.34"),),
        backend=backend,
    )

    result = pinned_backend.connect_tcp("metadata.example.com", 443)

    assert result == "stream:93.184.216.34:443"
    assert backend.calls == [("93.184.216.34", 443)]


def test_pinned_network_backend_rejects_mismatched_host() -> None:
    """Do not fall back to unvalidated DNS for a different connection host."""
    backend = _FakeNetworkBackend()
    pinned_backend = _PinnedNetworkBackend(
        pinned_host="metadata.example.com",
        pinned_ips=(ip_address("93.184.216.34"),),
        backend=backend,
    )

    with pytest.raises(OSError, match="does not match the validated host"):
        pinned_backend.connect_tcp("example.com", 443)

    assert backend.calls == []


def test_pinned_network_backend_rejects_idna_mismatch_without_dns_fallback() -> None:
    """Reject IDNA2003/IDNA2008 mismatches instead of resolving another host."""
    backend = _FakeNetworkBackend()
    pinned_backend = _PinnedNetworkBackend(
        pinned_host="faß.example",
        pinned_ips=(ip_address("93.184.216.34"),),
        backend=backend,
    )

    with pytest.raises(OSError, match="does not match the validated host"):
        pinned_backend.connect_tcp("fass.example", 443)

    assert backend.calls == []


def test_pinned_network_backend_matches_idn_punycode_host() -> None:
    """Pin IDN hosts even when the selected HTTP core connects with the punycode DNS name."""
    backend = _FakeNetworkBackend()
    pinned_backend = _PinnedNetworkBackend(
        pinned_host="bücher.example",
        pinned_ips=(ip_address("93.184.216.34"),),
        backend=backend,
    )

    result = pinned_backend.connect_tcp("xn--bcher-kva.example", 443)

    assert result == "stream:93.184.216.34:443"
    assert backend.calls == [("93.184.216.34", 443)]


def test_pinned_network_backend_tries_next_validated_ip() -> None:
    """Try the next validated DNS result when the first pinned address fails."""
    backend = _FakeNetworkBackend(fail_hosts={"93.184.216.34"})
    pinned_backend = _PinnedNetworkBackend(
        pinned_host="metadata.example.com",
        pinned_ips=(ip_address("93.184.216.34"), ip_address("93.184.216.35")),
        backend=backend,
    )

    result = pinned_backend.connect_tcp("metadata.example.com", 443)

    assert result == "stream:93.184.216.35:443"
    assert backend.calls == [("93.184.216.34", 443), ("93.184.216.35", 443)]


def test_pinned_network_backend_raises_last_connect_error() -> None:
    """Report the final connection error after trying all validated addresses."""
    backend = _FakeNetworkBackend(fail_hosts={"93.184.216.34", "93.184.216.35"})
    pinned_backend = _PinnedNetworkBackend(
        pinned_host="metadata.example.com",
        pinned_ips=(ip_address("93.184.216.34"), ip_address("93.184.216.35")),
        backend=backend,
    )

    with pytest.raises(OSError, match=r"93\.184\.216\.35"):
        pinned_backend.connect_tcp("metadata.example.com", 443)
    assert backend.calls == [("93.184.216.34", 443), ("93.184.216.35", 443)]


def test_pinned_network_backend_rejects_empty_pinned_ips() -> None:
    """Reject pinned connections when validation produced no usable IP address."""
    backend = _FakeNetworkBackend()
    pinned_backend = _PinnedNetworkBackend(
        pinned_host="metadata.example.com",
        pinned_ips=(),
        backend=backend,
    )

    with pytest.raises(OSError, match="No validated DNS addresses"):
        pinned_backend.connect_tcp("metadata.example.com", 443)
    assert backend.calls == []


def test_pinned_network_backend_delegates_unix_socket_and_sleep() -> None:
    """Forward backend methods that are unrelated to DNS pinning."""
    backend = _FakeNetworkBackend()
    pinned_backend = _PinnedNetworkBackend(
        pinned_host="metadata.example.com",
        pinned_ips=(ip_address("93.184.216.34"),),
        backend=backend,
    )

    assert pinned_backend.connect_unix_socket("/tmp/test.sock") == "unix:/tmp/test.sock"
    pinned_backend.sleep(0.1)
    assert backend.unix_socket_calls == ["/tmp/test.sock"]
    assert backend.sleep_calls == [0.1]


def test_get_body_blocks_redirect_to_unsafe_url(mocker: MockerFixture) -> None:
    """Validate redirect targets before the next request."""
    mocker.patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))],
    )
    mock_response = Mock()
    mock_response.status_code = 302
    mock_response.headers = {"location": "http://127.0.0.1/schema.json"}
    mock_fetch = mocker.patch("datamodel_code_generator.http._get_http_response", return_value=mock_response)

    with pytest.raises(SchemaFetchError, match="--allow-private-network"):
        get_body("https://example.com/schema.json")
    assert mock_fetch.call_count == 1


def test_get_body_allows_redirect_to_unsafe_url_with_explicit_opt_in(mocker: MockerFixture) -> None:
    """Allow trusted redirects to private network targets only when explicitly requested."""
    redirect_response = Mock()
    redirect_response.status_code = 302
    redirect_response.headers = {"location": "http://127.0.0.1/schema.json"}
    success_response = Mock()
    success_response.status_code = 200
    success_response.headers = {"content-type": "application/json"}
    success_response.content = b'{"type": "object"}'
    mock_get = mocker.patch.object(_get_httpx(), "get", side_effect=[redirect_response, success_response])

    result = get_body("https://example.com/schema.json", allow_private_network=True)

    assert result == '{"type": "object"}'
    assert [call.args[0] for call in mock_get.call_args_list] == [
        "https://example.com/schema.json",
        "http://127.0.0.1/schema.json",
    ]


def test_get_body_follows_relative_redirect(mocker: MockerFixture) -> None:
    """Follow safe relative redirects."""
    mocker.patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))],
    )
    redirect_response = Mock()
    redirect_response.status_code = 302
    redirect_response.headers = {"location": "schema.json"}
    success_response = Mock()
    success_response.status_code = 200
    success_response.headers = {"content-type": "application/json"}
    success_response.content = b'{"type": "object"}'
    mock_fetch = mocker.patch(
        "datamodel_code_generator.http._get_http_response",
        side_effect=[redirect_response, success_response],
    )

    result = get_body(
        "https://example.com/schemas/root.json",
        query_parameters=[("version", "v2")],
    )

    assert result == '{"type": "object"}'
    assert [call.args[1] for call in mock_fetch.call_args_list] == [
        "https://example.com/schemas/root.json",
        "https://example.com/schemas/schema.json",
    ]
    assert mock_fetch.call_args_list[0].kwargs["query_parameters"] == [("version", "v2")]
    assert mock_fetch.call_args_list[1].kwargs["query_parameters"] is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://schema.example/root.json", ("https", "schema.example", 443)),
        ("https://SCHEMA.EXAMPLE.:443/root.json", ("https", "schema.example", 443)),
        ("http://schema.example/root.json", ("http", "schema.example", 80)),
        ("https://bücher.example/root.json", ("https", "xn--bcher-kva.example", 443)),
        ("https:///root.json", None),
        ("https://schema.example:bad/root.json", None),
    ],
)
def test_get_url_origin_normalizes_redirect_scope(url: str, expected: tuple[str, str, int | None] | None) -> None:
    """Normalize scheme, host, and effective port for redirect credential scoping."""
    assert _get_url_origin(url) == expected


@pytest.mark.parametrize(
    ("current_url", "redirect_url", "expected_headers"),
    [
        (
            "https://schema.example/root.json",
            "https://schema.example/next.json",
            [
                ("authorization", "Bearer token"),
                ("COOKIE", "session=secret"),
                ("Proxy-Authorization", "Basic secret"),
                ("X-Trace", "1"),
            ],
        ),
        (
            "https://schema.example/root.json",
            "https://schema.example:443/next.json",
            [
                ("authorization", "Bearer token"),
                ("COOKIE", "session=secret"),
                ("Proxy-Authorization", "Basic secret"),
                ("X-Trace", "1"),
            ],
        ),
        (
            "https://schema.example./root.json",
            "https://SCHEMA.EXAMPLE/next.json",
            [
                ("authorization", "Bearer token"),
                ("COOKIE", "session=secret"),
                ("Proxy-Authorization", "Basic secret"),
                ("X-Trace", "1"),
            ],
        ),
        (
            "https://bücher.example/root.json",
            "https://xn--bcher-kva.example/next.json",
            [
                ("authorization", "Bearer token"),
                ("COOKIE", "session=secret"),
                ("Proxy-Authorization", "Basic secret"),
                ("X-Trace", "1"),
            ],
        ),
        ("https://schema.example/root.json", "http://schema.example/next.json", [("X-Trace", "1")]),
        ("https://schema.example/root.json", "https://other.example/next.json", [("X-Trace", "1")]),
        ("https://schema.example/root.json", "https://schema.example:444/next.json", [("X-Trace", "1")]),
        ("https://schema.example:bad/root.json", "https://other.example/next.json", [("X-Trace", "1")]),
        ("https://schema.example/root.json", "https://schema.example:bad/next.json", [("X-Trace", "1")]),
        ("https://schema.example:bad/root.json", "https://other.example:bad/next.json", [("X-Trace", "1")]),
    ],
)
def test_get_redirect_headers_scopes_sensitive_headers(
    current_url: str,
    redirect_url: str,
    expected_headers: list[tuple[str, str]],
) -> None:
    """Keep sensitive headers only when redirect origin is unchanged."""
    headers = [
        ("authorization", "Bearer token"),
        ("COOKIE", "session=secret"),
        ("Proxy-Authorization", "Basic secret"),
        ("X-Trace", "1"),
    ]

    assert _get_redirect_headers(headers, current_url, redirect_url) == expected_headers


def test_get_redirect_headers_handles_no_headers() -> None:
    """Preserve no-header inputs without creating a new header list."""
    assert _get_redirect_headers(None, "https://schema.example/root.json", "https://other.example/next.json") is None
    assert _get_redirect_headers([], "https://schema.example/root.json", "https://other.example/next.json") == []


def test_get_body_drops_sensitive_headers_on_cross_origin_redirect(mocker: MockerFixture) -> None:
    """Do not forward scoped credentials to a different redirect origin."""
    redirect_response = Mock()
    redirect_response.status_code = 302
    redirect_response.headers = {"location": "https://other.example/schema.json"}
    success_response = Mock()
    success_response.status_code = 200
    success_response.headers = {"content-type": "application/json"}
    success_response.content = b'{"type": "object"}'
    mock_get = mocker.patch.object(_get_httpx(), "get", side_effect=[redirect_response, success_response])

    result = get_body(
        "https://schema.example/root.json",
        headers=[
            ("Authorization", "Bearer token"),
            ("Cookie", "session=secret"),
            ("Proxy-Authorization", "Basic secret"),
            ("X-Trace", "1"),
        ],
        allow_private_network=True,
    )

    assert result == '{"type": "object"}'
    assert mock_get.call_args_list[0].kwargs["headers"] == [
        ("Authorization", "Bearer token"),
        ("Cookie", "session=secret"),
        ("Proxy-Authorization", "Basic secret"),
        ("X-Trace", "1"),
    ]
    assert mock_get.call_args_list[1].kwargs["headers"] == [("X-Trace", "1")]


def test_get_body_does_not_restore_sensitive_headers_after_cross_origin_redirect(mocker: MockerFixture) -> None:
    """Once sensitive headers are dropped on a redirect chain, later hops do not restore them."""
    same_origin_redirect = Mock()
    same_origin_redirect.status_code = 302
    same_origin_redirect.headers = {"location": "https://schema.example/step1.json"}
    cross_origin_redirect = Mock()
    cross_origin_redirect.status_code = 302
    cross_origin_redirect.headers = {"location": "https://other.example/step2.json"}
    return_redirect = Mock()
    return_redirect.status_code = 302
    return_redirect.headers = {"location": "https://schema.example/final.json"}
    success_response = Mock()
    success_response.status_code = 200
    success_response.headers = {"content-type": "application/json"}
    success_response.content = b'{"type": "object"}'
    mock_get = mocker.patch.object(
        _get_httpx(),
        "get",
        side_effect=[same_origin_redirect, cross_origin_redirect, return_redirect, success_response],
    )
    headers = [("Authorization", "Bearer token"), ("X-Trace", "1")]

    result = get_body("https://schema.example/root.json", headers=headers, allow_private_network=True)

    assert result == '{"type": "object"}'
    assert [call.kwargs["headers"] for call in mock_get.call_args_list] == [
        headers,
        headers,
        [("X-Trace", "1")],
        [("X-Trace", "1")],
    ]


def test_get_body_keeps_headers_on_same_origin_redirect(mocker: MockerFixture) -> None:
    """Keep headers when a redirect stays within the same origin."""
    redirect_response = Mock()
    redirect_response.status_code = 302
    redirect_response.headers = {"location": "https://schema.example/next.json"}
    success_response = Mock()
    success_response.status_code = 200
    success_response.headers = {"content-type": "application/json"}
    success_response.content = b'{"type": "object"}'
    headers = [("Authorization", "Bearer token")]
    mock_get = mocker.patch.object(_get_httpx(), "get", side_effect=[redirect_response, success_response])

    result = get_body("https://schema.example/root.json", headers=headers, allow_private_network=True)

    assert result == '{"type": "object"}'
    assert mock_get.call_args_list[1].kwargs["headers"] == headers


@pytest.mark.parametrize("url", ["ftp://example.com/schema.json", "https:///schema.json"])
def test_get_body_rejects_invalid_fetch_urls(mocker: MockerFixture, url: str) -> None:
    """Reject unsupported or incomplete URLs before fetching."""
    mock_get = mocker.patch.object(_get_httpx(), "get")

    with pytest.raises(SchemaFetchError, match="HTTP fetch"):
        get_body(url)
    assert mock_get.call_count == 0


def test_get_body_rejects_redirect_without_location(mocker: MockerFixture) -> None:
    """Reject redirect responses that do not provide a target."""
    mock_response = Mock()
    mock_response.status_code = 302
    mock_response.headers = {}
    mock_get = mocker.patch.object(_get_httpx(), "get", return_value=mock_response)

    with pytest.raises(SchemaFetchError, match="missing a Location header"):
        get_body("https://example.com/schema.json", allow_private_network=True)
    assert mock_get.call_count == 1


def test_get_body_rejects_too_many_redirects(mocker: MockerFixture) -> None:
    """Reject redirect chains that exceed the configured limit."""
    mock_response = Mock()
    mock_response.status_code = 302
    mock_response.headers = {"location": "https://example.com/schema.json"}
    mock_get = mocker.patch.object(_get_httpx(), "get", return_value=mock_response)

    with pytest.raises(SchemaFetchError, match="Too many redirects"):
        get_body("https://example.com/schema.json", allow_private_network=True)
    assert mock_get.call_count == MAX_HTTP_REDIRECTS + 1


def test_get_body_wraps_transport_error(mocker: MockerFixture) -> None:
    """Test that transport failures (DNS, timeout, etc.) are wrapped in SchemaFetchError."""
    httpx_module = _get_httpx()
    mocker.patch.object(httpx_module, "get", side_effect=httpx_module.ConnectError("DNS resolution failed"))

    with pytest.raises(SchemaFetchError, match="Failed to fetch"):
        get_body("https://nonexistent.example.com/schema.json", allow_private_network=True)
