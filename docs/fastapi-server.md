# FastAPI Server Generation

!!! warning "Experimental"

    FastAPI server generation is experimental. Its options, target configuration file, generated package,
    template values, and served OpenAPI document may change. See [Experimental Features](experimental.md).

`--generate-server fastapi` generates the models of one OpenAPI document with the usual model options and, in
the same run, a FastAPI server package for its operations: routers, a service Protocol for each router group,
adapters for the parameter styles and media types FastAPI cannot read, authentication, and the served OpenAPI
document. Every
generated file is regenerated on each run. You write the business logic in your own modules, implementing the
service Protocols, and type checkers, Python, and the package at startup check that every operation has a
matching method.

Server generation needs Python 3.11 or later, both to run `datamodel-codegen` and as the target Python version.
Set the target with `--target-python-version` or a preset that sets it, as the quick start below does; the
default target, 3.10, is refused with `E_CONFIG_VALUE`, and running on Python 3.10 with `E_PYTHON_UNSUPPORTED`.

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

Generation ends by printing the `uv add` command that adds the runtime dependencies of the package to your project,
including what the models import, such as `email-validator` for `format: email` and `python-ulid` for
`format: ulid`. It names only the minimum version each one needs, so uv adds the latest release and your lock file
keeps it. Run it, then start the application with an ASGI server:

```bash
uv add uvicorn
uv run uvicorn app:app
```

