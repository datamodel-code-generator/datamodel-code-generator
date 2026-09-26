## `--dependency-format` {#dependency-format}

Choose what a generation prints to add the generated package to your project (experimental): `uv`, the
default, prints a `uv add` command, and `requirements` prints the lines of a requirements file.

**Related:** [`--generate-server`](generate-server.md#generate-server),
[FastAPI Server](../../fastapi-server.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input openapi.yaml --input-file-type openapi \
      --openapi-scopes schemas api --output models.py \
      --generate-server fastapi --target-config fastapi.toml \
      --dependency-format requirements > requirements.txt
    ```

In embedded mode, `requirements` prints one runtime dependency per line, each with the minimum version the
package needs:

```text
fastapi>=0.141.1
starlette>=1.0.0
pydantic>=2.13.5
jsonschema[format-nongpl]>=4.26
referencing>=0.37
typing-extensions>=4.16
```

In standalone mode it prints `-e` with the distribution's path, such as `-e ./service`; a path that needs
quoting is written as a `file:` URL, which pip and uv both read. Nothing is printed for `--check`, and the option
cannot be combined with `--diagnostics-json -`, which also writes to stdout (`E_CONFIG_CONFLICT`).
