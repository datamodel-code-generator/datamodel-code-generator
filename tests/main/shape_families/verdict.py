"""The verdict of one cell: a shape generated for one backend under one contract, judged against the validator.

The validator is the payload suite's source validator (``source_schema_validator``), and a model's verdict on a
payload is the payload suite's (``PayloadRuntime`` and ``model_verdict``). A cell differs from the schema by any of
``KINDS``:

``model-raises``
    the model raises on a payload with an error that is no rejection;
``rejects-valid``
    the model rejects a payload the validator accepts, in its default (lax) mode;
``accepts-invalid``
    the model accepts a payload the validator rejects, in strict mode (``PYDANTIC_V2_UNASSERTED_MUTATION_CONSTRAINTS``
    of the payload suite: the default models use non-strict types, so lax coercion is no finding). A payload that
    holds null anywhere is not judged here, since the default models admit null for a property that is not required
    (``--strict-nullable`` and ``--use-missing-sentinel`` write the exact form), and neither is a payload that the
    schema accepts once ``oneOf`` reads as ``anyOf`` (the same table: the models read ``oneOf`` as a union).

A shape is generated with ``run_main_with_args`` and its module imported with ``_generated_model``, the helpers of the
main tests, which store no output; a generation that does not exit cleanly fails the test there, as in the payload
suite, so it is no kind of a cell. A ``Batch`` holds several rows of one document (the decision tables): it is
generated and imported once, and each row is judged on its own payloads. A module that does not import, lacks the
checked name or has no validator fails the test: every module of the families imports since #7102, and such a failure
is fixed in the default output, never kept.
TypedDict and standard dataclasses have no runtime of their own; their annotations
are judged by a Pydantic ``TypeAdapter``, which validates both. msgspec Structs are judged by ``msgspec.convert``, as
the payload suite does.

The payloads of a shape are a bounded pool, a pure function of its schema (``payload_pool``): every JSON kind at the
top level, the values the schema writes (enum members and consts, each bound and length with its neighbours, mapping
keys), arrays of up to two of them, objects of up to ``OBJECT_WIDTH`` of the schema's property names each holding one
of them, one level of nesting, and the payload suite's seed of the schema with its value neighbours and its one-step
mutations (a key dropped, an unknown key added, a value replaced by each JSON kind).
"""

from __future__ import annotations

import contextlib
import hashlib
import itertools
import json
import sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final, TypeAlias

from pydantic import TypeAdapter, ValidationError

from datamodel_code_generator.format import PythonVersion
from datamodel_code_generator.preset import latest_preset_name
from tests.main.conftest import _generated_model, run_main_with_args
from tests.main.payload_validation import PayloadBackend, PayloadRuntime, SchemaCase, source_schema_validator
from tests.main.payload_validation.neighbours import model_verdict, value_neighbours

from .families import DIALECT, MSGSPEC, Shape

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Iterator, Mapping, Sequence

    from .families import Schema

IMPORT_FAILS: Final = "import-fails"
MODEL_RAISES: Final = "model-raises"
REJECTS_VALID: Final = "rejects-valid"
ACCEPTS_INVALID: Final = "accepts-invalid"
KINDS: Final = (MODEL_RAISES, REJECTS_VALID, ACCEPTS_INVALID)
"""Every way a cell can differ from its schema."""


NEWEST_PRESET: Final = latest_preset_name("standard", PythonVersion.PY_311)
CONTRACTS: Final[Mapping[str, tuple[str, ...]]] = {"default": (), "preset": ("--preset", NEWEST_PRESET)}
"""The option sets a cell is generated under: the default output, whose differences are frozen, and the newest dated
standard preset for Python 3.11, the oldest Python the generated code runs on, whose differences are the to-do list."""

LEAVES: Final[tuple[object, ...]] = (
    None,
    True,
    0,
    1,
    2,
    5,
    6,
    1.5,
    "",
    "a",
    "abc",
    "abcdef",
    [],
    ["a"],
    ["a", None],
    {},
)
"""The top-level payloads of every pool: each JSON kind, small and large numbers and strings, short arrays."""
SMALL: Final[tuple[object, ...]] = (None, True, 1, 5, 1.5, "a", "abc", [], {})
"""The values a pool puts into arrays and under property names, beside the values the schema writes."""
OBJECT_WIDTH: Final = 2
"""The most property names one object payload of a pool holds."""
WRITTEN_VALUES: Final = 6
"""The values the schema writes that a pool puts beside ``SMALL`` inside arrays and objects."""
PROPERTY_NAMES: Final = 6
"""The most property names of the schema a pool builds objects over."""


