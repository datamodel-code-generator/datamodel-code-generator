"""Serve a document of the application's own, such as the source OpenAPI document, instead of FastAPI's."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI


def serve_openapi(app: FastAPI, load: Callable[[], dict[str, Any]], *, prefix: str = "") -> None:
    """Make the application's document the one `load` returns, loaded on its first request and kept.

    Its paths take the prefix the routes were included under, as in FastAPI's own document. FastAPI's documentation
    endpoints serve it as they serve FastAPI's own, the root path's server included.
    """

    def openapi() -> dict[str, Any]:
        if not app.openapi_schema:
            document = load()
            if prefix:
                document["paths"] = {f"{prefix}{path}": item for path, item in document.get("paths", {}).items()}
            app.openapi_schema = document
        return app.openapi_schema

    setattr(app, "openapi", openapi)  # noqa: B010 - FastAPI's documented way to replace the document.
