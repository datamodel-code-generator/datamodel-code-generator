"""Call the reference siblings server through its client when `--schema-version 3.0` reads the document as OpenAPI 3.0.

The models read the keywords beside a `$ref` in OpenAPI 3.0 too, and so do client and server: `page` takes the
default written beside its `$ref`, and `limit` and `offset` keep the default of `Limit`.
"""

from __future__ import annotations

from tests.api_generation.scenarios.cross_ref_siblings import DOCUMENT, calls, services

MODEL = {"schema_version": "3.0"}

__all__ = ["DOCUMENT", "MODEL", "calls", "services"]
