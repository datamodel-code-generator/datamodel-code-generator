"""The OpenAPI features whose use leaves an operation out of a target, as the feature table documents them.

The parser owns the feature metadata (`parser.schema_version.OpenAPISchemaFeatures`); targets consume accepted
contracts and not parser modules, so they read the features here.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from datamodel_code_generator.parser.schema_version import feature_label

__all__ = ["ClientFeature", "feature_label"]

ClientFeature: TypeAlias = Literal[
    "http_auth_schemes",
    "mutual_tls",
    "cookie_style",
    "deep_object_style",
    "structured_parameter_values",
    "binary_in_text_media",
    "multipart_media",
    "part_content_type",
]
"""An OpenAPI feature whose use can leave an operation out of the client: a field of `OpenAPISchemaFeatures`."""