def _nodes(value: object) -> Iterator[Schema]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _nodes(item)


def _key(value: object) -> str:
    """Return a key that tells payloads apart by value and by JSON kind (``1``, ``1.0`` and ``true`` differ)."""
    return json.dumps(value, sort_keys=True) + _kinds(value)


def _kinds(value: object) -> str:
    if isinstance(value, dict):
        return "{" + ",".join(map(_kinds, value.values())) + "}"
    if isinstance(value, list):
        return "[" + ",".join(map(_kinds, value)) + "]"
    return type(value).__name__[0]


def _unique(values: Iterator[object] | list[object]) -> list[object]:
    seen: set[str] = set()
    unique: list[object] = []
    for value in values:
        if (key := _key(value)) not in seen:
            seen.add(key)
            unique.append(value)
    return unique


def _written_values(schema: Schema) -> list[object]:
    """Return the scalars a schema writes: enum members, consts, bounds and lengths with their neighbours, tags."""
    found: list[object] = []
    for node in _nodes(schema):
        if isinstance(members := node.get("enum"), list):
            found.extend(members)
        if "const" in node:
            found.append(node["const"])
        for keyword in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
            if isinstance(bound := node.get(keyword), int | float) and not isinstance(bound, bool):
                found.extend([bound - 1, bound, bound + 1, bound * 2, bound * 3])
        for keyword in ("minLength", "maxLength"):
            if isinstance(length := node.get(keyword), int):
                found.extend(["x" * max(length - 1, 0), "x" * length, "x" * (length + 1)])
        if isinstance(discriminator := node.get("discriminator"), dict) and isinstance(
            mapping := discriminator.get("mapping"), dict
        ):
            found.extend(mapping)
            found.extend(int(key) for key in mapping if key.lstrip("-").isdigit())
    return [value for value in _unique(found) if not isinstance(value, dict | list)]


def _property_names(schema: Schema) -> list[str]:
    names: list[str] = []
    for node in _nodes(schema):
        candidates = [*node.get("properties", {}), *node.get("required", [])]
        if isinstance(discriminator := node.get("discriminator"), dict):
            candidates.append(discriminator["propertyName"])
        names.extend(name for name in candidates if name not in names)
    return names[:PROPERTY_NAMES]


def _mutations(payload: object) -> Iterator[object]:
    """Yield the one-step neighbours of a payload: a key dropped, an unknown key added, a value of another kind."""

    def visit(path: tuple[str | int, ...], value: object) -> Iterator[object]:
        if isinstance(value, dict):
            for key in value:
                yield _replaced(payload, path, {name: item for name, item in value.items() if name != key})
            yield _replaced(payload, path, {**value, "zzz": 1})
            for key, item in value.items():
                yield from visit((*path, key), item)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from visit((*path, index), item)
        if path:
            for replacement in SMALL:
                yield _replaced(payload, path, replacement)

    yield from visit((), payload)


def _replaced(payload: object, path: tuple[str | int, ...], value: object) -> object:
    """Return the payload with the value at ``path`` replaced, copying only the containers on the path."""
    if not path:
        return value
    head, rest = path[0], path[1:]
    if isinstance(payload, dict):
        return {**payload, head: _replaced(payload[head], rest, value)}
    items = list(payload)
    items[head] = _replaced(items[head], rest, value)
    return items


def payload_pool(schema: Schema, case: SchemaCase) -> tuple[object, ...]:
    """Return the bounded payload pool of a schema (see the module docstring); ``case`` is the schema's suite case."""
    written = _written_values(schema)
    small = [*SMALL, *written[:WRITTEN_VALUES]]
    names = _property_names(schema)
    pool: list[object] = [*LEAVES, *written]
    pool.extend([value] for value in small)
    pool.extend([first, second] for first, second in itertools.product(small[: len(SMALL)], repeat=2))
    for width in range(1, OBJECT_WIDTH + 1):
        for subset in itertools.combinations(names, width):
            pool.extend(dict(zip(subset, values, strict=True)) for values in itertools.product(small, repeat=width))
    nested = [{name: value} for name in names for value in small]
    for name in names:
        pool.extend({name: inner} for inner in nested)
        pool.extend({name: [inner]} for inner in nested)
    seed, *neighbours = value_neighbours(case)
    pool.extend([seed.payload, *(neighbour.payload for neighbour in neighbours), *_mutations(seed.payload)])
    return tuple(_unique(pool))


