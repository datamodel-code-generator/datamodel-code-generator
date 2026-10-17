# datamodel-code-generator

🚀 Generate Python data models from schema definitions in seconds.

📚 [Documentation](https://datamodel-code-generator.koxudaxi.dev/) ·
🧪 [Playground](https://datamodel-code-generator.koxudaxi.dev/playground/) ·
💼 [Lead maintainer available for work](https://koxudaxi.dev/?utm_source=github_readme&utm_medium=top&utm_campaign=open_to_work)

> [!NOTE]
> Playground privacy: Generation runs locally in your browser with Pyodide. Your schema and options are not sent to a
> backend. Shared repro URLs encode them in the URL fragment (`#state=...`), which browsers do not send to the server;
> the full URL can still be stored in your browser history or wherever you share it.

[![PyPI version](https://img.shields.io/pypi/v/datamodel-code-generator.svg)](https://pypi.python.org/pypi/datamodel-code-generator)
[![Conda-forge](https://img.shields.io/conda/v/conda-forge/datamodel-code-generator)](https://anaconda.org/conda-forge/datamodel-code-generator)
[![Downloads](https://api.pepy.tech/badge/datamodel-code-generator/month)](https://pepy.tech/projects/datamodel-code-generator)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/datamodel-code-generator)](https://pypi.python.org/pypi/datamodel-code-generator)
[![codecov](https://codecov.io/gh/datamodel-code-generator/datamodel-code-generator/graph/badge.svg?token=plzSSFb9Li)](https://codecov.io/gh/datamodel-code-generator/datamodel-code-generator)
![license](https://img.shields.io/github/license/datamodel-code-generator/datamodel-code-generator.svg)
[![Pydantic v2](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/pydantic/pydantic/main/docs/badge/v2.json)](https://pydantic.dev)

## ✨ What it does

<!-- Source of truth: docs/assets/diagrams/hero.mmd — regenerate with `tox run -e diagrams` -->
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/diagrams/hero-dark.svg">
    <img alt="Schema files, raw data, and existing Python models flow through datamodel-code-generator into Python model output types" src="docs/assets/diagrams/hero-light.svg" width="760">
  </picture>
</p>

Pick any one of the supported inputs and pick the Python model style you want as output.
`--input-model path/to/file.py:ClassName` can even retarget an existing Pydantic, dataclass, or TypedDict class defined
in another Python file to a different output type.

- 📄 Converts **OpenAPI 3**, **AsyncAPI**, **JSON Schema**, **Apache Avro**, **XML Schema**, **Protocol Buffers/gRPC**, **GraphQL**, **MCP tool schemas**, and raw data (JSON/YAML/CSV) into Python models
- 🐍 Generates from **existing Python types** (Pydantic, dataclass, TypedDict) via `--input-model`
- 🎯 Generates **Pydantic v2**, **Pydantic v2 dataclass**, **dataclasses**, **TypedDict**, or **msgspec** output
- 🔗 Handles complex schemas: `$ref`, `allOf`, `oneOf`, `anyOf`, enums, and nested types
- ✅ Produces type-safe, validated code ready for your IDE and type checker

---

## 📦 Installation

Recommended for standalone CLI use:

```bash
uv tool install datamodel-code-generator
```

Conda users can install from conda-forge:

```bash
conda install -c conda-forge datamodel-code-generator
```

For projects that should pin the generator version, add it as a development dependency instead:

```bash
uv add --dev datamodel-code-generator
```

> [!NOTE]
> Community-maintained distribution packages are also available from
> [Debian](https://packages.debian.org/search?keywords=datamodel-codegen&searchon=names&suite=all&section=all),
> [Ubuntu](https://packages.ubuntu.com/search?keywords=datamodel-codegen&searchon=names&suite=all&section=all),
> [nixpkgs](https://search.nixos.org/packages?query=datamodel-code-generator), and
> [openSUSE Tumbleweed](https://software.opensuse.org/package/python-datamodel-code-generator).
> Availability and versions vary by distribution.

<details>
<summary>Other installation methods</summary>

**pip:**
```bash
pip install datamodel-code-generator
```

**uv (run without adding to project):**
```bash
uv run --with datamodel-code-generator datamodel-codegen --help
```

**With stable HTTP support** (for resolving remote `$ref`):
```bash
pip install 'datamodel-code-generator[http]'
```

The `http` extra is supported and is not deprecated. To require the experimental
HTTPX2 backend instead, install `datamodel-code-generator[httpx2]` and pass
`--http-backend httpx2`. The experimental extra is not included in
`datamodel-code-generator[all]`. See
[HTTP backend selection](https://datamodel-code-generator.koxudaxi.dev/faq/#http-backend-selection)
for automatic and explicit selection behavior.

**With GraphQL support:**
```bash
pip install 'datamodel-code-generator[graphql]'
```

**With Protocol Buffers support:**
```bash
pip install 'datamodel-code-generator[protobuf]'
```

**Docker:**
```bash
docker pull koxudaxi/datamodel-code-generator
```

Published Docker images run as a non-root `appuser`. When writing generated files
to a bind-mounted directory, make sure the directory is writable by the container
user or pass an explicit Docker user, for example `--user "$(id -u):$(id -g)"`.

</details>

---

## 🏃 Quick Start

<!-- BEGIN AUTO-GENERATED PRESET QUICK START -->
**Command**

```bash
datamodel-codegen \
  --input schema.json \
  --input-file-type jsonschema \
  --output-model-type pydantic_v2.BaseModel \
  --preset standard-py312-20261017 \
  --output model.py
```

This quick start uses `standard-py312-20261017` as the modern Python 3.12 baseline.
Preset names include the target Python version: `py312` means Python 3.12.

See [CLI Reference](https://datamodel-code-generator.koxudaxi.dev/cli-reference/) for all options. See [Presets](https://datamodel-code-generator.koxudaxi.dev/presets/),
[`--preset`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/base-options/#preset), [`--input-file-type`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/base-options/#input-file-type), and
[`--output-model-type`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/model-customization/#output-model-type) for this command.

For more schema-aware output that preserves schema-authored names, reuses models, and embeds generated
documentation, use [`practical-py312-20261017`](https://datamodel-code-generator.koxudaxi.dev/presets/#practical-py312-20261017).

<details>
<summary>Input (<code>schema.json</code>)</summary>

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Pet",
  "type": "object",
  "required": ["name"],
  "properties": {
    "name": {
      "type": "string",
      "description": "The pet's name"
    },
    "species": {
      "type": "string",
      "enum": ["dog", "cat", "bird", "fish"],
      "default": "dog"
    },
    "age": {
      "type": "integer",
      "minimum": 0,
      "description": "Age in years"
    },
    "vaccinated": {
      "type": "boolean",
      "default": false
    }
  }
}
```

</details>

**Output (`model.py`)**

<!-- fmt: off -->

```python
# generated by datamodel-codegen:
#   filename:  schema.json

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class Species(StrEnum):
    dog = 'dog'
    cat = 'cat'
    bird = 'bird'
    fish = 'fish'


class Pet(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
    )
    name: Annotated[str, Field(description="The pet's name")]
    species: Species = Species.dog
    age: Annotated[int | None, Field(description='Age in years', ge=0)] = None
    vaccinated: bool = False
```

<!-- fmt: on -->

### Choose a formatter

Choose a formatter to match your project and generation priorities:

- **Projects using Ruff:** use `--formatters ruff-check ruff-format` to keep generated code consistent with the
  project's formatting and lint policy. Install it with `pip install 'datamodel-code-generator[ruff]'`.
- **No Ruff, Black, or isort, or generation speed is the priority:** use `--formatters builtin` to avoid running
  external formatters on standard generated model modules.
- **Projects using Black/isort:** keep `--formatters black isort` to preserve the project's formatting and existing
  generated output.

The current default remains Black/isort, which are still required dependencies. Omitting formatter options continues
normal generation. The future builtin default is intended to reduce required installation dependencies and version
constraints; Ruff will still be recommended for projects that use Ruff. Formatters are never selected automatically
based on installed packages or Ruff configuration. The new `[black]` and `[isort]` extras prepare for later
optional installation; their ranges and environment markers match the current required dependencies.
Selecting only a formatter preserves your other generation settings; a preset also supplies model-generation options.
Explicit formatter selection does not pin formatter versions or guarantee byte-for-byte output stability.

Custom templates can emit Python outside the standard generated model patterns covered by `builtin`, so
custom-template output is not exhaustively validated. If `--formatters builtin` produces invalid or poorly formatted
output with a custom template, please open an issue with a small reproducer. See
[Formatter Behavior](https://datamodel-code-generator.koxudaxi.dev/formatter-behavior/) for details.

See [Performance Benchmarks](https://datamodel-code-generator.koxudaxi.dev/performance-benchmarks/) for release benchmark data and interactive charts.
<!-- END AUTO-GENERATED PRESET QUICK START -->

---

## 🌐 Generate a FastAPI server or an HTTP client (experimental)

> [!WARNING]
> Server and client generation are experimental since 0.84.0: their options and generated packages may change.
> See [Experimental Features](https://datamodel-code-generator.koxudaxi.dev/experimental/).

From one OpenAPI document, `--generate-server fastapi` writes a FastAPI server package and `--generate-client httpx2`
an HTTPX2 client package, in the same run as the models. The models stay the runtime contract: routes and client
methods take and return the generated models, so the server and the client parse and produce the same classes you already
use. Every generated file is regenerated on each run; keep your own code outside the generated packages.
The commands put everything in one application package, `app`: the generated `app/models.py`, `app/server/`, and
`app/client/`, and your `app/services/`, so no generic top-level name such as `models` or `server` is taken.

This example is the [FastAPI server tutorial](https://datamodel-code-generator.koxudaxi.dev/fastapi-server/#quick-start), and the tests of this project run it as shown.

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: readme.server-client.openapi -->
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
<!-- END AUTO-GENERATED DOC EXAMPLE: readme.server-client.openapi -->

**Commands**

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: readme.server-client.commands -->
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
  --preset standard-py312-20261017 \
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
  --preset standard-py312-20261017 \
  --output app/models.py \
  --generate-client httpx2 \
  --client-output app/client \
  --client-package app.client \
  --client-model-package app.models
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: readme.server-client.commands -->

`--openapi-scopes` must include `api`, and both need Python 3.11 or later, to run and as the target version (the preset
sets 3.12); left unset, the run sets them and says how. Each run ends by printing the runtime dependencies of its
package to stderr:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: readme.server-client.notice -->
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
<!-- END AUTO-GENERATED DOC EXAMPLE: readme.server-client.notice -->

**Generated files (trimmed)**

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: readme.server-client.tree -->
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
<!-- END AUTO-GENERATED DOC EXAMPLE: readme.server-client.tree -->

**Implement the server** in `app/services/pets.py`, which generation wrote once with stubs, by replacing the stubs of the
`Pets` class that subclasses the generated service Protocol; type checkers report a missing method or a mismatched
signature:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: readme.server-client.app -->
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
<!-- END AUTO-GENERATED DOC EXAMPLE: readme.server-client.app -->

```bash
uv add uvicorn
uv run uvicorn app.server.main:app
```

**Call it with the client** in `call.py`, which sends to the document's server URL by default:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: readme.server-client.call -->
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
<!-- END AUTO-GENERATED DOC EXAMPLE: readme.server-client.call -->

It prints:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: readme.server-client.output -->
<!-- fmt: off -->

```text
id=1 name='Mimi' tag='cat'
Mimi
404 message='Pet 99 not found'
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: readme.server-client.output -->

`AsyncClient` offers the same methods as coroutines. See [FastAPI Server Generation](https://datamodel-code-generator.koxudaxi.dev/fastapi-server/) and [Python Client Generation](https://datamodel-code-generator.koxudaxi.dev/python-client/) for every option, the
`pyproject.toml` settings, authentication, and the Python API.

---

## 📖 Documentation

**👉 [Read the full documentation →](https://datamodel-code-generator.koxudaxi.dev/)**

- 🧰 [Presets](https://datamodel-code-generator.koxudaxi.dev/presets/) - Recommended option bundles for modern output
- 🚀 [Getting Started](https://datamodel-code-generator.koxudaxi.dev/getting-started/) - Installation and first model
- 🖥️ [CLI Reference](https://datamodel-code-generator.koxudaxi.dev/cli-reference/) - All command-line options
- 🌐 [FastAPI Server Generation](https://datamodel-code-generator.koxudaxi.dev/fastapi-server/) - Experimental FastAPI server package from OpenAPI
- 📡 [Python Client Generation](https://datamodel-code-generator.koxudaxi.dev/python-client/) - Experimental HTTPX2 client package from OpenAPI
- 🧪 [Playground](https://datamodel-code-generator.koxudaxi.dev/playground/) - Try generation in your browser
- ⚙️ [pyproject.toml](https://datamodel-code-generator.koxudaxi.dev/pyproject_toml/) - Configuration file
- 🔄 [CI/CD Integration](https://datamodel-code-generator.koxudaxi.dev/ci-cd/) - GitHub Actions, pre-commit hooks
- ✅ [Conformance Dashboard](https://datamodel-code-generator.koxudaxi.dev/conformance/) - External corpus coverage signals
- 📈 [Performance Benchmarks](https://datamodel-code-generator.koxudaxi.dev/performance-benchmarks/) - Release benchmark tables and interactive charts
- 🧭 [Architecture](https://datamodel-code-generator.koxudaxi.dev/architecture/) - Generation pipeline and synchronized component inventory
- 🚀 [One-liner Usage](https://datamodel-code-generator.koxudaxi.dev/oneliner/) - uvx, pipx, clipboard integration
- ❓ [FAQ](https://datamodel-code-generator.koxudaxi.dev/faq/) - Common questions

---

## 📥 Supported Input

<!-- BEGIN AUTO-GENERATED README SUPPORTED INPUT -->
- OpenAPI 3 (YAML/JSON)
- AsyncAPI (YAML/JSON)
- JSON Schema
- MCP tool schemas
- XML Schema (XSD)
- Protocol Buffers / gRPC (`.proto`)
- Apache Avro schema (AVSC)
- JSON data
- YAML data
- Python dictionary
- CSV data
- GraphQL schema
- Python types (Pydantic, dataclass, TypedDict) via `--input-model`
<!-- END AUTO-GENERATED README SUPPORTED INPUT -->

## 📤 Supported Output

<!-- BEGIN AUTO-GENERATED README SUPPORTED OUTPUT -->
- [pydantic v2](https://docs.pydantic.dev/) BaseModel
- [pydantic v2](https://docs.pydantic.dev/) dataclass
- [dataclasses](https://docs.python.org/3/library/dataclasses.html)
- [TypedDict](https://docs.python.org/3/library/typing.html#typing.TypedDict)
- [msgspec](https://github.com/jcrist/msgspec) Struct
<!-- END AUTO-GENERATED README SUPPORTED OUTPUT -->

## ✅ Conformance Signals

CI exercises datamodel-code-generator against pinned external corpora for XML Schema, JSON Schema, AsyncAPI, Apache
Avro, and Protocol Buffers. See the [Conformance Dashboard](https://datamodel-code-generator.koxudaxi.dev/conformance/)
for the generated summary of runner scripts, tox environments, CI jobs, expected corpus counts, and upstream sources.

---

## 🍳 Common Recipes

<!-- BEGIN AUTO-GENERATED CLI RECIPE QUICK STARTS -->
### CLI option quick starts

Use these starting points when combining options; each option links to the generated CLI reference for details and examples.

- **Generate a local schema file:** Pin the input type and destination when the source extension is ambiguous or generated output needs a stable path. Options: [`--input`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/base-options/#input), [`--input-file-type`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/base-options/#input-file-type), [`--output`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/base-options/#output).
- **Target Pydantic v2 on modern Python:** Set the output model family and Python/Pydantic compatibility targets together. Options: [`--output-model-type`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/model-customization/#output-model-type), [`--target-python-version`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/model-customization/#target-python-version), [`--target-pydantic-version`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/model-customization/#target-pydantic-version).
- **Use modern Python annotations:** Target a recent Python version and prefer built-in collection and union syntax in generated types. Options: [`--target-python-version`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/model-customization/#target-python-version), [`--use-union-operator`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/typing-customization/#use-union-operator), [`--use-standard-collections`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/typing-customization/#use-standard-collections).
- **Normalize incoming field names:** Convert source names to Python identifiers while preserving explicit alias data for runtime IO. Options: [`--snake-case-field`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/field-customization/#snake-case-field), [`--original-field-name-delimiter`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/field-customization/#original-field-name-delimiter), [`--aliases`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/field-customization/#aliases).
- **Generate operation-focused models:** Limit OpenAPI output to operation shapes and name models from operation IDs and status codes. Options: [`--openapi-scopes`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/openapi-only-options/#openapi-scopes), [`--use-operation-id-as-name`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/openapi-only-options/#use-operation-id-as-name), [`--use-status-code-in-response-name`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/openapi-only-options/#use-status-code-in-response-name).
- **Resolve remote references deliberately:** Enable remote `$ref` loading and configure request metadata, timeouts, or local ref roots. Options: [`--allow-remote-refs`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/general-options/#allow-remote-refs), [`--http-headers`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/general-options/#http-headers), [`--http-timeout`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/general-options/#http-timeout), [`--http-local-ref-path`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/general-options/#http-local-ref-path).

See the [CLI Reference](https://datamodel-code-generator.koxudaxi.dev/cli-reference/) for the full option list and category-specific recipes.
<!-- END AUTO-GENERATED CLI RECIPE QUICK STARTS -->

### 🤖 Get CLI Help from LLMs

Generate a prompt to ask LLMs about CLI options:

```bash
datamodel-codegen --generate-prompt "Best options for Pydantic v2?" | claude -p
```

See [LLM Integration](https://datamodel-code-generator.koxudaxi.dev/llm-integration/) for more examples.

### 🌐 Generate from URL

```bash
pip install 'datamodel-code-generator[http]'
datamodel-codegen --url https://example.com/api/openapi.yaml --output model.py
```

The `http` extra is the stable, non-deprecated backend. For the experimental
HTTPX2 alternative, pass `--http-backend httpx2`; see
[HTTP backend selection](https://datamodel-code-generator.koxudaxi.dev/faq/#http-backend-selection).

### ⚙️ Use with pyproject.toml

```toml
[tool.datamodel-codegen]
input = "schema.yaml"
output = "src/models.py"
output-model-type = "pydantic_v2.BaseModel"
```

Then simply run:

```bash
datamodel-codegen
```

See [pyproject.toml Configuration](https://datamodel-code-generator.koxudaxi.dev/pyproject_toml/) for more options.

### 🔄 CI/CD Integration

Validate generated models in your CI pipeline:

```yaml
# Replace vX.Y.Z with a released action version.
- uses: datamodel-code-generator/datamodel-code-generator@vX.Y.Z
  with:
    input: schemas/api.yaml
    output: src/models/api.py
```

See [CI/CD Integration](https://datamodel-code-generator.koxudaxi.dev/ci-cd/) for more options.

---

## Coding agent skill

This repository includes an experimental Agent Skill that teaches compatible coding agents to run `datamodel-codegen` when generating Python models from OpenAPI, AsyncAPI, JSON Schema, GraphQL, JSON/YAML/CSV sample data, MCP tool schemas, Protocol Buffers, XML Schema, Apache Avro, or existing Python model objects.

See [Coding Agent Skill](docs/coding-agent-skill.md) for detailed guidance and troubleshooting.

Install the bundled skill directly from the CLI:

```bash
# Codex, project-local
datamodel-codegen --install-skill codex

# Claude Code, project-local
datamodel-codegen --install-skill claude-code
```

For a personal install, add `--skill-scope user`. Existing skills are preserved
unless you explicitly add `--overwrite-skill`.

Check your agent's current documentation for exact search paths.

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

## 🏢 Projects that use datamodel-code-generator

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

## 🔗 Related Projects

- **[fastapi-code-generator](https://github.com/koxudaxi/fastapi-code-generator)** - Generate FastAPI app from OpenAPI. To generate a FastAPI server with this project, see `--generate-server fastapi` and the [migration guide](https://datamodel-code-generator.koxudaxi.dev/fastapi-code-generator-migration/)
- **[pydantic-pycharm-plugin](https://github.com/koxudaxi/pydantic-pycharm-plugin)** - PyCharm plugin for Pydantic

---

## 🤝 Contributing

See [Development & Contributing](https://datamodel-code-generator.koxudaxi.dev/development-contributing/) for how to get started!

---

## 👥 Maintainers

- [Koudai Aono](https://koxudaxi.dev/?utm_source=github_readme&utm_medium=maintainer_section&utm_campaign=open_to_work) ([@koxudaxi](https://github.com/koxudaxi)) - Lead maintainer
- [Bernát Gábor](https://github.com/gaborbernat) ([@gaborbernat](https://github.com/gaborbernat)) - Maintainer
- [Antonio Spadaro](https://github.com/ilovelinux) ([@ilovelinux](https://github.com/ilovelinux)) - Maintainer

---

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.
