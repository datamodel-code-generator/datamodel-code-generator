"""Call the reference siblings server through its client when `--schema-version 3.0` reads the document as OpenAPI 3.0.

OpenAPI 3.0 ignores the keywords beside a `$ref`, as the models do under that version: `page` has no default on
either side, while `limit` and `offset` keep the default of `Limit`.
"""

from __future__ import annotations

from tests.api_generation.scenarios.cross_ref_siblings import DOCUMENT, calls, services

MODEL = {"schema_version": "3.0"}

__all__ = ["DOCUMENT", "MODEL", "calls", "services"]
