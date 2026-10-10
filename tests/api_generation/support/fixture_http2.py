"""A local HTTP/2 TLS server whose response headers precede a delayed body."""

from __future__ import annotations

import ssl
import threading
import time
from socketserver import BaseRequestHandler, ThreadingTCPServer

import h2.config
import h2.connection
import h2.events
import trustme


class _Handler(BaseRequestHandler):
    """Exchange real HTTP/2 frames, leaving each small response body below the initial flow-control window."""

    request: ssl.SSLSocket
    server: Http2Fixture

    def setup(self) -> None:
        """Finish the TLS handshake in this connection's thread, so a client abandoning it never stalls the server."""
        self.request.do_handshake()

    def handle(self) -> None:
        connection = h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=False))
        connection.initiate_connection()
        self.request.sendall(connection.data_to_send())
        self.server.protocols.append(self.request.selected_alpn_protocol())
        while data := self.request.recv(65536):
            for event in connection.receive_data(data):
                if isinstance(event, h2.events.RequestReceived):
                    self.server.requests += 1
                    connection.send_headers(
                        event.stream_id,
                        [(":status", "200"), ("content-type", "application/octet-stream")],
                    )
                    self.request.sendall(connection.data_to_send())
                    time.sleep(self.server.delay)
                    connection.send_data(event.stream_id, b"ready", end_stream=True)
            if outgoing := connection.data_to_send():
                self.request.sendall(outgoing)


class Http2Fixture(ThreadingTCPServer):
    """Serve delayed bodies on localhost over an independently trusted HTTP/2 TLS connection."""

    daemon_threads = True

    def __init__(self, delay: float) -> None:
        """Listen on localhost, delaying each body by the given seconds."""
        authority = trustme.CA()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_alpn_protocols(["h2"])
        authority.issue_cert("localhost").configure_cert(context)
        self.client_context = ssl.create_default_context()
        authority.configure_trust(self.client_context)
        self.delay = delay
        self.requests = 0
        self.protocols: list[str | None] = []
        super().__init__(("127.0.0.1", 0), _Handler)
        self.socket = context.wrap_socket(self.socket, server_side=True, do_handshake_on_connect=False)
        self.url = f"https://localhost:{self.socket.getsockname()[1]}/stream"
        self._thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.005}, daemon=True)
        self._thread.start()

    def handle_error(self, request: object, client_address: object) -> None:
        """Accept a client closing its socket before the server finishes its delayed body."""

    def stop(self) -> None:
        """Stop accepting connections and release the listening socket."""
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=5)
