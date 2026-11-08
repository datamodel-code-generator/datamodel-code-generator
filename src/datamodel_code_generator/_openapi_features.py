"""The OpenAPI features whose use leaves an operation out of a target, as the feature table documents them.

The parser owns the feature metadata (`parser.schema_version.OpenAPISchemaFeatures`); targets consume accepted
contracts and not parser modules, so they read the features here.
"""

from __future__ import annotations

from datamodel_code_generator.parser.schema_version import ClientFeature, feature_label

__all__ = ["ClientFeature", "feature_label"]
