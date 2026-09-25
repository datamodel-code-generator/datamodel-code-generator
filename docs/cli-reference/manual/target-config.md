## `--target-config` {#target-config}

Read the selected target's settings from a flat TOML file (experimental). `--generate-server` requires it.

**Related:** [`--generate-server`](generate-server.md#generate-server),
[`--target-output`](target-output.md#target-output), [Target configuration](../../fastapi-server.md#target-configuration)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input openapi.yaml --input-file-type openapi \
      --openapi-scopes schemas api --output models.py \
      --generate-server fastapi --target-config fastapi.toml
    ```

    ```toml
    # fastapi.toml
    schema_version = 1
    package = "server"
    model_package = "models"
    output = "server"
    ```

The file holds `schema_version = 1` and the target's own settings only; model options stay on the command
line or in `pyproject.toml`. Relative paths are resolved against the file's directory. An unknown setting is
`E_CONFIG_UNKNOWN`, and a missing or invalid value is `E_CONFIG_VALUE`.
