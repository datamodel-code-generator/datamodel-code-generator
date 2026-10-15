# datamodel-code-generator

🚀 Generate Python data models from schema definitions in seconds.

<p>
  <a class="md-button md-button--primary" href="getting-started.md">Getting Started</a>
  <a class="md-button md-button--primary" href="playground.md">Open Playground</a>
  <a class="md-button md-button--primary" href="https://koxudaxi.dev/?utm_source=dm_docs&utm_medium=top&utm_campaign=open_to_work">Lead maintainer available for work</a>
</p>

[![PyPI version](https://img.shields.io/pypi/v/datamodel-code-generator.svg)](https://pypi.python.org/pypi/datamodel-code-generator)
[![Conda-forge](https://img.shields.io/conda/v/conda-forge/datamodel-code-generator)](https://anaconda.org/conda-forge/datamodel-code-generator)
[![Downloads](https://api.pepy.tech/badge/datamodel-code-generator/month)](https://pepy.tech/projects/datamodel-code-generator)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/datamodel-code-generator)](https://pypi.python.org/pypi/datamodel-code-generator)
[![codecov](https://codecov.io/gh/datamodel-code-generator/datamodel-code-generator/graph/badge.svg?token=plzSSFb9Li)](https://codecov.io/gh/datamodel-code-generator/datamodel-code-generator)
![license](https://img.shields.io/github/license/datamodel-code-generator/datamodel-code-generator.svg)
[![Pydantic v2](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/pydantic/pydantic/main/docs/badge/v2.json)](https://pydantic.dev)

---

## ✨ What it does

<!-- Source of truth: docs/assets/diagrams/hero.mmd — regenerate with `tox run -e diagrams` -->
![Schema files, raw data, and existing Python models flow through datamodel-code-generator into Python model output types](assets/diagrams/hero-light.svg#only-light){ align=center }
![Schema files, raw data, and existing Python models flow through datamodel-code-generator into Python model output types](assets/diagrams/hero-dark.svg#only-dark){ align=center }

Pick any one of the supported inputs and pick the Python model style you want as output.
`--input-model path/to/file.py:ClassName` can even retarget an existing Pydantic, dataclass, or TypedDict class defined
in another Python file to a different output type.

- 📄 Converts **OpenAPI 3**, **AsyncAPI**, **JSON Schema**, **Apache Avro**, **XML Schema**, **Protocol Buffers/gRPC**, **GraphQL**, **MCP tool schemas**, and raw data (JSON/YAML/CSV) into Python models
- 🐍 Generates from **existing Python types** (Pydantic, dataclass, TypedDict) via `--input-model`
- 🎯 Generates **Pydantic v2**, **Pydantic v2 dataclass**, **dataclasses**, **TypedDict**, or **msgspec** output
- 🔗 Handles complex schemas: `$ref`, `allOf`, `oneOf`, `anyOf`, enums, and nested types
- ✅ Produces type-safe, validated code ready for your IDE and type checker

---

## 🧪 Try It In Your Browser

Generate models in your browser without installing anything.

!!! note "Playground privacy"
    Generation runs locally in your browser with Pyodide. Your schema and options are not sent to a backend. Shared
    repro URLs encode them in the URL fragment (`#state=...`), which browsers do not send to the server; the full URL
    can still be stored in your browser history or wherever you share it.

---

## 🚀 Start Here

Install the CLI and generate your first model from [Getting Started](getting-started.md).

!!! note "Default output model"
    When `--output-model-type` is omitted, datamodel-code-generator generates Pydantic v2 BaseModel output
    (`pydantic_v2.BaseModel`). Use `--output-model-type` explicitly when you want dataclasses, TypedDict, or msgspec
    output.

---

## 📥 Choose Your Input

| Input Type | File Types | Example |
|------------|------------|---------|
| 📘 [OpenAPI 3.0/3.1/3.2](openapi.md) | `.yaml`, `.json` | API specifications |
| 📡 [AsyncAPI](asyncapi.md) | `.yaml`, `.json` | Event-driven API specifications |
| 📋 [JSON Schema](jsonschema.md) | `.json`, `.yaml` | Data validation schemas |
| 🪶 [Apache Avro](avro.md) | `.avsc`, `.json` | Avro schemas |
| 🧾 [XML Schema](xmlschema.md) | `.xsd` | XML document schemas |
| 🧩 [Protocol Buffers / gRPC](protobuf.md) | `.proto` | Protobuf messages and service schemas |
| 🔷 [GraphQL](graphql.md) | `.graphql` | GraphQL type definitions |
| 🛠️ [MCP Tool Schemas](mcp-tools.md) | `.json`, `.yaml` | MCP tool input/output schemas |
| 📊 [JSON/YAML/CSV Data](jsondata.md) | `.json`, `.yaml`, `.csv` | Infer schema from data |
| 🐍 [Python Models](python-model.md) | `.py` | Pydantic, dataclass, TypedDict |

---

## ✅ Conformance Signals

CI exercises datamodel-code-generator against pinned external corpora for XML Schema, JSON Schema, AsyncAPI, Apache
Avro, and Protocol Buffers. See the [Conformance Dashboard](conformance.md) for the generated summary of runner scripts,
tox environments, CI jobs, expected corpus counts, and upstream sources.

---

## 📤 Choose Your Output

```bash
# 🆕 Pydantic v2 (recommended for new projects)
datamodel-codegen --output-model-type pydantic_v2.BaseModel ...

# 🏗️ Python dataclasses
datamodel-codegen --output-model-type dataclasses.dataclass ...

# 📝 TypedDict (for type hints without validation)
datamodel-codegen --output-model-type typing.TypedDict ...

# ⚡ msgspec (high-performance serialization)
datamodel-codegen --output-model-type msgspec.Struct ...
```

See [Supported Data Types](supported-data-types.md) for the full list.

---

## 🌐 Generate a FastAPI server or an HTTP client (experimental)

!!! warning "Experimental"
    Server and client generation are experimental since 0.84.0: their options and generated packages may
    change. See [Experimental Features](experimental.md).

From one OpenAPI document, `--generate-server fastapi` writes a FastAPI server package and `--generate-client httpx2`
an HTTPX2 client package, in the same run as the models. The models stay the runtime contract: routes and client
methods take and return the generated models, so the server and the client parse and produce the same classes you already
use. Every generated file is regenerated on each run; keep your own code outside the generated packages.
The commands put everything in one application package, `app`: the generated `app/models.py`, `app/server/`, and
`app/client/`, and your `app/services/`, so no generic top-level name such as `models` or `server` is taken.

This example is the [FastAPI server tutorial](fastapi-server.md#quick-start), and the tests of this project run it as shown.

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: index.server-client.openapi -->
<details>
<summary>Input (<code>openapi.yaml</code>)</summary>

<!-- fmt: off -->

```yaml
openapi: 3.1.0
info:
  title: Pets
  version: "1.0"
servers:
  - url: http://localhost:8000
paths:
  /pets:
    get:
      operationId: listPets
      tags: [pets]
      parameters:
        - name: limit
          in: query
          schema: {type: integer, minimum: 1, maximum: 100, default: 20}
      responses:
        "200":
          description: The pets.
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Pets"}
    post:
      operationId: createPet
      tags: [pets]
      requestBody:
        required: true
        content:
          application/json:
            schema: {$ref: "#/components/schemas/NewPet"}
      responses:
        "201":
          description: Created.
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Pet"}
  /pets/{petId}:
    get:
      operationId: getPet
      tags: [pets]
      parameters:
        - name: petId
          in: path
          required: true
          schema: {type: integer}
      responses:
        "200":
          description: The pet.
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Pet"}
        "404":
          description: No pet has this ID.
          content:
            application/json:
              schema: {$ref: "#/components/schemas/Error"}
components:
  schemas:
    NewPet:
      type: object
      required: [name]
      properties:
        name: {type: string}
        tag: {type: string}
    Pet:
      type: object
      required: [id, name]
      properties:
        id: {type: integer}
        name: {type: string}
        tag: {type: string}
    Pets:
      type: array
      items: {$ref: "#/components/schemas/Pet"}
    Error:
      type: object
      required: [message]
      properties:
        message: {type: string}
```

<!-- fmt: on -->
</details>
<!-- END AUTO-GENERATED DOC EXAMPLE: index.server-client.openapi -->

**Commands**

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: index.server-client.commands -->
<!-- fmt: off -->

```bash
# The application package app, which the generated packages go into
mkdir -p app && touch app/__init__.py

# FastAPI server package in app/server/
datamodel-codegen \
  --input openapi.yaml \
  --input-file-type openapi \
  --openapi-scopes schemas api \
  --output-model-type pydantic_v2.BaseModel \
  --preset standard-py312-20260909 \
  --output app/models.py \
  --generate-server fastapi \
  --server-output app/server \
  --server-package app.server \
  --server-model-package app.models

# HTTPX2 client package in app/client/
datamodel-codegen \
  --input openapi.yaml \
  --input-file-type openapi \
  --openapi-scopes schemas api \
  --output-model-type pydantic_v2.BaseModel \
  --preset standard-py312-20260909 \
  --output app/models.py \
  --generate-client httpx2 \
  --client-output app/client \
  --client-package app.client \
  --client-model-package app.models
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: index.server-client.commands -->

`--openapi-scopes` must include `api`, and both need Python 3.11 or later, to run and as the target version (the preset
sets 3.12); left unset, the run sets them and says how. Each run ends by printing the runtime dependencies of its
package to stderr:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: index.server-client.notice -->
<!-- fmt: off -->

```text
Add the runtime dependencies of app.server to your project:
  uv add "fastapi>=0.141.1" "pydantic>=2.14.0" "typing-extensions>=4.16"
Next steps: implement the stubs in app/services/pets.py, then run the server:
  uv add uvicorn && uv run uvicorn app.server.main:app --reload
or: uv add "fastapi[standard]" && uv run fastapi dev --entrypoint app.server.main:app
See app/server/README.md.
Add the runtime dependencies of app.client to your project:
  uv add "httpx2>=2.13.0" "typing-extensions>=4.16" "pydantic>=2.14.0"
Next steps: call the API with the client:
  from app.client import Client

  with Client(base_url=...) as client:
      client.pets.list_pets(...)
See app/client/README.md.
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: index.server-client.notice -->

**Generated files (trimmed)**

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: index.server-client.tree -->
<!-- fmt: off -->

```text
app/
├── __init__.py
├── models.py
├── server/
├── services/
└── client/
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: index.server-client.tree -->

**Implement the server** in `app/services/pets.py`, which generation wrote once with stubs, by replacing the stubs of the
`Pets` class that subclasses the generated service Protocol; type checkers report a missing method or a mismatched
signature:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: index.server-client.app -->
<!-- fmt: off -->

```python
from app import models
from app.server import HTTPResult
from app.server.services import PetsService


class Pets(PetsService):
    def __init__(self) -> None:
        self.pets: dict[int, models.Pet] = {}

    def list_pets(self, *, limit: int) -> models.Pets:
        return models.Pets(list(self.pets.values())[:limit])

    def create_pet(self, *, body: models.NewPet) -> models.Pet:
        pet = models.Pet(id=len(self.pets) + 1, name=body.name, tag=body.tag)
        self.pets[pet.id] = pet
        return pet

    def get_pet(self, *, pet_id: int) -> models.Pet | HTTPResult[models.Error]:
        if (pet := self.pets.get(pet_id)) is None:
            return HTTPResult(404, models.Error(message=f"Pet {pet_id} not found"))
        return pet
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: index.server-client.app -->

```bash
uv add uvicorn
uv run uvicorn app.server.main:app
```

**Call it with the client** in `call.py`, which sends to the document's server URL by default:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: index.server-client.call -->
<!-- fmt: off -->

```python
from app.client import Client
from app.client.errors import NotFoundError
from app.models import NewPet

with Client() as client:
    pet = client.pets.create_pet(body=NewPet(name="Mimi", tag="cat"))
    print(pet)
    for listed in client.pets.list_pets(limit=10).root:
        print(listed.name)
    try:
        client.pets.get_pet(pet_id=99)
    except NotFoundError as error:
        print(error.status_code, error.body)
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: index.server-client.call -->

It prints:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: index.server-client.output -->
<!-- fmt: off -->

```text
id=1 name='Mimi' tag='cat'
Mimi
404 message='Pet 99 not found'
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: index.server-client.output -->

`AsyncClient` offers the same methods as coroutines. See [FastAPI Server Generation](fastapi-server.md) and [Python Client Generation](python-client.md) for every option, the
`pyproject.toml` settings, authentication, and the Python API.

---

## 🍳 Common Recipes

<!-- BEGIN AUTO-GENERATED CLI RECIPE QUICK STARTS -->
### CLI option quick starts

Use these starting points when combining options; each option links to the generated CLI reference for details and examples.

- **Generate a local schema file:** Pin the input type and destination when the source extension is ambiguous or generated output needs a stable path. Options: [`--input`](cli-reference/base-options.md#input), [`--input-file-type`](cli-reference/base-options.md#input-file-type), [`--output`](cli-reference/base-options.md#output).
- **Target Pydantic v2 on modern Python:** Set the output model family and Python/Pydantic compatibility targets together. Options: [`--output-model-type`](cli-reference/model-customization.md#output-model-type), [`--target-python-version`](cli-reference/model-customization.md#target-python-version), [`--target-pydantic-version`](cli-reference/model-customization.md#target-pydantic-version).
- **Use modern Python annotations:** Target a recent Python version and prefer built-in collection and union syntax in generated types. Options: [`--target-python-version`](cli-reference/model-customization.md#target-python-version), [`--use-union-operator`](cli-reference/typing-customization.md#use-union-operator), [`--use-standard-collections`](cli-reference/typing-customization.md#use-standard-collections).
- **Normalize incoming field names:** Convert source names to Python identifiers while preserving explicit alias data for runtime IO. Options: [`--snake-case-field`](cli-reference/field-customization.md#snake-case-field), [`--original-field-name-delimiter`](cli-reference/field-customization.md#original-field-name-delimiter), [`--aliases`](cli-reference/field-customization.md#aliases).
- **Generate operation-focused models:** Limit OpenAPI output to operation shapes and name models from operation IDs and status codes. Options: [`--openapi-scopes`](cli-reference/openapi-only-options.md#openapi-scopes), [`--use-operation-id-as-name`](cli-reference/openapi-only-options.md#use-operation-id-as-name), [`--use-status-code-in-response-name`](cli-reference/openapi-only-options.md#use-status-code-in-response-name).
- **Resolve remote references deliberately:** Enable remote `$ref` loading and configure request metadata, timeouts, or local ref roots. Options: [`--allow-remote-refs`](cli-reference/general-options.md#allow-remote-refs), [`--http-headers`](cli-reference/general-options.md#http-headers), [`--http-timeout`](cli-reference/general-options.md#http-timeout), [`--http-local-ref-path`](cli-reference/general-options.md#http-local-ref-path).

See the [CLI Reference](cli-reference/index.md) for the full option list and category-specific recipes.
<!-- END AUTO-GENERATED CLI RECIPE QUICK STARTS -->

### 🤖 Get CLI Help from LLMs

Generate a prompt to ask LLMs about CLI options:

```bash
datamodel-codegen --generate-prompt "Best options for Pydantic v2?" | claude -p
```

See [LLM Integration](llm-integration.md) for more examples.

### 🌐 Generate from URL {#http-extra-option}

```bash
pip install 'datamodel-code-generator[http]'
datamodel-codegen --url https://example.com/api/openapi.yaml --output model.py
```

The `http` extra is the stable, non-deprecated backend. For the experimental
HTTPX2 alternative, pass `--http-backend httpx2`; see
[HTTP backend selection](faq.md#http-backend-selection).

### ⚙️ Use with pyproject.toml

```toml title="pyproject.toml"
[tool.datamodel-codegen]
input = "schema.yaml"
output = "src/models.py"
output-model-type = "pydantic_v2.BaseModel"
```

Then simply run:

```bash
datamodel-codegen
```

See [pyproject.toml Configuration](pyproject_toml.md) for more options.

### 🔄 CI/CD Integration

Validate generated models in your CI pipeline:

```yaml title=".github/workflows/validate-models.yml"
# Replace vX.Y.Z with a released action version.
- uses: datamodel-code-generator/datamodel-code-generator@vX.Y.Z
  with:
    input: schemas/api.yaml
    output: src/models/api.py
```

See [CI/CD Integration](ci-cd.md) for more options.

---

## 📚 Next Steps

- 🖥️ **[CLI Reference](cli-reference/index.md)** - All command-line options with examples
- 🧰 **[Presets](presets.md)** - Recommended immutable option bundles
- 🌐 **[FastAPI Server Generation](fastapi-server.md)** - Experimental FastAPI server package from OpenAPI
- 📡 **[Python Client Generation](python-client.md)** - Experimental HTTPX2 client package from OpenAPI
- ⚙️ **[pyproject.toml Configuration](pyproject_toml.md)** - Configure via pyproject.toml
- 🚀 **[One-liner Usage](oneliner.md)** - uvx, pipx, clipboard integration
- 🔄 **[CI/CD Integration](ci-cd.md)** - GitHub Actions and CI validation
- ✅ **[Conformance Dashboard](conformance.md)** - External corpus and CI coverage signals
- 📈 **[Performance Benchmarks](performance-benchmarks.md)** - Release benchmark tables and interactive charts
- 🎨 **[Custom Templates](custom_template.md)** - Customize generated code with Jinja2
- 🖌️ **[Code Formatting](formatting.md)** - Configure black, isort, and ruff
- ❓ **[FAQ](faq.md)** - Common questions and troubleshooting

---

## 💖 Sponsors

<table>
  <tr>
    <td valign="top" align="center">
      <a href="https://github.com/openai">
        <img src="https://avatars.githubusercontent.com/u/14957082?s=200&v=4" alt="OpenAI Logo" style="width: 100px;">
        <p>OpenAI</p>
      </a>
    </td>
  </tr>
</table>

---

## 🏢 Used by

These public examples are grouped by how each project uses datamodel-code-generator.

### Code generation and runtime integration

- [openai/codex](https://github.com/openai/codex) - *[Generate public Python SDK types from protocol schemas](https://github.com/openai/codex/blob/205d37a20f742b0bf8e191622bd07c43f567ea49/sdk/python/scripts/update_sdk_artifacts.py#L558-L590)*
- [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) - *[Generate MCP protocol models from vendored JSON Schemas](https://github.com/modelcontextprotocol/python-sdk/blob/main/scripts/gen_surface_types.py)*
- [typesafe-ai/typesafe-sdk-python](https://github.com/typesafe-ai/typesafe-sdk-python) - *[Generate official Jev Python SDK models from the TypeSafe OpenAPI schema](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_schemas/models.py#L1-L2)*
- [modular/modular](https://github.com/modular/modular) - *[Generate MAX Serve KServe schemas from OpenAPI with datamodel-codegen](https://github.com/modular/modular/blob/0735fa29762a5c53d65a0456d0b53eac1472180f/max/python/max/serve/schemas/README.md#L20-L33)*
- [apache/airflow](https://github.com/apache/airflow) - *[Generate OpenAPI datamodels for airflow-ctl and task-sdk via pyproject codegen config](https://github.com/apache/airflow/blob/f1ac27af8b53e7d3ca7ff710c4f4413599bd1535/airflow-ctl/pyproject.toml#L148-L172)*
- [PostHog/posthog](https://github.com/PostHog/posthog) - *[Generate Pydantic models from JSON Schema](https://github.com/PostHog/posthog/blob/master/bin/build-schema-python.sh#L5-L14)*
- [airbytehq/airbyte](https://github.com/airbytehq/airbyte) - *[Generate Python, Java/Kotlin, and Typescript protocol models](https://github.com/airbytehq/airbyte-protocol/tree/main/protocol-models/bin)*
- [open-metadata/OpenMetadata](https://github.com/open-metadata/OpenMetadata) - *[datamodel_generation.py](https://github.com/open-metadata/OpenMetadata/blob/main/scripts/datamodel_generation.py)*
- [topoteretes/cognee](https://github.com/topoteretes/cognee) - *[Runtime generation of graph data models from JSON Schema](https://github.com/topoteretes/cognee/blob/main/cognee/shared/graph_model_utils.py)*
- [e2b-dev/E2B](https://github.com/e2b-dev/E2B) - *[Generate MCP server TypedDict models via Makefile](https://github.com/e2b-dev/E2B/blob/main/packages/python-sdk/Makefile)*
- [DataDog/integrations-core](https://github.com/DataDog/integrations-core) - *[Config models](https://github.com/DataDog/integrations-core/blob/master/docs/developer/meta/config-models.md)*
- [open-telemetry/opentelemetry-python](https://github.com/open-telemetry/opentelemetry-python) - *[Generate SDK configuration dataclasses from JSON Schema](https://github.com/open-telemetry/opentelemetry-python/blob/main/tox.ini)*

### Development, testing, and evaluation

- [browser-use/browser-use](https://github.com/browser-use/browser-use) - *[Evaluation dependency](https://github.com/browser-use/browser-use/blob/de14b9aa31d167696a7ea7185d71876dbd7e6c94/pyproject.toml#L74-L79)*
- [vllm-project/vllm](https://github.com/vllm-project/vllm) - *[Test dependency for MiniCPM3 tests](https://github.com/vllm-project/vllm/blob/46f01a50acd6862806ed67b88176c96c2b161142/requirements/test/cuda.in#L40)*
- [stanfordnlp/dspy](https://github.com/stanfordnlp/dspy) - *[Generate Pydantic models from JSON Schema for reliability tests](https://github.com/stanfordnlp/dspy/blob/main/tests/reliability/generate/utils.py)*

[See all dependents →](https://github.com/datamodel-code-generator/datamodel-code-generator/network/dependents)

---

## 👥 Maintainers

- [Koudai Aono](https://koxudaxi.dev/?utm_source=dm_docs&utm_medium=maintainer_section&utm_campaign=open_to_work) ([@koxudaxi](https://github.com/koxudaxi)) - Lead maintainer
- [Bernát Gábor](https://github.com/gaborbernat) ([@gaborbernat](https://github.com/gaborbernat)) - Maintainer
- [Antonio Spadaro](https://github.com/ilovelinux) ([@ilovelinux](https://github.com/ilovelinux)) - Maintainer
