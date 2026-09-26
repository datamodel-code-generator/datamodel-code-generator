# FastAPI Server Generation

!!! warning "Experimental"

    FastAPI server generation is experimental. Its options, target configuration file, generated package,
    template context, and served OpenAPI document may change. See [Experimental Features](experimental.md).

`--generate-server fastapi` generates the models of one OpenAPI document with the usual model options and, in
the same run, a FastAPI server package for its operations: routers, a service Protocol for each router group,
codecs for the values FastAPI cannot validate exactly, authentication, and the served OpenAPI document. Every
generated file is regenerated on each run. You write the business logic in your own modules, implementing the
service Protocols, and type checkers, Python, and the package at startup check that every operation has a
matching method.

## Quick start

Write a target configuration file:

```toml
# fastapi.toml
schema_version = 1
package = "server"
model_package = "models"
output = "server"
```

Generate the models and the server:

<!-- BEGIN AUTO-GENERATED FASTAPI QUICK START -->
```bash
datamodel-codegen \
  --input openapi.yaml \
  --input-file-type openapi \
  --openapi-scopes schemas api \
  --output-model-type pydantic_v2.BaseModel \
  --preset standard-py312-20260909 \
  --output models.py \
  --generate-server fastapi \
  --target-config fastapi.toml
```

The preset supplies the model options, as in the model [quick start](getting-started.md).
<!-- END AUTO-GENERATED FASTAPI QUICK START -->

`server/services.py` declares a Protocol for each router group, such as `PetsService` for the operations tagged
`pets`, with an abstract method for each operation that takes the operation's arguments as keywords. For a
document whose `pets` operations are `GET /pets/{petId}` (`getPet`) and `DELETE /pets/{petId}` (`deletePet`),
implement it in a module of your own, outside the generated package:

```python
# app.py
from models import Pet
from server import create_app
from server.services import PetsService


class Pets(PetsService):
    def get_pet(self, *, pet_id: int) -> Pet:
        return Pet(id=pet_id, name="Mimi")

    def delete_pet(self, *, pet_id: int) -> None:
        return None


app = create_app(pets=Pets())
```

Generation ends by printing the `uv add` command that adds the runtime dependencies of the package to your
project. It names only the minimum version each one needs, so uv adds the latest release and your lock file keeps
it. Run it, then start the application with an ASGI server:

```bash
uv add uvicorn
uv run uvicorn app:app
```

Because `Pets` subclasses `PetsService`, type checkers report a missing method or one whose arguments or result
do not match its operation, and Python refuses to create `Pets()` while a method is missing. `create_app` takes
one service for each group, under the group's name, and checks every method when the application starts; any
object with the right methods works, and type checkers check it where you pass it. The generated
`server/README.md` lists the operations of each service and shows how to connect an authorizer and your own
`FastAPI` application.

## Requirements

- The input is one OpenAPI 3.0, 3.1, or 3.2 document, from a file, a URL, or stdin; a directory or a list of
  files is `E_INPUT_ROOT`.
- `--output-model-type` is `pydantic_v2.BaseModel` or `pydantic_v2.dataclass`; the other backends are
  `E_FASTAPI_BACKEND_UNSUPPORTED`. Custom templates, base classes, types, and codec registrations do not add
  backends.
- `--openapi-scopes` includes `api`, so that the models cover the parameters, bodies, and responses of the
  operations.
- `--output` names the model file or package, and `model_package` is its import path.
- The generated package needs FastAPI 0.141 and the other requirements it lists.

## Python API

`datamodel_code_generator.fastapi` has the same entry points:

<!-- BEGIN AUTO-GENERATED FASTAPI PYTHON API -->
```python
from pathlib import Path

from datamodel_code_generator import DataModelType, GenerateConfig, InputFileType, OpenAPIScope
from datamodel_code_generator.fastapi import FastAPIConfig, generate_fastapi

report = generate_fastapi(
    Path("openapi.yaml"),
    model_config=GenerateConfig(
        output=Path("models.py"),
        input_file_type=InputFileType.OpenAPI,
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
        output_model_type=DataModelType.PydanticV2BaseModel,
        preset="standard-py312-20260909",
    ),
    config=FastAPIConfig(output=Path("server"), package="server", model_package="models"),
)
for record in report.written_files:
    print(record.path)
```
<!-- END AUTO-GENERATED FASTAPI PYTHON API -->

