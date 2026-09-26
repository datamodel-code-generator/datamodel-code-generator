## `--generate-server` {#generate-server}

Generate a server package for the models from the settings `--target-config` names (experimental).
The only choice is `fastapi`.

!!! warning "Experimental"

    FastAPI server generation is experimental; its options, target configuration file,
    generated package, and diagnostics may change.

**Related:** [`--target-config`](target-config.md#target-config), [`--target-output`](target-output.md#target-output),
[`--diagnostics-json`](diagnostics-json.md#diagnostics-json),
[`--dependency-format`](dependency-format.md#dependency-format), [FastAPI Server](../../fastapi-server.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input openapi.yaml --input-file-type openapi \
      --openapi-scopes schemas api --output models.py \
      --generate-server fastapi --target-config fastapi.toml # (1)!
    datamodel-codegen --input openapi.yaml --input-file-type openapi \
      --openapi-scopes schemas api --output models.py \
      --generate-server fastapi --target-config fastapi.toml --check # (2)!
    ```

    1. :material-arrow-left: Generate the models at `--output` and the server package the target file describes
    2. :material-arrow-left: Report the files that would change, without writing them

One run generates the models with the usual model options and the server package from the same accepted
document, then publishes both together. `--check` exits with 1 when a file would change and with 2 for an
error. Diagnostics go to stderr, and the generated code is never printed; a generation ends by printing the
command that adds the package to your project, as [`--dependency-format`](dependency-format.md#dependency-format)
chooses.

`--generate-server` cannot be combined with `--watch`, `--diff-against`, `--input-model`,
`--output-format json`, `--job`, or `--all-jobs` (`E_CONFIG_CONFLICT`), nor with an option that only prints
information, such as `--generate-prompt` or `--list-experimental`.
