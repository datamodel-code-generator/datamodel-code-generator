# Migrating from fastapi-code-generator

This guide is for projects that generate a FastAPI application with
[fastapi-code-generator](https://github.com/koxudaxi/fastapi-code-generator) (*fcg*, the `fastapi-codegen` command)
and want to generate it with `datamodel-codegen --generate-server fastapi` instead. It maps each fcg option and habit
to its counterpart, and lists what has no counterpart yet.

!!! warning "Experimental"

    FastAPI server generation is experimental. Its options, generated package, template values, and served
    OpenAPI document may change. See [Experimental Features](experimental.md).

Both tools read one OpenAPI document and generate the models with datamodel-code-generator. fcg 0.8.1 requires
`datamodel-code-generator>=0.61,<0.66`, so a project that generates with fcg gets the model generation of those
releases; later model fixes and options reach it through `datamodel-codegen` only.

This page assumes the [FastAPI Server Generation](fastapi-server.md) tutorial: it links to the tested examples there
instead of repeating them.

## What changes

- **Generated code and your code are separate.** fcg writes a `main.py` whose function bodies you fill in. dcg writes a
  `app/server/` package with the routes, the request handling, and one *service Protocol* (a `typing.Protocol` class) per
  group of operations. You implement the Protocols in a module of your own and pass the objects to `create_app`.
- **You can regenerate at any time.** Every file of the package is overwritten on each run and nothing in it is meant
  to be edited, so there is nothing to merge when the document changes.
- **The settings live in `pyproject.toml`.** Every option has a `[tool.datamodel-codegen]` key of the same name, so
  `datamodel-codegen` without arguments repeats the run.

## Translate the command

=== "Before: fastapi-codegen"

    ```bash
    fastapi-codegen \
      --input openapi.yaml \
      --output app \
      --generate-routers \
      --python-version 3.12
    ```

    The run writes `app/main.py`, `app/routers/<tag>.py`, `app/dependencies.py`, and `app/models.py`.

=== "After: datamodel-codegen"

    <!-- BEGIN AUTO-GENERATED DOC EXAMPLE: fastapi-code-generator-migration.command -->
    <!-- fmt: off -->

    ```bash
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
    ```

    <!-- fmt: on -->

    <!-- END AUTO-GENERATED DOC EXAMPLE: fastapi-code-generator-migration.command -->

    The run writes `app/models.py` and the `app/server/` package, as the
    [tutorial](fastapi-server.md#2-generate-the-server) shows. The same settings fit in `pyproject.toml`, which is
    also on that page.

fcg's one `--output` directory splits into three settings: `--server-output` is the directory of the package,
`--server-package` its import path, and `--server-model-package` the import path of the models that `--output`
generates. fcg imported its models with `from .models`; the package imports them by this absolute path.

The command also needs:

- **Python 3.11 or later**, both to run `datamodel-codegen` and as the target of the generated code. The
  [preset](presets.md) in the command targets 3.12.
- **A Pydantic v2 backend**: `pydantic_v2.BaseModel`, or `pydantic_v2.dataclass` with `--use-missing-sentinel` and
  `--target-pydantic-version 2.14`.
- **The `api` scope**: `--openapi-scopes schemas api`, so that the models cover the parameters, bodies, and responses
  of the operations.

[Requirements](fastapi-server.md#requirements) lists all of them. The dcg command line has no short options such as
`-i` and `-o`.

## Option mapping

| fcg | dcg | Notes |
| --- | --- | --- |
| `-i`, `--input` | `--input` with `--input-file-type openapi` | One document; a directory or a list of files is refused. |
| `-o`, `--output` | `--server-output` and `--server-package` | Both are required with `--generate-server`. |
| `-m`, `--model-file` | `--output` and `--server-model-package` | `--output` is a file or a directory. A document with references between modules needs a directory. |
| `-r`, `--generate-routers` | `--server-layout routers`, the default | One router module per group. fcg's default, a single `main.py`, corresponds to `--server-layout single`: one `routes.py` and one `Service` Protocol. |
| `-t`, `--template-dir` | `--custom-template-dir`, templates in its `fastapi/` directory | Roles differ from fcg's files; see [Custom templates and visitors](#custom-templates-and-visitors). |
| `--model-template-dir` | `--custom-template-dir`, templates in its `pydantic_v2/` directory | The model templates are datamodel-code-generator's own. |
| `-c`, `--custom-visitor` | none | `--extra-template-data` passes static values to templates. |
| `--specify-tags` | none | Include the router of one group in your own app, or filter paths with `--openapi-include-paths`. |
| `-d`, `--output-model-type` | `--output-model-type` | Only `pydantic_v2.BaseModel` and `pydantic_v2.dataclass`, which needs `--use-missing-sentinel` and `--target-pydantic-version 2.14`. |
| `-p`, `--python-version` (default 3.10) | `--target-python-version`, or a preset that sets it | 3.11 or later. |
| `--include-request-argument` | `--server-include-request` | For the raw body, use `--server-body-mode request`, or `--server-body-modes` per operation. |
| `-e`, `--encoding`, `--enum-field-as-literal`, `--strict-nullable`, `--use-annotated`, `--reuse-model`, `--enable-faux-immutability`, `--disable-timestamp`, `--allow-remote-refs`, `--allow-private-network` | the same options | Every model option of dcg applies to the server's models. |

Options that dcg adds are described in [Server options](fastapi-server.md#server-options). The server target also
brings `--check`, `--watch`, `--diff-against`, [batch jobs](fastapi-server.md#batch-jobs), the
[`generate()` function](fastapi-server.md#python-api), and, from the same document,
[a Python client](python-client.md).

## Move your handlers

In fcg, the generated `main.py` holds a function for each operation, and you replace its `pass` with your logic. For the
tutorial's `getPet`, fcg 0.8.1 generates this `main.py`, shown without its other operations; the `HTTPException`
import, `PETS`, and the body of `get_pet` are yours:

<!-- fmt: off -->

```python
from __future__ import annotations

from typing import Union

from fastapi import FastAPI, HTTPException, Path

from .models import Error, Pet

app = FastAPI(
    title='Pets',
    version='1.0',
    servers=[{'url': 'http://localhost:8000'}],
)

PETS: dict[int, Pet] = {}


@app.get(
    '/pets/{petId}',
    response_model=Pet,
    responses={'404': {'model': Error}},
    tags=['pets'],
)
def get_pet(pet_id: int = Path(..., alias='petId')) -> Union[Pet, Error]:
    if (pet := PETS.get(pet_id)) is None:
        raise HTTPException(status_code=404, detail=f'Pet {pet_id} not found')
    return pet
```

<!-- fmt: on -->

In dcg, the same logic is the method `PetStore.get_pet` of a class in your own module, which the
[quick start](fastapi-server.md#4-implement-the-operations) shows next to `list_pets` and `create_pet`. Moving a
function takes these steps:

- **Collect the methods in a class.** Subclass the Protocol of the group, `PetsService` for the `pets` tag, and pass an
  object of it to `create_app(pets=...)`. The decorator, the path, `response_model`, and `tags` are generated.
- **Keep the names.** A method is named after its `operationId` in snake case, as fcg names a function. See
  [How methods and arguments are named](fastapi-server.md#how-methods-and-arguments-are-named).
- **Drop the FastAPI defaults.** A method takes the parameters and the body as keyword arguments, already validated by
  the models, so `Path(...)`, `Query(...)`, and `Depends(...)` disappear from the signature. The arguments follow the
  naming of model fields: the quick start's preset turns on snake case, and without it you pass `--snake-case-field`.
- **Return the primary response, or an `HTTPResult`.** A bare return value is the model of the primary response. For
  another declared status, such as the `404` above, return an `HTTPResult`, which the package validates with the
  response's model; see [Return values and `HTTPResult`](fastapi-server.md#return-values-and-httpresult).
- **Keep `HTTPException` for undocumented errors.** It answers FastAPI's `{"detail": ...}` body, as in fcg; see
  [Raise errors](fastapi-server.md#raise-errors).
- **Choose the handler mode in the settings.** fcg users turn a function into a coroutine function by editing it. In
  dcg, `--server-handler-mode async` or `--server-handler-modes` makes the method a coroutine function; see
  [Async methods](fastapi-server.md#async-methods).

The method's docstring is `Handle GET /pets/{petId}.` by default, where fcg used the operation's summary.
`--use-schema-description` writes the summary and description of the operation instead.

## Dependencies, security, uploads, and raw requests

### Per-request dependencies

fcg handlers declare what they need in their signature, such as `db: Session = Depends(get_db)`. A generated method
has a fixed signature, and the values that the `dependencies` and `operation_dependencies` of `create_app` and
`build_router` return never reach it.

Pass long-lived objects, such as a database client, to the constructor of your service class. For a value of the
request, generate with `--server-include-request` and let a dependency put it in `request.state`; the
[how-to](fastapi-server.md#the-request-and-per-request-dependencies) shows both. Dependencies still run before the
method, so they fit checks and side effects such as auditing.

### Security

fcg projects secure operations in their templates, with a `Depends` in each function. In dcg, the document describes
the security: declare `securitySchemes` and `security` in it, and the package gets a `security.py` with one FastAPI
dependency for each scheme. `create_app` then takes an `authorize(requirement_sets, credentials)` callback, and what
it returns reaches the method as the keyword `principal`. [Authenticate requests](fastapi-server.md#authenticate-requests)
walks through it with a bearer token.

### Uploads

fcg passes a file part of a `multipart/form-data` body as an `UploadFile`. In dcg, the part arrives as `bytes` in the
body model, so the file name and the content type are lost; see [Accept uploads](fastapi-server.md#accept-uploads).
When you need them, generate the operation with `--server-body-modes` set to `request` (or `--server-body-mode request`
for every operation) and read the form from the raw request.

### The raw request

`--include-request-argument` of fcg corresponds to `--server-include-request`: every method receives the Starlette
request as the keyword `request`. To receive the request instead of a validated body, use `--server-body-mode request`
or `--server-body-modes`, whose keys are operation references; see
[Per-operation settings](fastapi-server.md#per-operation-settings).

## Custom templates and visitors

dcg's model templates are the same mechanism that `--model-template-dir` of fcg used, so a template that overrides a
model usually carries over: put it in `pydantic_v2/` of the directory that `--custom-template-dir` names, and compare
it with the current builtin template. Model templates must keep the class and field names, because the server binds
each operation to them.

The server templates cannot be ported one to one. fcg renders one template with a list of `operations`. dcg renders a
fixed set of *roles*, each a file of the `fastapi/` directory of the template directory, with the values of that
role:

| Role | Generates |
| --- | --- |
| `application.jinja2` | `application.py`: `create_app`, `build_router` |
| `router.jinja2` | the routes module, or each router module |
| `services.jinja2` | `services.py`: the service Protocols |
| `security.jinja2` | `security.py` |
| `contract.jinja2` | `_generated/contract.py` |
| `facade.jinja2` | the package initializers |
| `openapi.jinja2` | `_generated/openapi.py` |
| `readme.jinja2` | `README.md` |

A role the directory does not hold keeps its builtin template. A file in `fastapi/` that is not a role is ignored
without a message, so check the file name when an override has no effect. The values of every role, which can change
while the target is experimental, are in [Templates](fastapi-server.md#templates).

Most reasons to customize an fcg template have a built-in answer: security is generated from the document, async is a
setting, and the request is an option. For the rest, rewrite the override from the builtin template of the role.

fcg's custom visitors compute extra variables for the templates from the parsed document. dcg has no visitors.
`--extra-template-data` passes static values to every role under the `#all#` key, so a script of your own can compute
them and write the JSON file before the run.

## Migrate incrementally

You do not have to move an application at once. Generate the package beside the old code, with another
`--server-output` and `--server-package` than any module that you already have, and include the router of a group in
your existing FastAPI application. With the default `routers` layout, each router module `app/server/routers/<group>.py` has its own `build_router`, which
takes the service of that group only, so you move one group at a time. The old routes keep working. fcg wrote
`models.py` into its `--output` directory too: if that is `app`, point `--output` and `--server-model-package` at another
module so that the two models do not overwrite each other.

[Use your own FastAPI app](fastapi-server.md#use-your-own-fastapi-app) shows `build_router` in an application of your
own. Three things apply while old and new routes share an application:

- **Registration order decides.** FastAPI uses the first route that matches a request, so remove the old function of an
  operation, or register the new router first, when the router takes it over.
- **Leave `serve_source_openapi` for the end.** It makes `/openapi.json` serve the generated document, which lists every
  generated path and none of the old routes. Until the old routes are gone, FastAPI's own document describes both.
- **Register `validation_error_handler` last.** It applies to the whole application, so it also changes the `422`
  bodies of the old routes.

To generate only the operations that you moved, pass `--openapi-include-paths`; see
[Server options](fastapi-server.md#server-options).

## Regenerate in CI

Run the generation command again whenever the document changes. The Protocols change with the operations, and type
checkers point at the implementations to update.

- **Check in CI.** `--check` renders everything without writing it and exits with 0 when nothing would change, 1 when a
  file would change, and 2 for an error. A run without a timestamp, which `--disable-timestamp` or a preset gives you,
  reproduces every byte; see [Check in CI](fastapi-server.md#check-in-ci).
- **Delete stale router modules.** Generation overwrites every generated file but does not delete one. After you remove
  a tag from the document, its `app/server/routers/<group>.py` stays, and `--check` lists it as no longer generated. Delete
  it by hand.
- **Keep your code outside the package**, in the `app/services` modules that `app/server/main.py` imports, which generation writes
  once with stubs. `--check` also reports any other `.py` file under `--server-output`.
- **Watch the document.** `--watch` regenerates on every change of the input and needs the `watch` extra,
  `datamodel-code-generator[watch]`; see [Compare and watch](fastapi-server.md#compare-and-watch).

## Behaviour differences

- **Nothing generated is editable.** Every file under `--server-output` is overwritten, so your code lives outside the
  package.
- **Methods are keyword-only** and return the primary response model, an `HTTPResult`, or a Starlette `Response`.
- **Operations are grouped by their first tag.** An operation without a tag goes to the `default` group, which makes
  `DefaultService` and the `default=` argument of `create_app`. fcg's modular output dropped such operations.
- **There is no `GET /` route.** fcg's modular `main.py` added a "Gateway of the App" route; the package serves only
  the paths of the document.
- **`/openapi.json` serves your document**, not the one FastAPI derives from the routes. `create_app(...,
  source_openapi=False)` serves FastAPI's again. See [Served OpenAPI document](fastapi-server.md#served-openapi-document).
- **`422` bodies omit `input` and `ctx`**, so the response does not return the rejected value; see
  [Production settings](fastapi-server.md#production-settings).
- **Literal paths come before templated paths**, so `/pets/me` is registered before `/pets/{petId}`. fcg sorted the
  routes by path.
- **The models file has more names.** A schema written inline takes a derived name such as `PetsGetResponse`, and
  parameters get type aliases. Declare schemas under `components` to name them yourself, as the tutorial does.
- **Generation is stricter.** A path placeholder without a declared path parameter stops the run, where fcg ignored it.
  A path parameter that names no placeholder of its operation's path is refused with an error instead of being
  generated, and every other path parameter is required even when the document leaves out `required` or declares it
  `false`.
- **Modular references need a directory.** When the document references schemas in other modules, `--output` must name
  a directory, not a file.

## Not available yet

- **Tag filtering (`--specify-tags`).** Include the router of a group in your own app with its `build_router`, or leave
  paths out with `--openapi-include-paths`. `create_app` takes every group.
- **Custom visitors (`--custom-visitor`).** Pass static values with `--extra-template-data`.
- **`UploadFile`.** A file part is `bytes`. Use `--server-body-mode request` or `--server-body-modes` and read the form
  yourself.
- **Python 3.10 targets.** A target below 3.11, and running `datamodel-codegen` on 3.10, are refused. Keep fcg for such
  a project until it moves to 3.11.
- **Backends other than Pydantic v2.** fcg also accepts `dataclasses.dataclass`, `typing.TypedDict`, and
  `msgspec.Struct`; the server target does not, and has no workaround.