`render_fastapi` takes the same arguments and returns every file as a `GeneratedArtifact` without writing it.
Both results list the package's runtime requirement specifiers in `dependencies`, which the command line prints
as a `uv add` command.
Invalid settings, bindings, and ownership conflicts raise `APIGenerationError`, whose `diagnostics` are
ordered `Diagnostic` records; model generation errors keep their own types. The records, errors, operation
selection, and codec registrations are also available from `datamodel_code_generator.api_types`, and the
context records hooks read from `datamodel_code_generator.fastapi.templates`.

## Target configuration

The target configuration file holds `schema_version = 1` and the target's own settings. Model options stay on
the command line or in `pyproject.toml`. Relative paths are resolved against the file's directory, and
`--target-output` replaces `output`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `output` | required | Directory of the generated package, or of the distribution in standalone mode |
| `package` | required | Import path of the generated package |
| `model_package` | required | Import path of the models `--output` generates |
| `model_mode` | `"generate"` | `"verify"` compares the models with existing files instead of writing them |
| `selection` | every operation | Table of `include_operations`, `include_tags`, `exclude_operations`, `exclude_tags`, and a required `reason` |
| `layout` | `"routers"` | One router module per tag, or `"single"` for one `routes.py` |
| `handler_mode` | `"sync"` | `"async"` makes service methods coroutine functions; `[[handler_modes]]` sets one operation |
| `include_request` | `false` | Pass the Starlette `Request` to every service method |
| `body_mode` | `"typed"` | `"request"` passes the raw `Request` instead of a validated body; `[[body_modes]]` sets one operation |
| `primary_responses` | inferred | `[[primary_responses]]` choose the response a bare return value takes |
| `operation_names` | inferred | `[[operation_names]]` rename service methods |
| `router_names` | inferred | Table of group keys to group names: router modules, service arguments, and `<Name>Service` Protocols |
| `parameter_names` | inferred | `[[parameter_names]]` rename method arguments |
| `hooks` | none | `[[hooks]]` tables with `module` or `file`, and `callable` (default `transform`) |
| `templates` | none | Directory of template overrides and extra files |
| `update_groups` | none | Regenerate only these router groups |
| `package_mode` | `"embedded"` | `"standalone"` writes a distribution with `pyproject.toml` under `output` |
| `package_version`, `distribution_name`, `model_dependency` | none | Distribution metadata of standalone mode |
| `formatters`, `formatter_settings`, `custom_formatters`, `custom_formatter_kwargs` | builtin | Formatting of the generated package |
| `encoding`, `header`, `include_timestamp` | `"utf-8"` | Encoding and header of the generated files |
| `builtin_codec_compatibility`, `export_bindings` | none | `[[builtin_codec_compatibility]]` and `[[export_bindings]]` codec registrations |

Operations are selected by their key, the JSON pointer of the path item method, such as `/paths/~1pets/get`,
or a table with `pointer` and `document`:

```toml
[[handler_modes]]
operation = "/paths/~1pets/get"
mode = "async"

[[body_modes]]
operation = "/paths/~1upload/post"
mode = "request"

[[hooks]]
module = "project.hooks"
callable = "rename"
```

## The generated package

| Path | Owner | Contents |
| --- | --- | --- |
| `__init__.py`, `application.py` | generator | `create_app`, `build_router`, `install_openapi`, and the public types |
| `services.py` | generator | One service Protocol per router group, with an abstract method per operation |
| `routers/` or `routes.py` | generator | Route registrations |
| `responses.py`, `errors.py`, `auth_types.py`, `model_codecs.py` | generator | Results, errors, authentication, and codec types |
| `_generated/`, `_runtime/` | generator | Plans, bindings, and the runtime the package imports |
| `README.md`, `py.typed` | generator | Documentation and typing marker |
| `.dcg-target-manifest.json`, `.dcg-state/` | generator | What the last generation wrote, to regenerate and check safely |