def _one_of_as_any_of(value: object) -> object:
    if isinstance(value, dict):
        return {("anyOf" if key == "oneOf" else key): _one_of_as_any_of(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_one_of_as_any_of(item) for item in value]
    return value


def holds_null(value: object) -> bool:
    """Return whether a payload holds null anywhere."""
    if isinstance(value, dict):
        return any(map(holds_null, value.values()))
    if isinstance(value, list):
        return any(map(holds_null, value))
    return value is None


def schema_case(shape: Shape) -> SchemaCase:
    """Return the payload suite's case of a shape's checked definition."""
    return _case(shape.family, shape.schema, shape.document)


def _case(family: str, schema: Schema, document: Schema) -> SchemaCase:
    """Return the payload suite's case whose validator reads ``schema``; ``document`` is what generation reads."""
    digest = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:16]
    return SchemaCase(
        id=f"shape_families/{family}/{digest}",
        input_file_type="jsonschema",
        source_path=Path("shape_families", f"{family}.json"),
        source_schema=schema,
        codegen_schema=document,
        temp_input_suffix=".json",
    )


def _one_of_as_any_of_case(case: SchemaCase) -> SchemaCase:
    return replace(case, id=f"{case.id}/any-of", source_schema=_one_of_as_any_of(case.source_schema))


@dataclass(frozen=True)
class Truth:
    """The pool of a shape, the validator's verdict on each payload, and the validator that reads oneOf as anyOf."""

    payloads: tuple[object, ...]
    valid: tuple[bool, ...]
    case: SchemaCase
    keys: tuple[str, ...]
    """The key of each payload, which tells payloads of two pools apart by value and by JSON kind."""

    def valid_as_any_of(self, payload: object) -> bool:
        """Return whether the schema accepts the payload once ``oneOf`` reads as ``anyOf``."""
        return source_schema_validator(_one_of_as_any_of_case(self.case)).is_valid(payload)


_TRUTHS: dict[str, Truth] = {}


def truth(shape: Shape) -> Truth:
    """Return the pool of a shape with the validator's verdicts, computed once per schema in this process."""
    case = schema_case(shape)
    if (known := _TRUTHS.get(case.id)) is None:
        known = _TRUTHS[case.id] = _truth(case, payload_pool(shape.schema, case))
    return known


def _truth(case: SchemaCase, payloads: tuple[object, ...]) -> Truth:
    """Return the payloads with the verdicts of the validator of ``case`` and their keys."""
    return Truth(
        payloads, tuple(map(source_schema_validator(case).is_valid, payloads)), case, tuple(map(_key, payloads))
    )


@dataclass(frozen=True)
class Row:
    """One row of a decision table: its parameters, the definition whose model reads its decision, and its payloads."""

    params: tuple[tuple[str, object], ...]
    name: str
    payloads: tuple[object, ...]


@dataclass(frozen=True)
class Batch:
    """Rows of one table read from one generated module.

    ``document`` is what generation reads, as ``input_file_type``. The validator reads a row's definition as
    ``prefix`` plus its name beside ``definitions``, the part of the document that holds them as JSON Schema
    (``{"$defs": ...}``, or ``{"components": {"schemas": ...}}`` with OpenAPI's own keywords read).
    """

    family: str
    document: Schema
    rows: tuple[Row, ...]
    prefix: str
    definitions: Schema
    input_file_type: str = "jsonschema"

    def schema(self, row: Row) -> Schema:
        """Return the JSON Schema the validator reads a row's payloads with."""
        return {"$schema": DIALECT, "$ref": self.prefix + row.name, **self.definitions}


