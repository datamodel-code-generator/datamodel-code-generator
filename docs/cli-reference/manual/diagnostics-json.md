## `--diagnostics-json` {#diagnostics-json}

Write the selected target's diagnostics as JSON to a file, or to stdout with `-` (experimental).

**Related:** [`--generate-server`](generate-server.md#generate-server),
[Diagnostics](../../fastapi-server.md#diagnostics)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input openapi.yaml --input-file-type openapi \
      --openapi-scopes schemas api --output models.py \
      --generate-server fastapi --target-config fastapi.toml \
      --diagnostics-json diagnostics.json
    ```

The document is written after every run, successful or not, and lists the diagnostics in the order stderr
shows them:

```json
{
  "schema_version": 1,
  "target": "fastapi",
  "diagnostics": [
    {
      "code": "E_CONFIG_UNKNOWN",
      "severity": "error",
      "stage": "config",
      "message": "The target file has no setting 'packages'",
      "source_uri": null,
      "source_pointer": null,
      "operation": null,
      "option_path": "packages",
      "artifact_path": null,
      "target_id": null
    }
  ]
}
```

A path the generation reads or writes, or one inside the model or target output, is `E_CONFIG_CONFLICT`,
and nothing is written to it.