The generator owns every file of the package and rewrites them on every generation, as it does the models,
restoring any you edited or deleted. Keep the service implementations, the authorizer, and the application in your
own modules; a file the generator never wrote at a path it needs stops the run (`E_OUTPUT_CONFLICT`). In standalone
mode the package lives under `output/src/<package>` next to `pyproject.toml` and `README.md`, the `pyproject.toml`
declares the runtime dependencies, and generation prints the `uv add --editable` command that adds the distribution
to your project instead.

## Regenerating and checking

Run the same command again after the OpenAPI document changes. The service Protocols change with the operations, so
type checkers point at the implementations to update, and Python refuses to create an instance of a subclass that
lacks a new method; your own modules are never touched. `--check` renders everything without writing it and exits
with 0 when nothing would change, 1 when a file would change, such as a generated file you edited or deleted, and 2
for an error; `render_fastapi` returns the same artifacts, each with its action.

Server generation always writes the models without the generation timestamp, as `--disable-timestamp` does, so a
run on unchanged inputs reproduces every byte: it writes nothing, and `--check` exits with 0, also from another
directory when the paths name the same input and target configuration files.
Only `include_timestamp = true` puts a time into the server files, and then every run changes them. The remote
lock file takes part as it does for model generation, when `--lockfile` names it, `--update-lock` or `--locked`
uses it, or the default `datamodel-codegen.lock` exists.

`update_groups` regenerates only the named router groups, and refuses when anything else changed.
`model_mode = "verify"` lets several targets share models generated once: each target compares the models with
the existing files instead of writing them, and reports `E_MODEL_MISMATCH` when they differ. Generate those
models with `--disable-timestamp` too.

## Templates and hooks

A `templates` directory overrides builtin roles by file name: `application.jinja2`, `router.jinja2`,
`services.jinja2`, and `readme.jinja2`. Each role
receives the values its builtin template uses and the frozen `context`. A `fastapi-templates.toml` file in the
directory declares extra files, rendered on every generation once for the project, each router, or each
operation:

```toml
schema_version = 1

[[files]]
template = "operation.md.jinja2"
path = "docs/{operation}.md"
scope = "operation"
```

A hook is a function that receives the `FastAPIContext` and returns a `FastAPIContextPatch`, which can rename
methods and router groups, reorder or remove operations, change method modes, and add extras and imports that
templates read:

```python
from datamodel_code_generator.fastapi.templates import FastAPIContext, FastAPIContextPatch


def transform(context: FastAPIContext) -> FastAPIContextPatch:
    return FastAPIContextPatch(
        handler_modes={operation.key: "async" for operation in context.operations},
    )
```

An invalid patch is `E_HOOK_CONTRACT`, a patch that makes the plan conflict is `E_HOOK_CONFLICT`, and an
exception the hook raises is reported as `E_HOOK_FAILURE` by the command line and propagates from the Python
API.

## Diagnostics

The command line prints each diagnostic to stderr as `CODE severity stage location: message`, and
[`--diagnostics-json`](cli-reference/manual/diagnostics-json.md#diagnostics-json) writes them as JSON. Errors
stop the run before anything is written: configuration (`E_CONFIG_*`), input (`E_INPUT_ROOT`), model
(`E_MODEL_CONFIG`, `E_MODEL_PARSE`), binding and planning (`BND_*`, `F_*`), hooks (`E_HOOK_*`), and ownership
(`E_OUTPUT_*`, `E_STATE_*`). Warnings, such as `W_DOCUMENTATION_ANNOTATION` for a documentation value the
served document cannot carry, and informational records, such as `S_OPERATION_EXCLUDED`, do not.