@dataclass(frozen=True)
class Cell:
    """A shape generated for one backend under one contract, and how its model differs from the schema."""

    shape: Shape
    backend: str
    contract: str
    findings: Mapping[str, str] = field(default_factory=dict)
    """The smallest witness of each kind the cell shows (``KINDS``, or a relation of ``relations.py``): a payload as
    JSON, or the error."""
    verdicts: tuple[str, ...] | None = None
    """The model's verdict on each payload of the shape's pool (``model_verdict``), or None for a cell not judged."""

    @property
    def params(self) -> dict[str, object]:
        """The shape's parameters with the backend."""
        return {**dict(self.shape.params), "backend": self.backend}


class _UnusableCellError(Exception):
    """Raised when a generated module has no model to judge; it holds the kind and the error."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(kind, detail)
        self.kind = kind
        self.detail = detail


def _generate(
    document: Schema, input_file_type: str, backend: str, contract: str, directory: Path, extra: tuple[str, ...] = ()
) -> Path:
    """Generate a document into ``directory`` with the main tests' helper and return the path of the module.

    ``extra`` are more arguments, after those of the contract: an option whose effect a relation checks.
    """
    directory.mkdir(parents=True)
    source = directory / "schema.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    output = directory / "model.py"
    args = [
        "--input",
        str(source),
        "--input-file-type",
        input_file_type,
        "--output",
        str(output),
        "--output-model-type",
        backend,
        "--disable-timestamp",
        "--formatters",
        "builtin",
        *CONTRACTS[contract],
        *extra,
    ]
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        run_main_with_args(args)
    return output


def payload_backend(backend: str) -> PayloadBackend:
    """Return the payload suite's backend whose runtime and conformance table judge an output model type.

    msgspec Structs are converted by msgspec; every other output, TypedDict and standard dataclasses among them, is
    validated by a Pydantic ``TypeAdapter``.
    """
    return PayloadBackend.MSGSPEC if backend == MSGSPEC else PayloadBackend.PYDANTIC_V2


def _runtime(payload_type: object, backend: str) -> PayloadRuntime:
    """Return the payload suite's runtime of a generated type."""
    if payload_backend(backend) is PayloadBackend.MSGSPEC:
        return PayloadRuntime(PayloadBackend.MSGSPEC, payload_type)
    return PayloadRuntime(PayloadBackend.PYDANTIC_V2, payload_type, TypeAdapter(payload_type))


@contextlib.contextmanager
def _loaded(output: Path, names: Sequence[str], backend: str) -> Generator[list[PayloadRuntime], None, None]:
    """Import a generated module with the main tests' helper and yield the runtime of each of its ``names``.

    A module that does not import, lacks a name or cannot adapt one is the finding of every cell it holds, with the
    error.
    """
    module_name = "shape_families_" + hashlib.sha256(str(output).encode()).hexdigest()[:16]
    with contextlib.ExitStack() as stack:
        try:
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                stack.enter_context(_generated_model(output, module_name, names[0]))
                runtimes = [_runtime(getattr(sys.modules[module_name], name), backend) for name in names]
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"[:200]
            raise _UnusableCellError(IMPORT_FAILS, detail) from error
        yield runtimes


def _strict_verdict(runtime: PayloadRuntime, payload: object) -> str:
    """Return the verdict of strict validation of the payload's JSON text; msgspec converts strictly already."""
    if runtime.adapter is None:
        return model_verdict(runtime, payload)
    try:
        runtime.adapter.validate_json(json.dumps(payload), strict=True)
    except ValidationError:
        return "rejects"
    return "accepts"


def lax_verdicts(runtime: PayloadRuntime, known: Truth) -> tuple[str, ...]:
    """Return the model's verdict on each payload of a pool, in the default (lax) mode."""
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        return tuple(model_verdict(runtime, payload) for payload in known.payloads)


def strict_verdicts(runtime: PayloadRuntime, known: Truth) -> tuple[str, ...]:
    """Return the model's verdict on each payload of a pool, in strict mode over its JSON text."""
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        return tuple(_strict_verdict(runtime, payload) for payload in known.payloads)


