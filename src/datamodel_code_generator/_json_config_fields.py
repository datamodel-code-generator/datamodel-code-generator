"""The Config fields that hold JSON, kept free of pydantic for the callers that run before it loads."""

from __future__ import annotations

from typing import Literal, TypeAlias, get_args

JsonConfigFieldName: TypeAlias = Literal[
    "aliases",
    "base_class_map",
    "client_operations",
    "client_protocols",
    "client_resource_names",
    "custom_formatters_kwargs",
    "default_values",
    "duplicate_name_suffix",
    "enum_field_as_literal_map",
    "extra_template_data",
    "import_overrides",
    "model_name_map",
    "serialization_aliases",
    "server_body_modes",
    "server_handler_modes",
    "server_operation_names",
    "server_parameter_names",
    "server_primary_responses",
    "server_router_names",
    "type_overrides",
    "validators",
]
JSON_CONFIG_FIELDS: tuple[JsonConfigFieldName, ...] = get_args(JsonConfigFieldName)

__all__ = ["JSON_CONFIG_FIELDS", "JsonConfigFieldName"]
