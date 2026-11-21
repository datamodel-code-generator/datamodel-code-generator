"""Upload raw binary content named by a content media type as bytes under `--use-bytes-for-content-media-type`.

The models type such a string `bytes`, as a `format: binary` string is, so the server takes every image the client
sends as a file, one that is not UTF-8 included, and reads it as bytes; a string with a content encoding stays text.
"""

from __future__ import annotations

from tests.api_generation.scenarios.cross_content_media_type import DOCUMENT, calls, services

MODEL = {"use_bytes_for_content_media_type": True}

__all__ = ("DOCUMENT", "MODEL", "calls", "services")
