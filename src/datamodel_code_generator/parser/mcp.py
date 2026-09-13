"""MCP tool schema profile support."""

from __future__ import annotations

import re
from collections import ChainMap
from collections.abc import Mapping, MutableMapping
from typing import Any, Literal, TypeAlias

from pydantic import ConfigDict, Field, StrictStr, ValidationError

from datamodel_code_generator import Error
from datamodel_code_generator._json_schema import (
    DEFINITION_KEYS,
    SCHEMA_MAP_KEYS,  # noqa: F401
    SCHEMA_VALUE_KEYS,  # noqa: F401
    DefinitionKey,  # noqa: F401
    _local_definition_ref_name,  # noqa: F401
    _rewrite_local_definition_ref,  # noqa: F401
    _rewrite_schema_refs,
)
from datamodel_code_generator.util import BaseModel

JSONSchemaDraft: TypeAlias = Literal["https://json-schema.org/draft/2020-12/schema"]
JSONSchemaMapping: TypeAlias = Mapping[str, Any]
OptionalStringToolKey: TypeAlias = Literal["title", "description"]
OptionalMappingToolKey: TypeAlias = Literal["outputSchema", "annotations", "_meta"]
SchemaKey: TypeAlias = Literal["inputSchema", "outputSchema"]

JSON_SCHEMA_DRAFT_2020_12: JSONSchemaDraft = "https://json-schema.org/draft/2020-12/schema"
OPTIONAL_STRING_TOOL_KEYS: tuple[OptionalStringToolKey, ...] = ("title", "description")
OPTIONAL_MAPPING_TOOL_KEYS: tuple[OptionalMappingToolKey, ...] = ("outputSchema", "annotations", "_meta")


