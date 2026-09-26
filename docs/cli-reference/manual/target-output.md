## `--target-output` {#target-output}

Write the selected target to a directory instead of the `output` its target configuration file names
(experimental). `--output` keeps naming the model output.

**Related:** [`--generate-server`](generate-server.md#generate-server),
[`--target-config`](target-config.md#target-config)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input openapi.yaml --input-file-type openapi \
      --openapi-scopes schemas api --output src/example/models.py \
      --generate-server fastapi --target-config fastapi.toml \
      --target-output src/example/server
    ```

A relative `--target-output` is resolved against the current directory.
