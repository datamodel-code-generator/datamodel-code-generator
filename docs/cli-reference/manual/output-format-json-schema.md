## `--output-format-json-schema` {#output-format-json-schema}

Output JSON Schema for a JSON output format and exit.

Use this when an LLM agent, tool call definition, or validation layer needs the
contract before consuming JSON output. The schema is emitted separately from the
JSON payload so tools can fetch the contract once and validate later command
output independently.

Currently supported schema targets:

- `generate-prompt`: schema for `--generate-prompt --output-format json`
- `generation`: schema for normal generation with `--output-format json`
- `model-metadata`: schema for files emitted by `--emit-model-metadata`
- `structured-output`: tagged union schema for all structured command outputs,
  discriminated by `kind`
- `config`: schema for JSON-valued configuration options

!!! tip "Usage"

    ```bash
    datamodel-codegen --output-format-json-schema generate-prompt # (1)!
    datamodel-codegen --output-format-json-schema generation # (2)!
    datamodel-codegen --output-format-json-schema model-metadata # (3)!
    datamodel-codegen --output-format-json-schema structured-output # (4)!
    datamodel-codegen --output-format-json-schema config # (5)!
    datamodel-codegen --generate-prompt --output-format json # (6)!
    datamodel-codegen --input schema.json --emit-model-metadata model-map.json # (7)!
    ```

    1. :material-arrow-left: Emit the JSON Schema for structured prompt output
    2. :material-arrow-left: Emit the JSON Schema for generated-file output
    3. :material-arrow-left: Emit the JSON Schema for generated model metadata
    4. :material-arrow-left: Emit the JSON Schema for all structured command outputs
    5. :material-arrow-left: Emit the JSON Schema for JSON-valued configuration options
    6. :material-arrow-left: Emit prompt payloads that match the prompt schema
    7. :material-arrow-left: Emit metadata payloads that match the model metadata schema
