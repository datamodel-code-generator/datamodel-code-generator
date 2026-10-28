# Generate from MCP Tool Schemas

Generate Python models from Model Context Protocol (MCP) tool schema profiles.

!!! warning "Experimental"
    MCP tool schema profile input support is experimental. The MCP specification and real-world tool schema shapes may
    evolve, so generated behavior may change as compatibility is expanded.

## Quick Start

```bash
datamodel-codegen \
    --input tools-list.json \
    --input-file-type mcp-tools \
    --output-model-type pydantic_v2.BaseModel \
    --output model.py
```

## Supported Input Shapes

The `mcp-tools` input type accepts:

- a `tools/list` JSON-RPC response containing `result.tools`;
- an MCP server definition containing a top-level `tools` array;
- a single tool definition or an array of tool definitions;
- JSON Schema documents whose `$defs` or `definitions` values are tool definitions.

Each tool must define `name` and `inputSchema`. If `outputSchema` is present, it is generated too.

## Example

**tools-list.json**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "tools": [
      {
        "name": "search",
        "inputSchema": {
          "type": "object",
          "properties": {
            "query": { "type": "string" }
          },
          "required": ["query"]
        },
        "outputSchema": {
          "type": "object",
          "properties": {
            "results": {
              "type": "array",
              "items": { "type": "string" }
            }
          },
          "required": ["results"]
        }
      }
    ]
  }
}
```

**Generated model.py**
```python
from __future__ import annotations

from pydantic import BaseModel


class SearchInput(BaseModel):
    query: str


class SearchOutput(BaseModel):
    results: list[str]
```

## Naming

Tool names are converted to PascalCase and suffixed with `Input` or `Output`.

For example:

| Tool name | Generated input model | Generated output model |
|-----------|-----------------------|------------------------|
| `search` | `SearchInput` | `SearchOutput` |
| `create_issue` | `CreateIssueInput` | `CreateIssueOutput` |

## Notes

MCP `inputSchema` and `outputSchema` entries are converted into JSON Schema definitions before generation. Local
`$defs` and `definitions` entries inside a tool schema are hoisted and prefixed to avoid collisions between tools.

## Supported MCP Features

| Feature | Generation behavior |
|---------|---------------------|
| Tool `inputSchema` | Generated as `<ToolName>Input` |
| Tool `outputSchema` | Generated as `<ToolName>Output` when present |
| `tools/list` responses | Extracts `result.tools` |
| Server definitions | Extracts top-level `tools` arrays |
| Local `$defs` / `definitions` | Hoisted into generated JSON Schema definitions |

## Limitations

MCP tool schema input generates Python models from declared JSON Schemas. It does not connect to MCP servers, call
tools, validate tool runtime behavior, or infer schemas from tool implementations.

## See Also

- [Getting Started](getting-started.md)
- [MCP Tools specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
- [MCP Schema reference](https://modelcontextprotocol.io/specification/2025-06-18/schema)
- [Generate from JSON Schema](jsonschema.md)
