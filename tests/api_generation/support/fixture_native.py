"""Independent local TLS, HTTP/2 and proxy peers for native generated-client conformance."""

from __future__ import annotations

import ssl
import threading
from socketserver import BaseRequestHandler, ThreadingTCPServer

import h2.config
import h2.connection
import h2.events
import trustme


class _Handler(BaseRequestHandler):
    server: NativeFixture

    def handle(self) -> None:
        stream = self.request
        try:
            if self.server.tls:
                stream = self.server.context.wrap_socket(stream, server_side=True, do_handshake_on_connect=False)
                stream.do_handshake()
            if self.server.http2:
                self._http2(stream)
            else:
                self._http1(stream)
        finally:
            stream.close()

    def _http2(self, stream: ssl.SSLSocket) -> None:
        connection = h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=False))
        connection.initiate_connection()
        stream.sendall(connection.data_to_send())
        self.server.protocols.append(stream.selected_alpn_protocol())
        pending: dict[int, tuple[tuple[tuple[bytes, bytes], ...], bytearray]] = {}
        while data := stream.recv(65536):
            for event in connection.receive_data(data):
                if isinstance(event, h2.events.RequestReceived):
                    pending[event.stream_id] = tuple(event.headers), bytearray()
                elif isinstance(event, h2.events.DataReceived):
                    pending[event.stream_id][1].extend(event.data)
                    connection.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                elif isinstance(event, h2.events.StreamEnded):
                    request_headers, body = pending.pop(event.stream_id)
                    fields = dict(request_headers)
                    self.server.requests.append((fields[b":method"], fields[b":path"], bytes(body)))
                    self.server.request_headers.append(request_headers)
                    headers = [
                        (b":status", str(self.server.status).encode()),
                        (b"content-type", self.server.content_type),
                    ]
                    if self.server.location is not None:
                        headers.append((b"location", self.server.location))
                    headers.extend(self.server.extra_headers)
                    connection.send_headers(event.stream_id, headers)
                    connection.send_data(event.stream_id, self.server.body, end_stream=True)
            outgoing = connection.data_to_send()
            if outgoing:
                stream.sendall(outgoing)

    def _http1(self, stream: ssl.SSLSocket) -> None:
        while True:
            data = b""
            while b"\r\n\r\n" not in data:
                received = stream.recv(65536)
                if not received:
                    return
                data += received
            head, body = data.split(b"\r\n\r\n", 1)
            request_line, *fields = head.split(b"\r\n")
            method, target, _ = request_line.split(b" ", 2)
            header_fields = tuple((name, value) for name, value in (field.split(b":", 1) for field in fields))
            headers = dict(header_fields)
            length = int(next((value for name, value in headers.items() if name.lower() == b"content-length"), b"0"))
            transfer = next((value for name, value in headers.items() if name.lower() == b"transfer-encoding"), b"")
            if transfer.strip().lower() == b"chunked":
                payload = bytearray()
                while True:
                    while b"\r\n" not in body:
                        received = stream.recv(65536)
                        if not received:
                            return
                        body += received
                    size, body = body.split(b"\r\n", 1)
                    length = int(size.split(b";", 1)[0], 16)
                    while len(body) < length + 2:
                        received = stream.recv(65536)
                        if not received:
                            return
                        body += received
                    if not length:
                        break
                    payload.extend(body[:length])
                    body = body[length + 2 :]
                body = bytes(payload)
            else:
                while len(body) < length:
                    received = stream.recv(length - len(body))
                    if not received:
                        return
                    body += received
            if method == b"CONNECT" and self.server.proxy:
                self.server.connects += 1
                if self.server.proxy == "tunnel":
                    stream.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    with self.server.context.wrap_socket(
                        stream, server_side=True, do_handshake_on_connect=False
                    ) as tunnel:
                        tunnel.do_handshake()
                        self._http1(tunnel)
                    return
            else:
                self.server.requests.append((method, target, body))
                self.server.request_headers.append(
                    tuple((name, value.removeprefix(b" ")) for name, value in header_fields)
                )
            if self.server.malformed:
                stream.sendall(b"NOT-HTTP\r\n\r\n")
                return
            response = (
                b"HTTP/1.1 "
                + str(self.server.status).encode()
                + b" Fixture\r\nContent-Length: "
                + str(len(self.server.body)).encode()
                + b"\r\nContent-Type: "
                + self.server.content_type
                + b"\r\n"
            )
            if self.server.location is not None:
                response += b"Location: " + self.server.location + b"\r\n"
            response += b"".join(name + b": " + value + b"\r\n" for name, value in self.server.extra_headers)
            stream.sendall(response + b"\r\n" + self.server.body)


class NativeFixture(ThreadingTCPServer):
    """Serve actual protocol frames, with mutable outcomes for sequential generated-client calls."""

    daemon_threads = True

    def __init__(
        self, *, http2: bool = False, proxy: str | None = None, malformed: bool = False, ipv6: bool = False
    ) -> None:
        authority = trustme.CA(organization_name="dcg native test", organization_unit_name="TLS CA environment")
        self.ca_pem = authority.cert_pem.bytes()
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.set_alpn_protocols(["h2"] if http2 else ["http/1.1"])
        authority.issue_cert("localhost", "127.0.0.1", "origin.test", "::1").configure_cert(self.context)
        self.verify = ssl.create_default_context()
        authority.configure_trust(self.verify)
        self.http2 = http2
        self.proxy = proxy
        self.ipv6 = ipv6
        self.malformed = malformed
        self.tls = proxy is None
        self.status = 200
        self.body = b"ready"
        self.content_type = b"text/plain"
        self.location: bytes | None = None
        self.extra_headers: tuple[tuple[bytes, bytes], ...] = ()
        self.connects = 0
        self.requests: list[tuple[bytes, bytes, bytes]] = []
        self.request_headers: list[tuple[tuple[bytes, bytes], ...]] = []
        self.protocols: list[str | None] = []
        super().__init__(("127.0.0.1", 0), _Handler)
        self.port = self.socket.getsockname()[1]
        self.url = f"https://127.0.0.1:{self.port}"
        self.proxy_url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.005}, daemon=True)
        self.thread.start()

    def handle_error(self, request: object, client_address: object) -> None:
        """Clients may abandon a malformed response or reject this fixture's private CA."""

    def stop(self) -> None:
        """Stop the listener and join its dispatcher."""
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=5)