def _witnesses(
    runtime: PayloadRuntime, known: Truth, verdicts: tuple[str, ...], *, judges_null: bool = False
) -> dict[str, list[object]]:
    """Return the payloads on which a model differs from the validator, by kind, from its lax ``verdicts``.

    A payload holding null is judged for ``accepts-invalid`` only where ``judges_null`` says so (the null table).
    """
    witnesses: dict[str, list[object]] = {}
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        for payload, valid, verdict in zip(known.payloads, known.valid, verdicts, strict=True):
            if verdict.startswith("raises"):
                witnesses.setdefault(MODEL_RAISES, []).append(payload)
            elif valid and verdict == "rejects":
                witnesses.setdefault(REJECTS_VALID, []).append(payload)
            elif (
                not valid
                and verdict == "accepts"
                and _accepted_invalid(runtime, known, payload, judges_null=judges_null)
            ):
                witnesses.setdefault(ACCEPTS_INVALID, []).append(payload)
    return witnesses


def _accepted_invalid(runtime: PayloadRuntime, known: Truth, payload: object, *, judges_null: bool) -> bool:
    """Return whether a payload the validator rejects and the model accepts is a finding.

    It is where strict validation accepts it too and ``oneOf`` read as ``anyOf`` still rejects it, and, where
    ``judges_null`` does not say so, it holds no null.
    """
    return (
        (judges_null or not holds_null(payload))
        and _strict_verdict(runtime, payload) == "accepts"
        and not known.valid_as_any_of(payload)
    )


def smallest(texts: Iterable[str]) -> str:
    """Return the shortest of some witnesses, the first in text order among equally short ones."""
    return min(texts, key=lambda text: (len(text), text))


Witnesses: TypeAlias = Callable[[PayloadRuntime, Truth, tuple[str, ...]], dict[str, list[object]]]
"""A reading of a model against the pool of its shape and its lax verdicts: the payloads of each kind found, by kind."""


def _findings(witnesses: Mapping[str, list[object]]) -> dict[str, str]:
    """Return the smallest witness of each kind, as JSON."""
    return {kind: smallest(json.dumps(payload) for payload in payloads) for kind, payloads in witnesses.items()}


def judge(
    shape: Shape,
    backend: str,
    contract: str,
    directory: Path,
    *,
    extra: tuple[str, ...] = (),
    strict: bool = False,
    witnesses_of: Witnesses = _witnesses,
) -> Cell:
    """Generate a shape for a backend under a contract into ``directory`` and return the cell with its findings.

    The cell keeps the model's verdict on every payload of the pool: lax, or strict over JSON text with ``strict``.
    ``extra`` are more generation arguments (see ``_generate``). ``witnesses_of`` reads the model against the pool; by
    default it finds the kinds of ``KINDS``.
    """
    known = truth(shape)
    output = _generate(shape.document, "jsonschema", backend, contract, directory, extra)
    with _loaded(output, [shape.name], backend) as (runtime,):
        verdicts = lax_verdicts(runtime, known)
        witnesses = witnesses_of(runtime, known, verdicts)
        if strict:
            verdicts = strict_verdicts(runtime, known)
    return Cell(shape, backend, contract, _findings(witnesses), verdicts)


def row_shape(batch: Batch, row: Row) -> Shape:
    """Return a row as the shape its cells name: the batch's table and document, the row's parameters and name."""
    return Shape(batch.family, row.params, batch.document, row.name, ())


def judge_batch(batch: Batch, backend: str, contract: str, directory: Path, *, judges_null: bool) -> tuple[Cell, ...]:
    """Generate a batch once for a backend under a contract and return the cell of each row with its findings.

    A cell keeps the model's lax verdict on each payload of its row.
    """
    shapes = [row_shape(batch, row) for row in batch.rows]
    truths = [_truth(_case(batch.family, batch.schema(row), batch.document), row.payloads) for row in batch.rows]
    output = _generate(batch.document, batch.input_file_type, backend, contract, directory)
    with _loaded(output, [row.name for row in batch.rows], backend) as runtimes:
        verdicts = list(itertools.starmap(lax_verdicts, zip(runtimes, truths, strict=True)))
        found = [
            _findings(_witnesses(runtime, known, row_verdicts, judges_null=judges_null))
            for runtime, known, row_verdicts in zip(runtimes, truths, verdicts, strict=True)
        ]
    return tuple(
        Cell(shape, backend, contract, findings, row_verdicts)
        for shape, findings, row_verdicts in zip(shapes, found, verdicts, strict=True)
    )
