"""Real local HTTP request recording for schema-resource fixtures."""

from __future__ import annotations

from http.server import SimpleHTTPRequestHandler
from typing import Any


class RecordingRequestHandler(SimpleHTTPRequestHandler):
    """Serve fixture files and record each actual GET request."""

    def __init__(self, *args: Any, requests: list[str], **kwargs: Any) -> None:
        """Bind the request recorder before serving the connection."""
        self.requests = requests
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        """Record the requested path and serve the real fixture response."""
        self.requests.append(self.path)
        super().do_GET()

    def log_message(self, *_args: Any, **_kwargs: Any) -> None:
        """Keep CLI diagnostic output separate from HTTP access logging."""
