# FastAPI Server Generation

!!! warning "Experimental"

    FastAPI server generation is experimental. Its options, generated package, template values, and served
    OpenAPI document may change. See [Experimental Features](experimental.md).

`--generate-server fastapi` generates the models of one OpenAPI document with the usual model options and, in
the same run, a FastAPI server package for its operations: routers, a service Protocol for each router group,
adapters for the parameter styles and media types FastAPI cannot read, authentication, and the served OpenAPI
document. Every
generated file is regenerated on each run. You write the business logic in your own modules, implementing the
service Protocols, and type checkers and Python check that every operation has a matching method.

Server generation needs Python 3.11 or later, both to run `datamodel-codegen` and as the target Python version.
Set the target with `--target-python-version` or a preset that sets it, as the quick start below does; the
default target, 3.10, and running on Python 3.10 are both refused with `Error:` and exit code 2.

## Quick start

Set the server options in `[tool.datamodel-codegen]` of `pyproject.toml`, next to the model options:

```toml
[tool.datamodel-codegen]
input = "openapi.yaml"
input-file-type = "openapi"
output = "models.py"
output-model-type = "pydantic_v2.BaseModel"
openapi-scopes = ["schemas", "api"]
preset = "standard-py312-20260909"
generate-server = "fastapi"
server-output = "server"
server-package = "server"
server-model-package = "models"
```

Then run `datamodel-codegen` without arguments to generate the models and the server, or pass the same settings
as options:

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
  --server-output server \
  --server-package server \
  --server-model-package models
```

The preset supplies the model options, as in the model [quick start](getting-started.md).
<!-- END AUTO-GENERATED FASTAPI QUICK START -->

`server/services.py` declares a Protocol for each router group, such as `PetsService` for the operations tagged
`pets`, with an abstract method for each operation that takes the operation's arguments as keywords. A method's
docstring names its route, such as `Handle GET /pets/{petId}.`, or, with `--use-schema-description`, the
operation's summary and description, formatted as model docstrings are, on one line with
`--use-single-line-docstring`. An optional
parameter or request body that the request omits arrives as `None`, and a parameter with a default as its default.
The routes declare such an input as FastAPI applications do, `T | None = None`, so FastAPI's own document shows
its schema as `anyOf` of the type and `null`.
Methods, router modules, and service arguments are named in snake case, and Protocols in PascalCase, whatever the model
naming options say: a method after its operationId, or after its method and path without one, such as
`get_pets_by_pet_id`. A method's arguments are named after their wire names as model fields are, so the quick start's
preset, which turns on `--snake-case-field` for Pydantic models, names the `petId` path parameter `pet_id`; without the
preset, pass `--snake-case-field` explicitly for snake_case arguments, or the argument keeps the wire name, `petId`.
`--aliases` and the special-field prefix options apply to arguments too. A derived name that its namespace already
holds takes the model's suffix, or the prefix `--naming-strategy` gives it, such as `id_1` for a query `id` beside a
path `id`, `body_1` for a parameter named `body`, or `pets_1` for a second tag that also becomes `pets`; an explicit
name of `--server-operation-names`,
`--server-router-names`, or `--server-parameter-names` must be new and is never renamed.
For a document whose `pets` operations are `GET /pets/{petId}` (`getPet`) and `DELETE /pets/{petId}` (`deletePet`),
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

Generation ends by printing to stderr the `uv add` command that adds the runtime dependencies of the package to your project,
including what the models import, such as `email-validator` for `format: email` and `python-ulid` for
`format: ulid`. It names only the minimum version each one needs, so uv adds the latest release and your lock file
keeps it. Run it, then start the application with an ASGI server:

```bash
uv add uvicorn
uv run uvicorn app:app
```

Because `Pets` subclasses `PetsService`, type checkers report a missing method or one whose arguments or result
do not match its operation, and Python refuses to create `Pets()` while a method is missing. `create_app` takes
one service for each group, under the group's name, passes its other keyword arguments, such as `lifespan` or
`middleware`, to `FastAPI`, and looks every method up when it registers the routes; any object with the right
methods works, and type checkers check it where you pass it. Registration stops with an error for what would
otherwise go wrong without one: a missing method (`AttributeError`), a coroutine function for an operation in the
`sync` handler mode or a plain method for one in the `async` mode, a secured operation without a callable
`authorize`, and an `operation_dependencies` entry that is not a sequence of `Depends(...)` (`TypeError`) or that
names no method of the router (`ValueError`). The package does not check the arguments of a method: one whose
signature a type checker would reject fails with Python's own error when its operation is requested. The generated
`server/README.md` lists the operations of each service and shows how to connect an `authorize` callback and your own
`FastAPI` application.

`create_app` and `build_router` also take `prefix` and `operation_dependencies`, FastAPI dependencies of single
operations keyed by service method name, such as `{"get_pet": [Depends(audit)]}`; `build_router` takes
`dependencies` for all of its routes, which `create_app` passes to `FastAPI`.

## Requirements

- The input is one OpenAPI 3.0, 3.1, or 3.2 document, from a file, a URL, or stdin; a directory or a list of
  files is refused.
- `--output-model-type` is `pydantic_v2.BaseModel` or `pydantic_v2.dataclass`; the other backends are
  refused. Custom templates, base classes, and types do not add backends.
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
- `--output` names the model file or package, and `--server-model-package` is its import path. The models can
  live inside the server package, and the server package inside a models directory; see
  [The generated package](#the-generated-package).
- The generated package needs FastAPI 0.141 and the other requirements it lists.

## Python API

`generate()` takes the server options under their Python names, with the model options; the choices are enums of
`datamodel_code_generator` such as `ServerType` and `ServerHandlerMode`, whose string values also work, and the JSON
options take mappings keyed by the same operation references:

<!-- BEGIN AUTO-GENERATED FASTAPI PYTHON API -->
```python
from pathlib import Path

