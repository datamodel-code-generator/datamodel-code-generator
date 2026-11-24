---
name: datamodel-code-generator
description: >
  Use this skill when the user wants Python data models, Pydantic models,
  dataclasses, TypedDicts, msgspec structs, or type-safe Python classes
  generated from OpenAPI, AsyncAPI, JSON Schema, GraphQL, JSON/YAML/CSV
  sample data, MCP tool schemas, Protocol Buffers, XML Schema, Apache Avro,
  or existing Python model objects. It also generates, experimentally, a
  typed HTTP client (SDK) or a FastAPI server from OpenAPI and a WebSocket or
  server-sent-event client from AsyncAPI. Use it for requests such as
  "Pydantic models from this schema", "types for this API", "parse this JSON
  into classes", "a Python client for this OpenAPI spec", or "a FastAPI server
  from this spec". Prefer the datamodel-codegen CLI over hand-written model,
  client, or server code whenever an input schema, API spec, sample data file,
  or Python model object exists.
license: MIT
metadata:
  status: experimental
---

# datamodel-code-generator

## Why use this CLI

Use `datamodel-codegen` whenever a schema, API spec, sample data file, or existing Python model object exists.

The CLI handles refs, enums, nested models, unions, `allOf`, `oneOf`, `anyOf`, and model reuse better than hand-written code.

Do not hand-write generated models first.

## Availability check

Check one-shot execution first:

```bash
uvx --from datamodel-code-generator datamodel-codegen --help
```

If `uvx` is unavailable:

```bash
pipx run datamodel-code-generator --help
```

If the project already uses `uv` and project-scoped execution is preferred:

```bash
uv run --with datamodel-code-generator datamodel-codegen --help
```

If the project already has a managed Python virtual environment and the user approves project dependency changes:

```bash
python -m pip install datamodel-code-generator
datamodel-codegen --help
```

Prefer one-shot execution. Do not add or upgrade datamodel-code-generator in the user's project without explicit approval.

If offline and no installed command is available, say that the CLI cannot be run. Only then fall back to hand-written models.

## Option discovery for agents

Before choosing options, run the recommender (experimental) and let it write its result into `pyproject.toml`:

```bash
datamodel-codegen --input SCHEMA [--input-file-type TYPE] [--output-model-type TYPE] \
  --recommend-options --write-pyproject-config
```

It prints the keys it wrote and the command to generate with; run that command with `--output`, then `--check` in CI. If it exits with an error, it wrote nothing and printed the full report instead: apply its `pyproject.toml` block or command yourself. For the full report as data, run the same command without `--write-pyproject-config` and with `--output-format json`. Do not add options it lists under `not_recommended` without a stated reason.

When the user's goal needs judgment beyond the schema's shape, use the CLI's own LLM workflow and add `--recommend-options` to it, so the prompt includes the recommendation; decide from both:

```bash
datamodel-codegen [known options] --generate-prompt "Find the minimal options for this model-generation task." \
  --recommend-options
```

Include the options already known from the project or user request. The prompt output explains current options, available options, option relationships, and verification steps. Without an input schema, run `--generate-prompt` alone.

For agent tooling that consumes structured data, prefer JSON:

```bash
datamodel-codegen [known options] \
  --generate-prompt "Find the minimal options for this model-generation task." \
  --output-format json
```

When defining or validating tool contracts, fetch the schema separately:

```bash
datamodel-codegen --output-format-json-schema structured-output
```

Use `generate-prompt` when the consumer only handles prompt payloads, `generation` when it only handles generated-file payloads, and `structured-output` when it accepts multiple `kind` values.

## Input type decision table

| User input | Use |
| --- | --- |
| OpenAPI document | `--input-file-type openapi` |
| AsyncAPI document | `--input-file-type asyncapi` |
| JSON Schema document | `--input-file-type jsonschema` |
| GraphQL SDL | `--input-file-type graphql` |
| Raw JSON sample data | `--input-file-type json` |
| Raw YAML sample data | `--input-file-type yaml` |
| CSV sample data | `--input-file-type csv` |
| Apache Avro schema | `--input-file-type avro` |
| XML Schema | `--input-file-type xmlschema` |
| Protocol Buffers or gRPC schema | `--input-file-type protobuf` |
| MCP tool schema | `--input-file-type mcp-tools` |
| Existing Python model, dataclass, TypedDict, or dict schema object | `--input-model path/or/module.py:ObjectName` |

When a JSON or YAML file could be either a schema or data, inspect the content. `$schema`, `openapi`, `asyncapi`, `type`, `properties`, `components`, `$defs`, or `definitions` usually mean schema. Plain example values usually mean raw sample data.

For a Python dictionary containing JSON Schema or OpenAPI content, use `--input-model ./schemas.py:OBJECT_NAME` and add `--input-file-type jsonschema` or `--input-file-type openapi` when needed. Do not choose the `dict` input-file-type value.

## Output type decision table

