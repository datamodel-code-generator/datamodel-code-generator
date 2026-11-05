"""Call the reference siblings server through its client when OpenAPI 3.0 reads a `$ref` as its target alone.

Under `--ignore-ref-siblings-by-schema-version` the models leave out the keywords beside a `$ref` where the version
ignores them, and so do client and server: `page` has no default on either side, while `limit` and `offset` keep the
default of `Limit`.
"""

from __future__ import annotations

from tests.api_generation.scenarios.cross_ref_siblings import DOCUMENT, calls, services

MODEL = {"schema_version": "3.0", "ignore_ref_siblings_by_schema_version": True}

__all__ = ["DOCUMENT", "MODEL", "calls", "services"]