from datamodel_code_generator import DataModelType, InputFileType, OpenAPIScope, ServerType, generate

generate(
    Path("openapi.yaml"),
    input_file_type=InputFileType.OpenAPI,
    openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
    output_model_type=DataModelType.PydanticV2BaseModel,
    preset="standard-py312-20260909",
    output=Path("models.py"),
    generate_server=ServerType.FastAPI,
    server_output=Path("server"),
    server_package="server",
    server_model_package="models",
)
```
<!-- END AUTO-GENERATED FASTAPI PYTHON API -->

It publishes the models and the package together and returns `None`, like model generation. Without an `output`, it
returns every model and server file as `GeneratedModules`, under the paths their import paths imply, such as
`("models.py",)` and `("server", "application.py")`, and writes nothing but the model metadata and a remote lock
update; see [Generating a Server or Client Package](using_as_module.md#generating-a-server-or-client-package).
`load_pyproject_config()` reads the server keys of `pyproject.toml`, whose paths and operation documents resolve
against its directory. Invalid settings, bindings, and template failures raise `datamodel_code_generator.Error` with
the message the command line prints after `Error:`. The `uv add` notice is a command-line message only.

## Server options

Every server setting is a command-line option and a `[tool.datamodel-codegen]` key of the same name, like the
model options, so profiles, `--ignore-pyproject`, `--generate-pyproject-config`, and `--generate-cli-command`
work for them as well. See [Target Generation Options](cli-reference/target-generation-options.md) for examples.

| Option | Default | Meaning |
| --- | --- | --- |
| `--generate-server fastapi` | off | Generate the server package with the models |
| `--server-output` | required | Directory of the generated package |
| `--server-package` | required | Import path of the generated package |
| `--server-model-package` | required | Import path of the models `--output` generates |
| `--server-layout` | `routers` | One router module per tag, or `single` for one `routes.py` |
| `--server-handler-mode` | `sync` | `async` makes service methods coroutine functions |
| `--server-handler-modes` | none | The handler mode of single operations, over `--server-handler-mode` |
| `--server-include-request` | off | Pass the Starlette `Request` to every service method |
| `--server-body-mode` | `typed` | `request` passes the raw `Request` instead of a validated body |
| `--server-body-modes` | none | The body mode of single operations, over `--server-body-mode` |
| `--server-primary-responses` | inferred | The response a bare return value takes: `status_code` and an optional `media_type` |
| `--server-operation-names` | inferred | Service method names of single operations |
| `--server-router-names` | inferred | Group keys, such as `tag:pets`, to group names: router modules, service arguments, and `<Name>Service` Protocols |
| `--server-parameter-names` | inferred | Method argument names of single operations, keyed by location and name such as `query:limit` |

`--server-handler-modes`, `--server-body-modes`, `--server-primary-responses`, `--server-operation-names`,
`--server-router-names`, and `--server-parameter-names` take a JSON object, inline or as the path of a JSON file, as
`--aliases` does; in `pyproject.toml` they are tables. `--generate-cli-command` prints these tables as
JSON, as for the model options that take JSON objects. A value on the command line takes precedence over the
selected profile, which takes precedence over the base table, and a command-line table replaces the whole table of
`pyproject.toml`. An operation's own entry takes precedence over the global setting, wherever each comes from. Paths
in `pyproject.toml`, including the JSON files, are relative to its directory, and command-line paths are relative to
the working directory. The documents of operation references are relative to the JSON file that holds them, however
the file is given, and to a symbolic link's directory when the file is given through one; without a file, to the
`pyproject.toml` directory for its tables, and to the working directory for inline JSON on the command line.

`--generate-server` requires `--server-output`, `--server-package`, and `--server-model-package`, and a `--server-*`
option given on the command line requires `--generate-server`; both stop with `Error:` and exit code 2. Server keys
of `pyproject.toml` are validated like model keys, but have no effect while no server is selected.

The package serves every operation under `paths` of the input; a path item under `components.pathItems` that no
path references has no URL and is not served. To leave paths out, pass
[`--openapi-include-paths`](cli-reference/openapi-only-options.md#openapi-include-paths). The models follow the
`api` scope as in a model-only run: inline schemas of the paths left out are not generated, while the schemas and
path items under `components` still are. A per-operation setting that names a path left out fails with
an error before generation.

Per-operation settings are keyed by operation reference: the JSON pointer of the path item method in the input
document, such as `/paths/~1pets/get`, or a document and a pointer joined by `#`, such as
`pets.yaml#/paths/~1pets/get`. The README of the generated package lists the reference of every operation.
These settings decide the generated code, including each method name, so they name an operation as the input
document does; `operation_dependencies`, an argument of the generated package, names it by its method.