With [`--dependency-format requirements`](cli-reference/manual/dependency-format.md#dependency-format), generation
prints the lines of a requirements file instead, for pip or `uv pip`.

Because `Pets` subclasses `PetsService`, type checkers report a missing method or one whose arguments or result
do not match its operation, and Python refuses to create `Pets()` while a method is missing. `create_app` takes
one service for each group, under the group's name, passes its other keyword arguments, such as `lifespan` or
`middleware`, to `FastAPI`, and checks every method when the application starts; any
object with the right methods works, and type checkers check it where you pass it. The generated
`server/README.md` lists the operations of each service and shows how to connect an `authorize` callback and your own
`FastAPI` application.

## Requirements

- The input is one OpenAPI 3.0, 3.1, or 3.2 document, from a file, a URL, or stdin; a directory or a list of
  files is `E_INPUT_ROOT`.
- `--output-model-type` is `pydantic_v2.BaseModel` or `pydantic_v2.dataclass`; the other backends are
  `E_FASTAPI_BACKEND_UNSUPPORTED`. Custom templates, base classes, and types do not add backends.
- `--openapi-scopes` includes `api`, so that the models cover the parameters, bodies, and responses of the
  operations: `--openapi-scopes schemas api`. Without it, generation stops with
  `Error: --generate-server requires --openapi-scopes to include api` and exit code 2, and the Python API raises
  `datamodel_code_generator.Error` with the same message.
- The model options apply as they do without `--generate-server`, so the same options write the same model files
  (only the command `--enable-command-header` records differs), and a model option the model generation refuses,
  such as `--collapse-root-models-name-strategy` without `--collapse-root-models`, is refused before anything is
  written.
- The server files are headed, formatted, and encoded like the model files, by the same model options:
  `--formatters` (including its default and warning), `--custom-formatters` and `--custom-formatters-kwargs`,
  `--custom-file-header`, `--custom-file-header-path` and `--custom-file-header-mode`, `--disable-timestamp`,
  `--enable-version-header`, `--enable-command-header`, `--use-double-quotes`, `--builtin-format-line-length`,
  `--encoding` (for the Python files; the other files are UTF-8), and the formatter settings found from `--output`.
  The target settings have no output options of their own. `--use-type-checking-imports` does not apply to the
  server files, because FastAPI reads their annotations at run time.
- Custom templates and custom formatters must keep the class and field names of the models: the server binds each
  operation to the names in the generated model graph and does not read the rendered model source.
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
ordered `Diagnostic` records; model generation errors keep their own types. The records, errors, and codec
registrations are also available from `datamodel_code_generator.api_types`.

## Target configuration

The target configuration file holds `schema_version = 1` and the target's own settings. Model options stay on
the command line or in `pyproject.toml`. Relative paths are resolved against the file's directory, and
`--target-output` replaces `output`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `output` | required | Directory of the generated package |
| `package` | required | Import path of the generated package |
| `model_package` | required | Import path of the models `--output` generates |
| `layout` | `"routers"` | One router module per tag, or `"single"` for one `routes.py` |
| `handler_mode` | `"sync"` | `"async"` makes service methods coroutine functions; `[[handler_modes]]` sets one operation |
| `include_request` | `false` | Pass the Starlette `Request` to every service method |
| `body_mode` | `"typed"` | `"request"` passes the raw `Request` instead of a validated body; `[[body_modes]]` sets one operation |
| `primary_responses` | inferred | `[[primary_responses]]` choose the response a bare return value takes |
| `operation_names` | inferred | `[[operation_names]]` rename service methods |
| `router_names` | inferred | Table of group keys to group names: router modules, service arguments, and `<Name>Service` Protocols |
| `parameter_names` | inferred | `[[parameter_names]]` rename method arguments |

The package serves every path operation of the input. To leave paths out, pass
[`--openapi-include-paths`](cli-reference/openapi-only-options.md#openapi-include-paths); the models only those
operations use are not generated either.

Operations are selected by their key, the JSON pointer of the path item method, such as `/paths/~1pets/get`,
or a table with `pointer` and `document`:

```toml
[[handler_modes]]
operation = "/paths/~1pets/get"
mode = "async"

[[body_modes]]
operation = "/paths/~1upload/post"
mode = "request"
```

## The generated package

| Path | Owner | Contents |
| --- | --- | --- |
| `__init__.py`, `application.py` | generator | `create_app`, `build_router`, and the public types |
| `services.py` | generator | One service Protocol per router group, with an abstract method per operation |
| `routers/` or `routes.py` | generator | Route registrations |
| `errors.py`, `security.py` | generator | Errors, and one FastAPI security dependency for each scheme |
| `_generated/`, `_runtime/` | generator | Plans and the runtime the package imports |
| `README.md`, `py.typed` | generator | Documentation and typing marker |
| `.dcg-target-manifest.json` | generator | What the last generation wrote, to regenerate and check safely |

The generator owns every file of the package and rewrites them on every generation, as it does the models,
restoring any you edited or deleted. Keep the service implementations, the `authorize` callback, and the application in your
own modules; a file the generator never wrote at a path it needs stops the run (`E_OUTPUT_CONFLICT`).

## Regenerating and checking

Generation stages file changes and rolls back earlier changes if publication fails.
Before publishing, it rechecks planned file hashes and reports `E_STATE_CHANGED`
when they differ. Run generators that share output files one at a time.

The manifest records the generator version, the target kind and package, the model output and one hash of the model
files, and the hash of every file the target owns. Generation overwrites the owned files, deletes the ones it no longer
plans, and warns with `W_TARGET_EDITED` about an owned file edited since the last generation; `--check` reports it as a
difference. A manifest of an older or unknown format owns nothing: generation warns with `W_STATE_UNOWNED` and deletes
no file.

Run the same command again after the OpenAPI document changes. The service Protocols change with the operations, so
type checkers point at the implementations to update, and Python refuses to create an instance of a subclass that
lacks a new method; your own modules are never touched. `--check` renders everything without writing it and exits
with 0 when nothing would change, 1 when a file would change, such as a generated file you edited or deleted, and 2
for an error; `render_fastapi` returns the same artifacts, each with its action.

The models and the server files keep the generation timestamp unless `--disable-timestamp` or a preset that sets it
leaves it out, as for model generation; one run heads every file with the same timestamp. Without the timestamp, a
run on unchanged inputs reproduces every byte: it writes nothing, and `--check` exits with 0, also from another
directory when the paths name the same input and target configuration files. The remote
lock file takes part as it does for model generation, when `--lockfile` names it, `--update-lock` or `--locked`
uses it, or the default `datamodel-codegen.lock` exists.

Several targets can share one models module: with the same model options and `--disable-timestamp`, each run
writes the same bytes, and `--check` reports a module that differs.

## Models and adapters

The generated models are the runtime contract. Routes declare the model types directly: a JSON body is
`Annotated[Model, Body()]`, a URL-encoded form of a `BaseModel` is a FastAPI form model, a primary JSON response is
`response_model=Model`, and path, query, header, and cookie parameters are `Annotated` FastAPI parameters of the
parameter's model type. A parameter's root model or type alias is unwrapped to its type when that type alone
validates the same, so the method receives the value; the default is the one the model declares. How strictly a
value follows the OpenAPI document is decided by the model generation options, not by the server.

FastAPI cannot read some inputs, so a generated adapter reads them and validates the result with the model's
`TypeAdapter`: deepObject, label, matrix, spaceDelimited, and pipeDelimited parameters, parameters with `content`,
form-style cookies, header and path arrays, objects, enums and literals of non-string values, repeated path
placeholders, request bodies with several media types, text and binary bodies, multipart forms, and forms of the
`pydantic_v2.dataclass` backend. A multipart form body reaches the method as its model, with uploads read to
`bytes`.

A service method returns the value of its primary response, which FastAPI validates and serializes with the
route's `response_model` when the response is JSON (`by_alias=True`, `exclude_unset=True`); an
`HTTPResult(status_code, body, headers)` for another declared status or extra headers, whose body the package
validates and serializes with the declared response's model and default media type; or a Starlette `Response`,
sent as it is, for anything else, such as another media type or repeated headers. A `pydantic_v2.dataclass`
cannot tell an omitted field from one set to `None`, so its responses send optional fields as `null`.

## Security

`security.py` declares one FastAPI dependency for each security scheme the selected operations use: `APIKeyHeader`,
`APIKeyQuery`, or `APIKeyCookie` for an API key, `HTTPBasic`, `HTTPBearer`, or `HTTPDigest` for an HTTP scheme,
`OAuth2` with the declared flows, and `OpenIdConnect`, each with `auto_error=False`. A scheme no FastAPI class reads,
such as `mutualTLS`, is a function that returns `None`; replace it, or any other scheme, with
`app.dependency_overrides[security.<scheme>]`. The credentials are what the dependencies return: a string for an API
key and for the `Authorization` header OAuth2 and OpenID Connect read, `HTTPBasicCredentials`, or
`HTTPAuthorizationCredentials`.

`create_app` and `build_router` of a package with secured operations take an
`authorize(requirement_sets, credentials)` callback, which may be a coroutine function. Each secured operation calls
it once with its security requirement sets whose every scheme presented a credential, in declaration order, and the
credentials by scheme name; the handler receives the principal it returns, and it rejects a request by raising
`HTTPException`. A request that presents no complete requirement set answers `401`, unless the operation also
accepts no credentials, in which case the handler receives `None`. FastAPI's document lists each scheme of an
operation as a separate alternative, since FastAPI cannot document a requirement that combines several schemes.

## Served OpenAPI document

The application serves FastAPI's own document, built from the routes and the models they declare, so routes
added later and other routers appear in it as usual. Each generated route adds what FastAPI cannot derive from it,
taken from the source document at generation time: `responses=` documents every declared response, through its
model when the body is JSON, and `openapi_extra` documents the parameters, request bodies, and callbacks that
adapters read, with schema references resolved in place (a schema that refers to itself documents the inner
reference as `{}`). Path placeholders that are not Python identifiers, or that repeat, appear under the route's
own placeholder names.

## Templates

The server templates come from [`--custom-template-dir`](cli-reference/template-customization.md#custom-template-dir),
as model templates do: the files of its `fastapi` directory replace the builtin roles of the same name,
`application.jinja2`, `router.jinja2`, `services.jinja2`, and `readme.jinja2`. A role the directory does not hold,
or a custom template directory without a `fastapi` directory, keeps its builtin template. The builtin templates are
in the `_fastapi/templates` directory of the installed package; copy one to start from it. An override may include
or import the other builtin templates.

```text
templates/
├── fastapi/
│   └── services.jinja2
└── pydantic_v2/
    └── BaseModel.jinja2
```

Each role receives the values its builtin template uses and, as custom model templates do, the `#all#` entry of
[`--extra-template-data`](cli-reference/template-customization.md#extra-template-data); the role's own values take
precedence over the extra data:

```json
{"#all#": {"team": "the platform team"}}
```

```bash
datamodel-codegen \
  --input openapi.yaml \
  --input-file-type openapi \
  --output-model-type pydantic_v2.BaseModel \
  --preset standard-py312-20260909 \
  --output models.py \
  --custom-template-dir templates \
  --extra-template-data extra.json \
  --generate-server fastapi \
  --target-config fastapi.toml
```

A template that does not parse or render stops the run with `F_TEMPLATE_INVALID`, which names the file in the custom
template directory and, when Jinja knows it, the line. Generation also reads the model templates of the same
directory, so the model templates must keep the class and field names, as the requirements above describe.

## Diagnostics

The command line prints each diagnostic to stderr as `CODE severity stage location: message`, and
[`--diagnostics-json`](cli-reference/manual/diagnostics-json.md#diagnostics-json) writes them as JSON. Errors
stop the run before anything is written: an unsupported Python (`E_PYTHON_UNSUPPORTED`), configuration
(`E_CONFIG_*`), input (`E_INPUT_ROOT`), model
(`E_MODEL_CONFIG`, `E_MODEL_PARSE`), binding, planning, and templates (`BND_*`, `F_*`), and ownership
(`E_OUTPUT_*`, `E_STATE_*`). Warnings, such as `W_DOCUMENTATION_ANNOTATION` for a documentation value the
served document cannot carry, do not.
