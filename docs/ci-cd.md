<!-- related-cli-options: --check, --disable-timestamp, --formatters, --target-python-version, --profile -->

# CI/CD Integration

This guide covers how to use datamodel-code-generator in CI/CD pipelines and development workflows to ensure generated code stays in sync with schemas.

!!! note
    The package name is `datamodel-code-generator`, and the CLI command is `datamodel-codegen`.

---

## Official GitHub Action

The official GitHub Action provides a simple way to validate generated models in your CI pipeline.

### Basic Usage

```yaml
- uses: datamodel-code-generator/datamodel-code-generator@vX.Y.Z
  with:
    input: schema.yaml
    output: src/models.py
    input-file-type: openapi
    output-model-type: pydantic_v2.BaseModel
```

Replace `vX.Y.Z` with the release tag you want to pin.

By default, the action runs in **check mode** (`--check`), which validates that the existing output file matches what would be generated. If they differ, the action fails.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `input` | Yes | - | Input schema file or directory |
| `output` | Yes | - | Output file or directory |
| `input-file-type` | Yes | - | Input file type (`openapi`, `asyncapi`, `jsonschema`, `mcp-tools`, `xmlschema`, `graphql`, `protobuf`, `avro`, `json`, `yaml`, `csv`) |
| `output-model-type` | Yes | - | Output model type (`pydantic_v2.BaseModel`, `pydantic_v2.dataclass`, `dataclasses.dataclass`, `typing.TypedDict`, `msgspec.Struct`) |
| `check` | No | `true` | Validate that existing output is up to date (no generation) |
| `working-directory` | No | `.` | Working directory (where `pyproject.toml` is located) |
| `profile` | No | - | Named profile from `pyproject.toml` |
| `extra-args` | No | - | Additional CLI arguments |
| `version` | No | - | Specific version to install (defaults to action's tag version) |
| `extras` | No | - | Optional extras to install (comma-separated: `graphql`, `http`, `validation`, `ruff`, `all`) |

### Example: Validate on Pull Request

```yaml title=".github/workflows/validate-models.yml"
name: Validate Generated Models

on:
  pull_request:
    paths:
      - 'schemas/**'
      - 'src/models/**'

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: datamodel-code-generator/datamodel-code-generator@vX.Y.Z
        with:
          input: schemas/api.yaml
          output: src/models/api.py
          input-file-type: openapi
          output-model-type: pydantic_v2.BaseModel
```

### Example: Monorepo with Multiple Schemas

```yaml title=".github/workflows/validate-models.yml"
jobs:
  validate:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        include:
          - working-directory: packages/api
            input: schemas/openapi.yaml
            output: src/models.py
            input-file-type: openapi
          - working-directory: packages/admin
            input: schemas/schema.json
            output: src/models.py
            input-file-type: jsonschema
    steps:
      - uses: actions/checkout@v4

      - uses: datamodel-code-generator/datamodel-code-generator@vX.Y.Z
        with:
          input: ${{ matrix.input }}
          output: ${{ matrix.output }}
          input-file-type: ${{ matrix.input-file-type }}
          output-model-type: pydantic_v2.BaseModel
          working-directory: ${{ matrix.working-directory }}
```

### Example: Using Profiles

```yaml
- uses: datamodel-code-generator/datamodel-code-generator@vX.Y.Z
  with:
    input: schemas/api.yaml
    output: src/models.py
    input-file-type: openapi
    output-model-type: pydantic_v2.BaseModel
    profile: api
```

### Example: Generate Models (Instead of Validation)

Set `check: 'false'` to actually generate the models:

```yaml
- uses: datamodel-code-generator/datamodel-code-generator@vX.Y.Z
  with:
    input: schema.yaml
    output: src/models.py
    input-file-type: openapi
    output-model-type: pydantic_v2.BaseModel
    check: 'false'
```

### Example: GraphQL Schema

For GraphQL schemas, use the `extras` input to install the required dependency:

```yaml
- uses: datamodel-code-generator/datamodel-code-generator@vX.Y.Z
  with:
    input: schema.graphql
    output: src/models.py
    input-file-type: graphql
    output-model-type: pydantic_v2.BaseModel
    extras: 'graphql'
```

### Example: Apache Avro Schema

```yaml
- uses: datamodel-code-generator/datamodel-code-generator@vX.Y.Z
  with:
    input: schema.avsc
    output: src/models.py
    input-file-type: avro
    output-model-type: pydantic_v2.BaseModel
```

### Example: Multiple Extras

You can install multiple extras with comma-separated values:

```yaml
- uses: datamodel-code-generator/datamodel-code-generator@vX.Y.Z
  with:
    input: schema.yaml
    output: src/models.py
    input-file-type: openapi
    output-model-type: pydantic_v2.BaseModel
    extras: 'http,validation,ruff'
```

### Example: Additional CLI Options

Use `extra-args` for CLI options not covered by the inputs:

```yaml
- uses: datamodel-code-generator/datamodel-code-generator@vX.Y.Z
  with:
    input: schema.yaml
    output: src/models.py
    input-file-type: openapi
    output-model-type: pydantic_v2.BaseModel
    extra-args: '--snake-case-field --field-constraints'
```

!!! tip "Version Pinning"
    Always pin the action to a specific version tag (e.g., `@vX.Y.Z`) to ensure reproducible builds. The action installs the same version of `datamodel-code-generator` as the tag.

---

## The `--check` Flag

The `--check` flag verifies that generated code matches existing files without modifying them. If the output would differ, it exits with a non-zero status code.

```bash
datamodel-codegen --check
```

### Success (Exit code 0)

When generated code matches the existing file, the command exits silently with code 0:

```console
$ datamodel-codegen --check
$ echo $?
0
```

### Failure (Exit code 1)

When the schema has changed and the generated code would differ, a unified diff is shown and the command exits with code 1:

```console
$ datamodel-codegen --check
--- models.py
+++ models.py (expected)
@@ -12,3 +12,4 @@
     name: Optional[str] = None
     age: Optional[int] = None
     email: Optional[str] = None
+    active: Optional[bool] = None
$ echo $?
1
```

!!! tip "Best Practice: Use pyproject.toml"
    Instead of passing many CLI options, configure all settings in `pyproject.toml`. This keeps CI commands simple, ensures consistency between local development and CI, and makes configuration easier to maintain.

    ```toml title="pyproject.toml"
    [tool.datamodel-codegen]
    input = "schemas/api.yaml"
    output = "src/models/api.py"
    output-model-type = "pydantic_v2.BaseModel"
    disable-timestamp = true
    ```

    Then simply run:

    ```bash
    datamodel-codegen --check
    ```

    For projects with multiple schemas, use [named profiles](pyproject_toml.md#named-profiles) to organize configurations by purpose.

**Related:** [`--check`](cli-reference/general-options.md#check), [`--disable-timestamp`](cli-reference/template-customization.md#disable-timestamp), [pyproject.toml Configuration](pyproject_toml.md)

---

## GitHub Actions

### Basic Example

```yaml title=".github/workflows/ci.yml"
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  check-generated-code:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: "3.14"

      - name: Install dependencies
        run: pip install datamodel-code-generator

      - name: Verify generated models are up-to-date
        run: datamodel-codegen --check
```

### Using Profiles for Multiple Schemas

For projects with multiple schemas, use [named profiles](pyproject_toml.md#named-profiles) to organize configurations by purpose:

```toml title="pyproject.toml"
[tool.datamodel-codegen]
output-model-type = "pydantic_v2.BaseModel"
disable-timestamp = true

[tool.datamodel-codegen.profiles.api]
input = "schemas/openapi/api.yaml"
output = "src/models/api.py"

[tool.datamodel-codegen.profiles.events]
input = "schemas/jsonschema/events.json"
output = "src/models/events.py"
input-file-type = "jsonschema"
```

```yaml title=".github/workflows/ci.yml"
- name: Verify API models are up-to-date
  run: datamodel-codegen --profile api --check

- name: Verify event models are up-to-date
  run: datamodel-codegen --profile events --check
```

### Using uv

If your project uses [uv](https://github.com/astral-sh/uv), you can run the CLI via `uv run`. This example installs the tool ephemerally (no need to add it to your project dependencies):

```yaml title=".github/workflows/ci.yml"
- name: Install uv
  uses: astral-sh/setup-uv@v4

- name: Verify generated models are up-to-date
  run: uv run --with datamodel-code-generator datamodel-codegen --profile api --check
```

If you want the generator version pinned in your project lockfile, add it once with `uv add --dev datamodel-code-generator`
and run `uv run datamodel-codegen --profile api --check` in CI.

---

## Pre-commit Hook

You can use datamodel-code-generator as a [pre-commit](https://pre-commit.com/) hook to automatically check or regenerate models before commits.

!!! tip
    Pin `rev` to a released tag (e.g., `vX.Y.Z`) to keep generated output stable and reproducible across developer machines and CI.

### With pyproject.toml (Recommended)

Configure settings in `pyproject.toml` and use a simple pre-commit hook:

```yaml title=".pre-commit-config.yaml"
repos:
  - repo: https://github.com/datamodel-code-generator/datamodel-code-generator
    rev: vX.Y.Z
    hooks:
      - id: datamodel-code-generator
        args: [--check]
        files: ^schemas/
```

### With Profiles

For projects with multiple schemas using [named profiles](pyproject_toml.md#named-profiles):

```yaml title=".pre-commit-config.yaml"
repos:
  - repo: https://github.com/datamodel-code-generator/datamodel-code-generator
    rev: vX.Y.Z
    hooks:
      - id: datamodel-code-generator
        name: Check API models
        args: [--profile, api, --check]
        files: ^schemas/openapi/
      - id: datamodel-code-generator
        name: Check event models
        args: [--profile, events, --check]
        files: ^schemas/jsonschema/
```

### Auto-regenerate Mode

This configuration automatically regenerates models when schema files change:

```yaml title=".pre-commit-config.yaml"
repos:
  - repo: https://github.com/datamodel-code-generator/datamodel-code-generator
    rev: vX.Y.Z
    hooks:
      - id: datamodel-code-generator
        files: ^schemas/
```

!!! note "Installing the hook"
    Ensure `pre-commit` is installed, then install the hooks:

    ```bash
    pip install pre-commit
    pre-commit install
    ```

---

## GitLab CI

```yaml title=".gitlab-ci.yml"
check-generated-code:
  image: python:3.14
  script:
    - pip install datamodel-code-generator
    - datamodel-codegen --check
  rules:
    - changes:
        - schemas/**/*
        - src/models/**/*
```

---

## Makefile Integration

Add targets to your Makefile for easy generation and checking:

```makefile title="Makefile"
.PHONY: generate-models check-models

generate-models:
	datamodel-codegen

check-models:
	datamodel-codegen --check
```

Then use in CI:

```yaml title=".github/workflows/ci.yml"
- name: Check generated models
  run: make check-models
```

For projects with multiple profiles:

```makefile title="Makefile"
.PHONY: generate-all check-all

generate-all:
	datamodel-codegen --profile api
	datamodel-codegen --profile events

check-all:
	datamodel-codegen --profile api --check
	datamodel-codegen --profile events --check
```

---

## Troubleshooting

### Check fails due to formatting differences

Ensure you're using the same formatters in CI as locally. Configure formatters in `pyproject.toml`:

```toml title="pyproject.toml"
[tool.datamodel-codegen]
formatters = ["ruff"]
```

See [Formatting](formatting.md) for details.

**Related:** [`--formatters`](cli-reference/template-customization.md#formatters)

### Check fails due to timestamp

Always use `disable-timestamp = true` in `pyproject.toml`:

```toml title="pyproject.toml"
[tool.datamodel-codegen]
disable-timestamp = true
```

**Related:** [`--disable-timestamp`](cli-reference/template-customization.md#disable-timestamp)

### Different Python versions produce different output

Some type annotations differ between Python versions. Pin the target version in `pyproject.toml` and ensure CI uses the same Python version as development:

```toml title="pyproject.toml"
[tool.datamodel-codegen]
target-python-version = "3.14"
```

**Related:** [`--target-python-version`](cli-reference/model-customization.md#target-python-version)