```toml
[tool.datamodel-codegen.server-handler-modes]
"/paths/~1pets/get" = "async"

[tool.datamodel-codegen.server-body-modes]
"/paths/~1upload/post" = "request"
```

```bash
datamodel-codegen --server-handler-modes '{"/paths/~1pets/get": "async"}'
```

## Batch jobs

A [named job](pyproject_toml.md#named-jobs-experimental) whose settings select the server generates it as a single run
with the same settings does, so one `pyproject.toml` can describe model-only jobs and server jobs together. Put
`generate-server` in each server job or in a profile it selects, not in the base table, when other jobs generate
models only.

```toml
[tool.datamodel-codegen]
output-model-type = "pydantic_v2.BaseModel"
openapi-scopes = ["schemas", "api"]
target-python-version = "3.11"

[tool.datamodel-codegen.profiles.server]
generate-server = "fastapi"
server-package = "app.server"
server-model-package = "app.models"

[tool.datamodel-codegen.jobs.schemas]
input = "openapi.yaml"
output = "client/schemas.py"

[tool.datamodel-codegen.jobs.server]
profile = "server"
input = "openapi.yaml"
output = "app/models.py"
server-output = "app/server"
```

```bash
datamodel-codegen --all-jobs
datamodel-codegen --job server --server-handler-mode async
datamodel-codegen --all-jobs --check
```

A server option on the command line applies to every selected job and takes precedence over the job table, its
profile, and the base table, as a model option does; every selected job must then select the server.

`server-output`, like `output`, must not overlap the outputs of other jobs or contain the remote lock file. Server
jobs can share their models, though: jobs that each select the server may name the same `output`, which the batch
writes once. They must generate the same models for it, so give them the same input and model settings; a model-only
job cannot share its `output`. The server jobs of a batch write one generation timestamp, unless
`--disable-timestamp` leaves it out.

The batch publishes the server packages together with the models of every job once all jobs succeed, and publishes
nothing when a job fails or when jobs generate different models for a shared `output`; the `uv add` notice of each
package follows the publication on stderr. `--check` compares every job as a single run does and exits with 1 when
any of them would change. With `--output-format json`, a server job adds the generation or check payload of a single
server run to the batch document, with file paths relative to that job's model output directory. `--all-jobs
--watch` regenerates the batch, server jobs included, whenever an input any job read changes.

## The generated package

| Path | Contents |
| --- | --- |
| `__init__.py`, `application.py` | `create_app`, `build_router`, and the public types |
| `services.py` | One service Protocol per router group, with an abstract method per operation |
| `routers/` or `routes.py` | Route registrations |
| `security.py` | One FastAPI security dependency for each scheme |
| `_generated/`, `_runtime/` | Plans and the runtime the package imports |
| `README.md`, `py.typed` | Documentation and typing marker |

Every generation overwrites every generated file of the package, as it does the models, restoring any you edited
or deleted. A file that is no longer generated, such as the router module of a removed tag, stays until you delete
it, and `--check` lists it as an extra file. Keep the service implementations, the `authorize` callback, and the
application in modules outside the package: `--check` treats every `.py` file under `--server-output` as generated,
and a file at a path the package needs is overwritten.

The models can be part of the package. Name a model file or directory inside `--server-output` and its import path:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-server.nested-models.command -->
<!-- fmt: off -->

```bash
datamodel-codegen \
  --input api.yaml \
  --input-file-type openapi \
  --output app/server/models.py \
  --output-model-type pydantic_v2.BaseModel \
  --preset standard-py312-20260909 \
  --openapi-scopes schemas api \
  --generate-server fastapi \
  --server-output app/server \
  --server-package app.server \
  --server-model-package app.server.models
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-server.nested-models.command -->

The server package can also sit inside a models directory. Either way `--check` compares the outer directory once,
so it reports each file once. A model file and a server file that would take the same path stop the run with an
error, and so does a module with the name of a generated package directory beside it, such as `server/routers.py`
beside `server/routers/`, which Python would import instead of the module. A job can nest its own `output` and
`server-output` in the same way.

## Regenerating and checking

Generation stages the changed files and rolls back earlier changes if publication fails. A file that already holds
its generated bytes is not rewritten. Like the models, every file of the package is written as text with the line
ending of the platform, CRLF on Windows and LF elsewhere.

Run the same command again after the OpenAPI document changes. The service Protocols change with the operations, so
type checkers point at the implementations to update, and Python refuses to create an instance of a subclass that
lacks a new method; modules outside the package are never touched. `--check` renders everything without writing it
and exits with 0 when nothing would change, 1 when a file would change, such as a generated file you edited or
deleted or a file that is no longer generated, and 2 for an error. In Python, compare the files `generate()` returns
without an `output`.

`--check` uses the model output comparison: it prints unified diffs and missing or extra file messages to stdout.
It compares the rendered model and server text, including the README and `py.typed`, plus extra `.py` files under
the model and server output directories. CRLF and LF compare equally. Unrelated text files are outside this scope.
`--check --output-format json` uses the same check payload as model generation. Checking publishes nothing.

`--diff-against OLD_INPUT` renders the models and the server from both inputs without writing anything, and prints
what changes between them with the model input diff: unified diffs labeled `(baseline input)` and `(current input)`,
and files generated only from one of the inputs. It exits with 0 when both render the same files and 1 otherwise;
`--output-format json` emits the model input-diff payload. `--watch` regenerates the models and the server whenever
the input, a document it references, or a file an option names changes, as for model generation, and has the same
limits: it needs a local `--input` and cannot be combined with `--check` or `--output-format json`.

`--output-format json` publishes the files and emits the existing generation payload as one JSON document on
stdout. Its `output` is the configured model output; `files` contains the rendered model and server text. As in
the model payload, a file path is relative to the model output directory, which is `output` for a model directory
and its parent for a single model file, so `output` and the path give the file on disk. A file outside that
directory, such as a server package beside a model directory, has an absolute path. Paths use forward slashes.
`--check --output-format json` names the files the same way in `differences[].path`, while the diff labels stay
relative to the working directory. Model metadata and the remote lock are outside the payload, as for model
generation. The dependency notice goes to stderr, along with errors and Python warnings.

The models and the server files keep the generation timestamp unless `--disable-timestamp` or a preset that sets it
leaves it out, as for model generation; one run heads every file with the same timestamp. Without the timestamp, a
run on unchanged inputs reproduces every byte: it writes nothing, and `--check` exits with 0, also from another
directory when the paths name the same input and outputs. The remote
lock file takes part as it does for model generation, when `--lockfile` names it, `--update-lock` or `--locked`
uses it, or the default `datamodel-codegen.lock` exists. As for model generation, it must not overlap the input or
the model output, and it must lie outside the server output directory; a run with such a lock path stops before it
generates anything.

Several targets can share one models module: with the same input, whose file name the model header records, the
same model options, including the same `--openapi-include-paths`, and `--disable-timestamp`, each run writes the same
bytes, and `--check` reports a module that differs.

## Models and adapters

The generated models are the runtime contract. Routes declare the model types directly: a JSON body is
`Annotated[Model, Body()]`, a URL-encoded form of a `BaseModel` is a FastAPI form model, a primary JSON response is
`response_model=Model`, and path, query, header, and cookie parameters are `Annotated` FastAPI parameters of the
parameter's model type. A parameter's root model or type alias is unwrapped to its type when that type alone
validates the same, so the method receives the value. A default that the parameter's schema declares reaches the
method whenever the request omits the parameter, however the model options spell the type, such as with
`--use-annotated` or `--field-constraints`; a parameter whose type stays a root model receives that model with its
own default. How strictly a value follows the OpenAPI document is decided by the model generation options, not by
the server.

FastAPI cannot read some inputs, so a generated adapter reads them and validates the result with the model's
`TypeAdapter`: deepObject, label, matrix, spaceDelimited, and pipeDelimited parameters, parameters with `content`,
cookie arrays, header and path arrays, objects, enums and literals of non-string values, strict integers,
numbers, and booleans (`--strict-types`), repeated path placeholders, request bodies with several media types, text
and binary bodies, multipart forms, and forms of the `pydantic_v2.dataclass` backend. A multipart form body reaches
the method as its model, with uploads read to `bytes`.

A cookie parameter with a single value is a FastAPI `Cookie`, whatever its style, so the method receives the value
as the request sent it: nothing is percent-decoded (`a%20b` stays `a%20b`), a value in double quotes loses the
quotes, a `;` ends the value, and the last of several cookies of one name is taken. The generated client sends such
values as they are, too, and generation stops with an error for such a cookie whose name is not a token. An adapter that reads a single-valued cookie, for a strict integer for instance, does not
percent-decode it either; cookie arrays and objects, one cookie for each item or property, keep the `form` style's
percent-encoding.

An adapter converts a parameter's text by the schema's type before the model validates it, and accepts fewer
spellings than FastAPI does for a parameter it reads itself: integers and numbers as JSON writes them, so `5`, `-5`,
`2.5`, and `1e3` but not `05`, `+1`, or `1_000`, and booleans only as `true` and `false`. Turning on
`--strict-types int float bool` therefore narrows what those parameters accept. A repeated single value is taken as
FastAPI takes it, by adapters too: the last query value, cookie, form field, and object member, and the first header.
A parameter with JSON `content` is parsed by its model's `TypeAdapter.validate_json`, as a JSON body is, so pydantic's
JSON rules apply: a number is read as a float first, so a `Decimal` field gets the float's value
(`0.1000000000000000000000000001` becomes `Decimal("0.1")`) and `NaN` and `Infinity` reach a `float` field; an object
that repeats a member keeps the last value; and text that is not JSON, or JSON past pydantic's limits such as an
integer of thousands of digits or deep nesting, answers `422` with `json_invalid`.
A strict bytes type (`--strict-types bytes` with `format: binary`) cannot be a path, query,
header, or cookie parameter, since a parameter carries text: generation stops with an error that names it.

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

`create_app` serves the source document, not one FastAPI builds: generation writes the part of it the selected
paths use into `server/_generated/openapi.py`, and the application reads it on the first request for
`/openapi.json` and keeps it. The document keeps the source's OpenAPI version, `info`, `servers`, `tags`, security
schemes, webhooks, and the selected paths with all their operations as they are declared, callbacks included.
Components stay when something kept references them, and so do the `allOf` subtypes of a kept discriminator base.
What a reference names in another loaded document, or in a path that is left out, joins the components of the
kind the reference's position holds, under its own name or a numbered one when the name is taken; an OpenAPI 3.0
path item, or a media type before 3.2, has no component kind and is copied in place. References are rewritten
relative to the document, so a schema's `$id` is left out; a reference resolves as the models resolve it,
through `$id`, `$anchor`, and `$dynamicAnchor`. A reference that names nothing the generation loaded stays as it
is, with a warning. A link whose `operationRef` names a path that is left
out is left out, and so is a value without a JSON form: a YAML `!!binary` value, NaN or infinity, or a YAML alias
that contains itself. Each of them is reported as a `DocumentationAnnotationWarning`.

FastAPI's `/docs` and `/redoc` show the document, and behind a root path FastAPI adds that path as the first
server, as it does for its own document. `create_app`'s `prefix` leads every path, and the references and link
`operationRef`s into the paths follow them. The description settings you pass to `create_app`, `title`, `version`,
`summary`, `description`, `terms_of_service`, `contact`, `license_info`, `servers`, `openapi_tags`, and
`openapi_external_docs`, replace the document's, as they do in FastAPI's own; one you leave unset or empty keeps
the document's. The document is documentation only: the generated models are what the server validates with, and
routes added to the application later do not appear in it.

`create_app(..., source_openapi=False)` serves FastAPI's own document instead, built from the routes and the
models they declare. It describes what FastAPI derives: a parameter, header, or body that only an adapter reads
does not appear in it. An application of your own that includes `build_router(...)` serves FastAPI's document
too, unless it calls `serve_source_openapi(app, prefix=..., metadata=...)`, imported from the generated package,
with the prefix the router is included under and the description settings to replace. Path placeholders that are
not Python identifiers, or that repeat, appear in FastAPI's document under the route's own placeholder names.

## Production settings

The application keeps FastAPI's defaults for its documentation endpoints and for the shape of its error
responses, with one difference: a request that fails validation answers `422` with FastAPI's `{"detail": [...]}`
records without their `input` and `ctx`, and without pydantic's `url`, which FastAPI already leaves out: the
response carries each record's `type`, `loc`, and `msg`, so it does not return the rejected value as such. `loc`
and `msg` are still pydantic's and can hold text the client chose: a key of a mapping or an unexpected field in
`loc`, a discriminator tag in `msg`, and whatever a validator of your own puts in its error message. Parameters and
bodies that adapters read answer with the same three keys.

- `create_app` registers the handler, `validation_error_handler`, through FastAPI's `exception_handlers`. Pass
  your own `exception_handlers` to send other error bodies, for example ones without `loc` and `msg`; an entry
  for `RequestValidationError` replaces the package's.
- An application of your own that includes `build_router(...)` keeps FastAPI's default records, with `input` and
  `ctx`, unless you register the handler: `app.add_exception_handler(RequestValidationError,
  validation_error_handler)`, imported from the generated package.
- Other errors are FastAPI's: `404` and `405` bodies, and an opaque `500` for an exception in a service method or a
  result that fails its response model. That error goes to the server log with the values it rejected. Inputs that
  adapters read also answer `400 {"detail": "Invalid request"}` and `415 {"detail": "Unsupported media type"}`.
- FastAPI's own document (`source_openapi=False`) describes validation errors with FastAPI's `ValidationError`
  schema, in which `loc`, `msg`, and `type` are required and `input` and `ctx` are optional, so these bodies
  conform to it; the source document describes the responses the source declares.
- `create_app(..., docs_url=None, redoc_url=None, openapi_url=None)` removes `/docs`, `/redoc`, and
  `/openapi.json`.
- The `server` response header is the ASGI server's, not the application's; uvicorn leaves it out with
  `--no-server-header`.

## Templates

The server templates come from [`--custom-template-dir`](cli-reference/template-customization.md#custom-template-dir),
as model templates do: the files of its `fastapi` directory replace the builtin roles of the same name:
`facade.jinja2` (the package initializers), `application.jinja2`, `router.jinja2` (the routes module or each router
module), `services.jinja2`, `contract.jinja2` (`_generated/contract.py`), `security.jinja2`, `openapi.jinja2`
(`_generated/openapi.py`), and `readme.jinja2`. A role the directory does not hold,
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
  --openapi-scopes schemas api \
  --output models.py \
  --custom-template-dir templates \
  --extra-template-data extra.json \
  --generate-server fastapi \
  --server-output server \
  --server-package server \
  --server-model-package models
```

The values a role receives can change while server generation is experimental, and a value that an older copy of a
builtin template names renders as nothing, which usually stops the run with an invalid generated source file;
compare an override with the current builtin template after an upgrade. The templates write every module, class,
function, signature, and call; they receive records of names, type annotations, and the values of the runtime plans.
Each role receives these top-level values, where `imports` is the module's import block:

| Role | Values |
| --- | --- |
| `application.jinja2` | `imports`, `exports`, `final`, `options`, `routes`, `info`, `build_router`, `build`, `services`, `settings`, `create_app`, `arguments`, `fastapi`, `error_handlers`, `serve_source_openapi` |
| `contract.jinja2` | `imports`, `typed_dict`, `dependencies`, `plans`, `final`, `dataclass`, `parameter_adapter`, `body_adapter`, `operation_responses` |
| `facade.jinja2` | `module`, `docstring`, `imports`, `exports` |
| `openapi.jinja2` | `lines` |
| `readme.jinja2` | `title`, `package`, `model_package`, `backend`, `operations`, `services`, `protocols`, `arguments`, `schemes`, `forms`, `raw_request` |
| `router.jinja2` | `imports`, `docstring`, `routes`, `api_router`, `wiring`, `final`, `literal`, `templated`, `builder`, `group`, `build`, `services`, `settings` |
| `security.jinja2` | `imports`, `schemes`, `exports` |
| `services.jinja2` | `imports`, `typevar`, `abstract`, `protocol_type`, `protocols` |

`router.jinja2` receives one `route` for each operation, with its local names (`adder`, `router`, `wiring`,
`service`, `handler`), its `lookup` of the service method, its `principal` dependency or none, its `endpoint` with
the endpoint's `parameters` (`name`, `annotation`, `default`), the service method `call` with its `arguments`, and
its `registration`, whose `keywords` hold the optional `add_api_route` arguments. A builtin template writes each
parameter, argument, and route entry on its own line, and leaves the type annotations and plan values on one line for
the formatter.

A template that does not parse or render stops the run with an `Error` that names the file in the custom
template directory and, when Jinja knows it, the line. Generation also reads the model templates of the same
directory, so the model templates must keep the class and field names, as the requirements above describe.

## Errors and warnings

The command line prints failures to stderr as `Error: message` and exits with 2. Target errors combine their
messages in order, without codes, severity or stage labels. Model generation errors keep their
messages, including the class-name hint and input encoding context. Unexpected exceptions print a traceback.
Configuration, input, model, binding, planning, and template errors stop the run before publication. Warnings are
Python `UserWarning` subclasses from `datamodel_code_generator`: `DocumentationAnnotationWarning` reports
a source value the served document cannot carry. Each message starts with the output path, spelled relative
to the working directory when it lies inside it, so every generated package reports its own files. They respect
`--disable-warnings` and Python warning filters, and are not returned as diagnostic records.
