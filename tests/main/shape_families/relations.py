"""Relations between cells that need no validator: spellings of one meaning, backends, options and targets.

A relation compares two generations of dcg instead of a model with the validator, so it holds where the validator's
reading and the models' differ by design; it finds inconsistency only. Each relation is a kind a cell can show, beside
``KINDS``, and its witness is the smallest payload on which the two sides decide differently, with both verdicts.

``TWINS``
    Two shapes of one family that spell one schema meaning differently (definition order, ``allOf`` member order, a
    base named through an alias or directly, a ``$ref`` with siblings or ``allOf: [$ref]`` beside them, an addition
    beside ``allOf`` or as its member, a ``$ref`` or its target inline, a nested union or the flat one, two spellings of
    a nullable, ``anyOf`` or ``oneOf`` over the same variants, a one-member ``enum`` or ``const``). The
    cell of the left value is compared with the cell whose parameter is the right value and whose others are equal,
    on the payloads both pools hold where the validator decides alike on both schemas, with the lax verdicts of the
    same backend under the same contract. The finding belongs to the left cell.
``backends``
    A cell of another backend is compared with the Pydantic v2 cell of the same shape and contract, on the strict
    verdicts over JSON text (msgspec converts strictly already, and lax coercion differs by library), leaving out the
    payloads that hold null (the default models of the backends admit null for a property that is not required in
    different ways, see ``verdict.py``).
``REWRITES``
    A shape whose document is rewritten without changing its meaning (the order of its definitions, of ``allOf``
    members, of ``anyOf`` and ``oneOf`` members) is compared with the shape as the family writes it, on the lax
    verdicts; the finding belongs to the cell of the shape's parameters.
``option:<name>``
    A presentation option of ``options.py`` must not change the lax verdicts of a cell (``option_kind``).
``client``
    The container family's properties as the fields of one OpenAPI request body: the field argument of the generated
    httpx2 client accepts every value the schema and the model field accept (``client_findings``). A client argument may
    be wider than its field (an annotation cannot hold a key pattern); a narrower one refuses valid data.
"""

from __future__ import annotations

import copy
import functools
import json
import operator
import sys
import types
import typing
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, NotRequired, Required

import msgspec
from pydantic import TypeAdapter, ValidationError

from scripts._source_model import SourceModule
from tests.main.conftest import _generated_model, run_main_with_args

from .families import DIALECT, FAMILIES, MSGSPEC, PYDANTIC_V2, REF, Shape
from .verdict import CONTRACTS, Cell, holds_null, smallest, truth

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping
    from pathlib import Path

    from scripts._source_model import Scope

    from .families import Schema


@dataclass(frozen=True)
class Twin:
    """Two values of one dimension of a family that spell one meaning: the left cell is held to the right one."""

    name: str
    family: str
    dimension: str
    left: object
    right: object


TWINS: Final[tuple[Twin, ...]] = (
    Twin("order", "extend", "child_first", True, False),
    Twin("order", "union", "child_first", True, False),
    Twin("member-order", "mixin", "swapped", True, False),
    Twin("alias", "extend", "via", "alias", "direct"),
    Twin("ref-siblings", "extend", "via", "ref-siblings", "allof-siblings"),
    Twin("beside", "extend", "form", "beside", "member"),
    Twin("beside", "union", "form", "beside", "member"),
    Twin("inline", "union", "shape", "nested-ref", "nested-inline"),
    Twin("inline", "container", "element", "ref_nullable", "nullable_str"),
    Twin("flat", "union", "shape", "nested-inline", "flat"),
    Twin("nullable", "container", "element", "anyof_null", "nullable_str"),
    Twin("one-of", "union", "kind", "anyOf", "oneOf"),
    Twin("one-of", "tagged", "kind", "anyOf", "oneOf"),
    Twin("const", "tagged", "tag", "enum1-str", "const-str"),
)
"""Every pair of spellings within a family; a left shape whose right twin the family leaves out is not compared."""


def _reversed_definitions(document: Schema) -> Schema:
    return {**document, "$defs": dict(reversed(document["$defs"].items()))}