| Project or user signal | Use |
| --- | --- |
| New project, API validation, general Python model generation | `--output-model-type pydantic_v2.BaseModel` |
| User wants Pydantic validation with dataclass syntax | `--output-model-type pydantic_v2.dataclass` |
| Project uses standard dataclasses and does not need runtime validation | `--output-model-type dataclasses.dataclass` |
| User wants dict-compatible static typing | `--output-model-type typing.TypedDict` |
| User wants high-performance serialization | `--output-model-type msgspec.Struct` |
| Project explicitly requires legacy Pydantic v1 | Verify current CLI support first. Current help does not list a Pydantic v1 output path. |

Before choosing the output type, inspect `pyproject.toml`, `requirements.txt`, `uv.lock`, `poetry.lock`, or installed dependencies when available. Prefer the project convention over the global default.

## Canonical command

```bash
uvx --from datamodel-code-generator datamodel-codegen \
  --input spec.yaml \
  --input-file-type openapi \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel \
  --preset PRESET
```

Replace `PRESET` with the preset the recommender prints, and add the options it recommends. A preset name carries its Python target, so do not add `--target-python-version` to this command yourself; run the `arguments` of the recommender's payload as printed (see "Target Python version").

For remote URLs or remote refs, use the HTTP extra:

```bash
uvx --from 'datamodel-code-generator[http]' datamodel-codegen \
  --url https://example.com/openapi.yaml \
  --input-file-type openapi \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel
```

For GraphQL, use the GraphQL extra when the one-shot command needs it:

```bash
uvx --from 'datamodel-code-generator[graphql]' datamodel-codegen \
  --input schema.graphql \
  --input-file-type graphql \
  --output models.py \
  --output-model-type pydantic_v2.BaseModel
```

## Target Python version

The recommender takes the target from `requires-python` in `pyproject.toml` when no option, key, or preset sets it, and the note in its payload names the source. Pass `--target-python-version X.Y` yourself only when the project declares its minimum Python elsewhere (`.python-version`, `runtime.txt`, CI config) or the user names one:

```bash
datamodel-codegen [options] --target-python-version X.Y --recommend-options --output-format json
```

A command with `--preset` takes its target from the preset name and keeps an explicit `--target-python-version` only when it matches.

## Clients and servers (experimental)

Use these when the user wants more than models from an API document. They are experimental: their options and the generated package may change.

| User wants | Use |
| --- | --- |
| A typed Python HTTP client (SDK) for an OpenAPI API | `--generate-client httpx2` with `--input-file-type openapi` |
| A Python client for the WebSocket or server-sent-event channels of an AsyncAPI document | `--generate-client httpx2` with `--input-file-type asyncapi` (`--client-asyncapi-perspective` says which side the document describes) |
| A FastAPI server for an OpenAPI API | `--generate-server fastapi` with `--input-file-type openapi` |

Each writes the models to `--output` and a package to `--client-output` or `--server-output`, with `--client-package`/`--server-package` and `--client-model-package`/`--server-model-package` naming their import paths. For OpenAPI add `--openapi-scopes schemas api`. Generation needs Python 3.11 or later and prints the `uv add` command for the package's runtime dependencies and the next steps. The recommender accepts `--generate-client` and `--generate-server` and starts from a preset the target can use. `references/workflows.md` has the full commands.

Boundary:

* Generated files are overwritten on every run, so never edit the models or the client or server package. Change the document or the options and regenerate; `--check` reports drift.
* A server is completed by the user's own code: their services implement the generated service Protocols in `services` modules beside the server package. Generation writes each missing services module once, with stubs, and never overwrites it.
* A client is used by the user's own code and tests, which import it; nothing in it is written by hand.
* If the user needs a server or client from a document type other than OpenAPI (and AsyncAPI for clients), say that it is not generated and offer models only.

## Verification step

Verification is mandatory.

For CI or agent integrations, prefer check mode when an output path exists:

```bash
datamodel-codegen [same options] --output models.py --check
```

If another tool must inspect generated content directly, use structured JSON output:

```bash
datamodel-codegen [same options] --output-format json
```

For single-file output:

```bash
python -c "import pathlib, importlib.util; p=pathlib.Path('models.py'); s=importlib.util.spec_from_file_location('generated_models', p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('import ok')"
```

For package output:

```bash
python -c "import models; print('import ok')"
```

Then run project checks when configured:

```bash
ruff check models.py
mypy models.py
pyright models.py
```

Use only the checks that the project already configures or asks for.

After verification, summarize the output path, input type, output model type, target Python version, generated class count if easy to determine, important class names, and warnings or limitations.

Do not dump the whole generated file into the conversation unless the user asks.

## What not to do

* Do not hand-write model files when a usable input schema or sample exists.
* Do not edit generated code by hand before trying the appropriate generator flags.
* Do not silently regenerate with different flags. Say what changed and why.
* Do not add datamodel-code-generator to a user's project dependencies without approval.
* Do not pin a version in user commands unless the repository maintainers decide to pin it.
* Do not claim support for non-Python output targets such as TypeScript.
* Do not use this CLI to fix an existing Pydantic model unless the task is to regenerate or convert models from a source input.

## References

Read `references/workflows.md` for commands by input type and scenario.

Read `references/cli-options.md` when choosing naming, optionality, formatting, splitting, or reuse flags.

Read `references/troubleshooting.md` when generation fails, imports fail, field names look wrong, optionality is surprising, or large specs are slow.
