## `--diagnostics-json` {#diagnostics-json}

Write the selected target's diagnostics as JSON to a file, or to stdout with `-` (experimental).

**Related:** [`--generate-server`](../target-generation-options.md#generate-server),
[`--generate-client`](../target-generation-options.md#generate-client),
[Diagnostics](../../fastapi-server.md#diagnostics)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input openapi.yaml --input-file-type openapi \
      --openapi-scopes schemas api --target-python-version 3.12 --output models.py \
      --generate-server fastapi --server-output server \
      --server-package server --server-model-package models \
      --diagnostics-json diagnostics.json
    ```

The document is written after every run that passes the command-line checks, successful or not, and lists
the diagnostic records in their original phase order. stderr prints warnings separately, then one combined
`Error:` message for a target failure, without the records' codes or stages. Ordinary model generation errors
receive code `E_GENERATION_FAILURE` and stage `target` in the document, with the original exception type and
message. A usage error, such as `--generate-server` without
`--server-output`, prints `Error:` like any other command-line error and writes no document.

```json
{
  "schema_version": 1,
  "target": "fastapi",
  "diagnostics": [
    {
      "code": "E_OPERATION_REF",
      "severity": "error",
      "stage": "config",
      "message": "The handler_modes entry '/paths/~1cats/get' selects no root path operation",
      "source_uri": null,
      "source_pointer": null,
      "operation": null,
      "option_path": "handler_modes",
      "artifact_path": null,
      "target_id": "3deca778a5f2d7af77744b6d85b16a8880b9708b45cff200725233911affaeba"
    }
  ]
}
```

`target` names the selected target: `fastapi` for `--generate-server` and `httpx2` for `--generate-client`.

A path the generation reads or writes, or one inside the model or target output, is refused with `Error:`,
and nothing is written to it; the check runs before anything is generated. An existing file is replaced only
when it has the exact shape of a diagnostics document for the same target. A directory, a path in a missing
directory, or a file that cannot be written is refused with `Error:`, and the command exits with 2.