def _reversed_members(*keywords: str) -> Callable[[Schema], Schema]:
    def rewrite(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: list(reversed(rewrite(item))) if key in keywords and isinstance(item, list) else rewrite(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        return value

    return rewrite


REWRITES: Final[Mapping[str, Callable[[Schema], Schema]]] = {
    "reversed-definitions": _reversed_definitions,
    "reversed-all-of": _reversed_members("allOf"),
    "reversed-union-members": _reversed_members("anyOf", "oneOf"),
}
"""Every rewrite of a shape's document that keeps its meaning: the order of the definitions, of ``allOf`` members and
of ``anyOf`` and ``oneOf`` members."""

BACKENDS: Final = "backends"
CLIENT: Final = "client"
OPTION_PREFIX: Final = "option:"
RELATIONS: Final = (*dict.fromkeys(twin.name for twin in TWINS), *REWRITES, BACKENDS, CLIENT)
"""Every relation a cell can show, beside the ``option:<name>`` relations."""
TWIN_RELATIONS: Final = frozenset(twin.name for twin in TWINS)
REFERENCE_BACKEND: Final = PYDANTIC_V2
"""The backend the cells of the others are held to."""


def option_kind(option: str) -> str:
    """Return the relation of a presentation option: it keeps the verdicts of the cells."""
    return OPTION_PREFIX + option


def _difference(left: Cell, right: Cell, *, keep: Callable[[object], bool] = lambda _payload: True) -> str | None:
    """Return the smallest payload on which two cells decide differently, with both verdicts, or None.

    A payload counts where both pools hold it, ``keep`` accepts it, and the validator decides alike on both schemas.
    Both cells are judged (``judge``), so each holds its verdicts.
    """
    mine, theirs = truth(left.shape), truth(right.shape)
    other = {
        key: (valid, verdict) for key, valid, verdict in zip(theirs.keys, theirs.valid, right.verdicts, strict=True)
    }
    texts = {
        json.dumps(payload): f"{verdict} / {seen[1]}"
        for payload, key, valid, verdict in zip(mine.payloads, mine.keys, mine.valid, left.verdicts, strict=True)
        if (seen := other.get(key)) is not None and seen[0] == valid and seen[1] != verdict and keep(payload)
    }
    if not texts:
        return None
    witness = smallest(texts)
    return f"{witness} {texts[witness]}"


def _with(cell: Cell, kind: str, witness: str | None) -> Cell:
    return cell if witness is None else replace(cell, findings={**cell.findings, kind: witness})


def _index(cells: Iterable[Cell]) -> dict[tuple[object, ...], Cell]:
    return {(cell.shape.params, cell.backend, cell.contract): cell for cell in cells}


def _twin_params(params: tuple[tuple[str, object], ...], twin: Twin) -> tuple[tuple[str, object], ...]:
    return tuple((name, twin.right if name == twin.dimension else value) for name, value in params)


def _twin_findings(cell: Cell, index: dict[tuple[object, ...], Cell]) -> Cell:
    """Return a cell with the finding of every twin relation whose left value it has and whose right twin exists."""
    params = dict(cell.shape.params)
    related = cell
    for twin in TWINS:
        if twin.family != cell.shape.family or params[twin.dimension] != twin.left:
            continue
        key = (_twin_params(cell.shape.params, twin), cell.backend, cell.contract)
        if (other := index.get(key)) is not None:
            related = _with(related, twin.name, _difference(cell, other))
    return related


def with_twins(cells: Iterable[Cell]) -> list[Cell]:
    """Return the cells with the findings of ``TWINS``: each left cell held to its twin of the same backend."""
    cells = list(cells)
    index = _index(cells)
    return [_twin_findings(cell, index) for cell in cells]


def with_backends(cells: Iterable[Cell]) -> list[Cell]:
    """Return the cells with the ``backends`` findings: each other backend held to the reference cell of its shape.

    The cells carry strict verdicts (``judge(..., strict=True)``).
    """
    cells = list(cells)
    reference = {(cell.shape.params, cell.contract): cell for cell in cells if cell.backend == REFERENCE_BACKEND}
    return [
        cell
        if cell.backend == REFERENCE_BACKEND
        else _with(
            cell,
            BACKENDS,
            _difference(
                cell, reference[cell.shape.params, cell.contract], keep=lambda payload: not holds_null(payload)
            ),
        )
        for cell in cells
    ]


def rewritten(shape: Shape, rewrite: str) -> Shape | None:
    """Return a shape with its document rewritten, or None where the rewrite leaves the document as it was."""
    document = REWRITES[rewrite](shape.document)
    return None if json.dumps(document) == json.dumps(shape.document) else replace(shape, document=document)


def with_base(kind: str, cells: Iterable[Cell], base: Iterable[Cell]) -> list[Cell]:
    """Return the cells of a variant (an option, a rewrite), with ``kind`` where they decide unlike the base cells.

    The variant cell and the base cell share the shape's parameters, backend and contract.
    """
    index = _index(base)
    return [
        _with(cell, kind, _difference(cell, index[cell.shape.params, cell.backend, cell.contract])) for cell in cells
    ]


CLIENT_PARAMS: Final = ("container", "element", "nullable", "required")
"""The parameters of the container family that name its field in the client's request body."""
_COMPONENTS: Final = "#/components/schemas/"
CLIENT_PACKAGE: Final = "rows"
"""The package the models and the client of ``client_document`` are generated into."""


def _field_name(params: tuple[tuple[str, object], ...]) -> str:
    return "_".join(str(value).replace("-", "_").lower() for _, value in params)


def _as_component(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: item.replace(REF, _COMPONENTS) if key == "$ref" else _as_component(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_as_component(item) for item in value]
    return value


def client_document() -> Schema:
    """Return the OpenAPI 3.1 document whose request body ``Row`` holds a field per shape of the container family."""
    shapes = FAMILIES["container"].shapes()
    definitions = shapes[0].document["$defs"]
    row: Schema = {"type": "object", "properties": {}, "required": []}
    for shape in shapes:
        name = _field_name(shape.params)
        row["properties"][name] = _as_component(shape.document["$defs"]["Holder"]["properties"]["f"])
        if dict(shape.params)["required"]:
            row["required"].append(name)
    body = {"required": True, "content": {"application/json": {"schema": {"$ref": _COMPONENTS + "Row"}}}}
    return {
        "openapi": "3.1.0",
        "info": {"title": "Container family", "version": "1"},
        "servers": [{"url": "https://rows.example.com"}],
        "paths": {
            "/rows": {
                "post": {"operationId": "sendRows", "requestBody": body, "responses": {"204": {"description": "Sent."}}}
            }
        },
        "components": {"schemas": {"Item": definitions["Item"], "NItem": definitions["NItem"], "Row": row}},
    }


def field_shape(shape: Shape) -> Shape:
    """Return a shape whose checked definition is the field ``f`` of a container shape, for the pool of its values."""
    definitions = dict(shape.document["$defs"])
    definitions["Field"] = definitions["Holder"]["properties"]["f"]
    return Shape(shape.family, shape.params, {"$schema": DIALECT, "$defs": definitions}, "Field", shape.relation)


def _generate_client(backend: str, contract: str, directory: Path) -> Path:
    """Generate the models and the httpx2 client of ``client_document`` and return the models' module."""
    directory.mkdir(parents=True)
    source = directory / "openapi.json"
    source.write_text(json.dumps(client_document()), encoding="utf-8")
    args = [
        "--input",
        str(source),
        "--input-file-type",
        "openapi",
        "--openapi-scopes",
        "schemas",
        "api",
        "--output-model-type",
        backend,
        "--output",
        str(directory / CLIENT_PACKAGE / "models.py"),
        "--generate-client",
        "httpx2",
        "--client-output",
        str(directory / CLIENT_PACKAGE / "client"),
        "--client-package",
        f"{CLIENT_PACKAGE}.client",
        "--client-model-package",
        f"{CLIENT_PACKAGE}.models",
        "--client-body-arguments",
        "both",
        "--disable-timestamp",
        "--formatters",
        "builtin",
        *CONTRACTS[contract],
    ]
    run_main_with_args(args)
    return directory / CLIENT_PACKAGE / "models.py"


class _Unset:
    """Stands for the client's ``UNSET`` in an evaluated annotation, so that it can be taken out of the union."""


def client_module(client: Path) -> SourceModule:
    """Return the module of the client's synchronous operations, which writes the field arguments of ``send_rows``."""
    path = client / "resources" / "default" / "_sync.py"
    return SourceModule(path.read_text(encoding="utf-8"), path.as_posix())


def parameter_annotations(module: SourceModule) -> list[tuple[Scope, str, str, int]]:
    """Return each annotated parameter of the functions a module writes: the function, the name, the text, the line.

    The text is the annotation's source with each run of whitespace one space, as the model gives it.
    """
    return [
        (function, name, module.source(annotation.node), annotation.line)
        for annotation in module.annotations
        if (name := getattr(annotation.target, "arg", None))
        and (function := module.header_of(annotation.line, annotation.node.col_offset)) is not None
    ]


def imported_names(module: SourceModule) -> list[tuple[str, str, str, int]]:
    """Return each name a module-level ``from`` import binds: the name, its module, the name imported, the line.

    Relative imports and ``__future__`` imports are left out.
    """
    return [
        (record.bound, str(record.source), str(record.target).rpartition(".")[2], record.node.lineno)
        for record in module.imports
        if record.kind == "from"
        and record.scope.kind == "module"
        and not record.level
        and record.source != "__future__"
        and record.bound is not None
    ]


def annotation_namespace(module: SourceModule, models: object) -> dict[str, object]:
    """Return the names the client module's annotations read: its imports from outside the package, and the models.

    The client imports from standard modules only, which the test process has loaded. ``UNSET`` stands for ``_Unset``,
    which ``client_type`` takes out again.
    """
    namespace: dict[str, object] = {"UNSET": _Unset}
    for bound, source, name, _line in imported_names(module):
        if source.partition(".")[0] == CLIENT_PACKAGE:
            namespace.update({bound: models} if name == "models" else {})
        else:
            namespace[bound] = getattr(sys.modules[source], name)
    return namespace


def client_arguments(module: SourceModule) -> dict[str, str]:
    """Return the annotation text of each argument of the client's ``send_rows`` that takes the body as fields.

    The overload that takes the fields writes ``body: UNSET``.
    """
    overloads: dict[Scope, dict[str, str]] = {}
    for function, name, text, _line in parameter_annotations(module):
        if function.qualname.endswith(".send_rows"):
            overloads.setdefault(function, {})[name] = text
    fields = next(arguments for arguments in overloads.values() if arguments.get("body") == "UNSET")
    return {name: text for name, text in fields.items() if name != "body"}


def client_type(text: str, namespace: dict[str, object]) -> object:
    """Return the type an annotation of the client writes, without its ``UNSET`` member."""
    written = eval(f"({text})", namespace)
    members = typing.get_args(written) if typing.get_origin(written) in {typing.Union, types.UnionType} else (written,)
    return functools.reduce(operator.or_, [member for member in members if member is not _Unset])


def _strip_required(hint: object) -> object:
    while typing.get_origin(hint) in {NotRequired, Required}:
        hint = typing.get_args(hint)[0]
    return hint


def _accepts(check: Callable[[object], object], value: object) -> bool:
    try:
        check(copy.deepcopy(value))
    except (ValidationError, msgspec.ValidationError):
        return False
    return True


def _converter(hint: object) -> Callable[[object], object]:
    return functools.partial(msgspec.convert, type=hint)


def _assigner(row: type, name: str) -> Callable[[object], object]:
    instance = row.model_construct()
    return functools.partial(row.__pydantic_validator__.validate_assignment, instance, name)


def _checks(
    backend: str, row: type, namespace: dict[str, object], arguments: dict[str, str]
) -> Iterator[tuple[str, Callable[[object], object], Callable[[object], object]]]:
    """Yield, per field, the check of the model field and that of the client argument, in the backend's runtime.

    A Pydantic v2 field is checked by assigning the value to it, so its ``Field`` constraints count; msgspec fields
    are converted; TypedDict and dataclass fields are validated by a ``TypeAdapter`` of their annotation.
    """
    hints = typing.get_type_hints(row, include_extras=True)
    for name, text in arguments.items():
        written = client_type(text, namespace)
        field_type = _strip_required(hints[name])
        if backend == MSGSPEC:
            yield name, _converter(field_type), _converter(written)
        elif backend == PYDANTIC_V2:
            yield name, _assigner(row, name), TypeAdapter(written).validate_python
        else:
            yield name, TypeAdapter(field_type).validate_python, TypeAdapter(written).validate_python


def client_findings(backend: str, contract: str, directory: Path) -> list[Cell]:
    """Generate ``client_document`` with its client and return the container cells with their ``client`` findings.

    A cell's witness is the smallest value of its field's pool that the schema and the model field accept and the
    client argument refuses.
    """
    models = _generate_client(backend, contract, directory / "client")
    module = client_module(models.parent / "client")
    shapes = FAMILIES["container"].shapes()
    names = {_field_name(shape.params) for shape in shapes}
    arguments = {name: text for name, text in client_arguments(module).items() if name in names}
    module_name = f"shape_families_rows_{backend.replace('.', '_')}_{contract}"
    cells: list[Cell] = []
    with _generated_model(models, module_name, "Row") as row:
        namespace = annotation_namespace(module, sys.modules[row.__module__])
        checks = {name: (model, client) for name, model, client in _checks(backend, row, namespace, arguments)}
        for shape in shapes:
            model, client = checks[_field_name(shape.params)]
            known = truth(field_shape(shape))
            refused = [
                json.dumps(value)
                for value, valid in zip(known.payloads, known.valid, strict=True)
                if valid and _accepts(model, value) and not _accepts(client, value)
            ]
            cells.append(Cell(shape, backend, contract, {CLIENT: smallest(refused)} if refused else {}))
    return cells