class MCPTool(BaseModel):
    """Validated MCP tool definition."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    name: StrictStr
    input_schema: JSONSchemaMapping = Field(alias="inputSchema")
    output_schema: JSONSchemaMapping = Field(default_factory=dict, alias="outputSchema")
    title: StrictStr = ""
    description: StrictStr = ""
    annotations: JSONSchemaMapping = Field(default_factory=dict)
    meta: JSONSchemaMapping = Field(default_factory=dict, alias="_meta")

    @property
    def has_output_schema(self) -> bool:
        """Return whether outputSchema was present in the source tool."""
        return "output_schema" in self.model_fields_set


def _has_tool_schema_key(value: JSONSchemaMapping) -> bool:
    return "inputSchema" in value or "outputSchema" in value


def _tool_validation_error(value: JSONSchemaMapping, validation_error: ValidationError) -> str:
    first_error = validation_error.errors()[0]
    location = first_error["loc"]
    key = str(location[0]) if location else ""
    tool_name = value.get("name")
    if key == "name" or not isinstance(tool_name, str):
        return "MCP tool name must be a string"
    if key == "inputSchema" and first_error["type"] == "missing":
        return f"MCP tool {tool_name!r} is missing inputSchema"
    if key in OPTIONAL_MAPPING_TOOL_KEYS or key == "inputSchema":
        return f"MCP tool {tool_name!r} {key} must be a JSON Schema object"
    if key in OPTIONAL_STRING_TOOL_KEYS:
        return f"MCP tool {tool_name!r} {key} must be a string"
    return f"Invalid MCP tool {tool_name!r}: {first_error['msg']}"  # pragma: no cover


def _parse_tool(value: JSONSchemaMapping) -> MCPTool:
    try:
        return MCPTool.model_validate(value)
    except ValidationError as validation_error:
        raise Error(_tool_validation_error(value, validation_error)) from validation_error


def _coerce_tool(value: Any, fallback_name: str | None = None) -> MCPTool | None:
    if not isinstance(value, Mapping) or not _has_tool_schema_key(value):
        return None
    if fallback_name is not None:
        value = {"name": fallback_name, **value}
    return _parse_tool(value)


def _validate_tools(value: list[Any], context: str) -> list[MCPTool]:
    tools: list[MCPTool] = []
    for item in value:
        if tool := _coerce_tool(item):
            tools.append(tool)
            continue
        msg = f"Invalid MCP tools document: {context} contains a non-tool item"
        raise Error(msg)
    return tools


def _collect_tools_from_servers(value: Any) -> list[MCPTool]:
    match value:
        case Mapping():
            servers = value.values()
        case list():
            servers = value
        case _:
            return []

    tools: list[MCPTool] = []
    for server in servers:
        if isinstance(server, Mapping) and isinstance(server_tools := server.get("tools"), list):
            tools.extend(_validate_tools(server_tools, "server tools"))
    return tools


def _collect_tools_from_definitions(value: Any) -> list[MCPTool]:
    if not isinstance(value, Mapping):
        return []
    return [tool for name, schema in value.items() if (tool := _coerce_tool(schema, str(name)))]


def _extract_tools(data: Any) -> list[MCPTool]:
    match data:
        case Mapping() if tool := _coerce_tool(data):
            tools = [tool]
        case list():
            tools = _validate_tools(data, "top-level list")
        case Mapping() if isinstance(tools := data.get("tools"), list):
            tools = _validate_tools(tools, "tools")
        case Mapping() if isinstance(result := data.get("result"), Mapping):
            tools = _extract_tools(result)
        case Mapping():
            for key in ("mcpServers", "servers"):
                if tools := _collect_tools_from_servers(data.get(key)):
                    break
            for key in DEFINITION_KEYS:
                if tools:
                    break
                tools = _collect_tools_from_definitions(data.get(key))
            if not tools:
                tools = _collect_tools_from_definitions(data)
        case _:
            tools = []
    return tools


def _unique_definition_name(name: str, used_names: set[str]) -> str:
    base_name = _to_definition_name(name)
    if base_name not in used_names:
        used_names.add(base_name)
        return base_name

    index = 2
    while (candidate := f"{base_name}{index}") in used_names:
        index += 1
    used_names.add(candidate)
    return candidate


def _to_definition_name(name: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", name) if part]
    candidate = "".join(f"{part[:1].upper()}{part[1:]}" for part in parts) or "Model"
    if not candidate[:1].isalpha():
        return f"Model{candidate}"
    return candidate


def _normalize_schema(
    schema: JSONSchemaMapping,
    definition_name: str,
    used_names: set[str],
    references: set[str],
    parent_ref_map: MutableMapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root_definitions = [definitions for key in DEFINITION_KEYS if isinstance(definitions := schema.get(key), Mapping)]
    local_ref_map: dict[str, str] = {}
    for definitions in root_definitions:
        for name in definitions:
            local_ref_map[str(name)] = _unique_definition_name(
                f"{definition_name} {_to_definition_name(str(name))}",
                used_names,
            )
    ref_map: MutableMapping[str, str] = ChainMap(local_ref_map, parent_ref_map) if parent_ref_map else local_ref_map

    hoisted_definitions: dict[str, Any] = {}
    for definitions in root_definitions:
        for name, inner_schema in definitions.items():
            definition_key = str(name)
            match inner_schema:
                case Mapping():
                    normalized, nested = _normalize_schema(
                        inner_schema,
                        local_ref_map[definition_key],
                        used_names,
                        references,
                        ref_map,
                    )
                    hoisted_definitions.update(nested)
                    hoisted_definitions[local_ref_map[definition_key]] = normalized
                case bool():
                    hoisted_definitions[local_ref_map[definition_key]] = inner_schema

    normalized_schema = _rewrite_schema_refs(
        schema,
        ref_map,
        references,
        strip_root_definitions=True,
    )
    normalized_schema["title"] = definition_name
    return normalized_schema, hoisted_definitions


def _add_tool_schema_definition(
    definitions: dict[str, Any],
    used_names: set[str],
    tool: MCPTool,
    schema_key: SchemaKey,
    suffix: str,
) -> None:
    if schema_key == "outputSchema" and not tool.has_output_schema:
        return
    base_name = _to_definition_name(tool.name)
    definition_name = _unique_definition_name(f"{base_name} {suffix}", used_names)
    schema = tool.input_schema if schema_key == "inputSchema" else tool.output_schema
    references: set[str] = set()
    normalized, hoisted_definitions = _normalize_schema(schema, definition_name, used_names, references)
    # A later sibling definition can reference a boolean schema hoisted earlier.
    for name, value in hoisted_definitions.items():
        if isinstance(value, bool) and name not in references:
            continue
        definitions[name] = value
    definitions[definition_name] = normalized


def convert_mcp_tools_to_jsonschema(data: Any) -> dict[str, Any]:
    """Convert MCP tool definitions into a JSON Schema definitions document."""
    tools = _extract_tools(data)
    if not tools:
        msg = "Invalid MCP tools document: no tool definitions were found"
        raise Error(msg)

    definitions: dict[str, Any] = {}
    used_names: set[str] = set()
    for tool in tools:
        _add_tool_schema_definition(definitions, used_names, tool, "inputSchema", "Input")
        _add_tool_schema_definition(definitions, used_names, tool, "outputSchema", "Output")

    return {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "properties": {},
        "$defs": definitions,
    }
