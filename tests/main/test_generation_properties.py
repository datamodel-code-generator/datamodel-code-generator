"""Generation properties beside the payload suite: the same input gives the same bytes, and dumps validate back equal.

Determinism: every input is generated for every output model type it is checked with, in two fresh processes under
different ``PYTHONHASHSEED`` values, the second running the inputs in reverse order, and once more in the first process
with the mapping keys of the input reordered where their order is no meaning. All three runs must write the same files
with the same bytes and exit alike. A job that a debug check of the suite stops is named by ``KNOWN_CHECK_FAILURES``,
with its issue.

Which key order is meaning follows the generator's documented behaviour:

- the names of a map of schemas: the fields of a model follow ``properties`` (and ``patternProperties``), and the models
  follow the definitions (``$defs``, ``definitions``, OpenAPI ``components/schemas``; docs/architecture.md, "in key
  order"); OpenAPI ``paths`` give the order of the operations;
- the keywords the generator copies into its output as data (every keyword ``JsonSchemaObject`` does not read, such as
  ``$schema``, ``xml``, ``contentMediaType`` or an extension) keep the order the document writes them in, the order of
  ``json_schema_extra`` (#6866);
- a keyword's value that holds no schema (``enum``, ``const``, ``default``, ``examples``, ``required``,
  ``discriminator``) is data.

No meaning, so reversed: the order of the keywords ``JsonSchemaObject`` reads, among themselves and against the copied
ones (they move in front of them), and the order of the keys of an OpenAPI document and of its ``components``.

The inputs are the shape families (``tests/main/shape_families/``), a fixed sample of the payload suite's schemas of
``tests/data`` and the documents of the server and client targets (``tests/data/generation_platform``), whose key order
is not changed (the reordered run of a target is a second generation in the same process).

Round trip: a valid payload that a model accepts is validated, dumped and validated again, and the second value must
equal the first, without a warning from the dump, for every backend that dumps: Pydantic v2 models and dataclasses
dump through the payload suite's ``pydantic_payload_dump``, msgspec Structs through ``msgspec.to_builtins``. TypedDict
and standard dataclasses have no dump of their own (the payload suite and the shape families judge them through a
Pydantic ``TypeAdapter``). The payloads are the shape families' pools and the payload suite's value neighbours that its
validator accepts.

``DCG_PAYLOAD_PROPERTIES`` selects the inputs (``MODES``): ``sample``, the default, for the PR tier, and ``all`` for the
nightly job.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import warnings
from dataclasses import dataclass
from functools import partial
from itertools import starmap
from pathlib import Path
from typing import TYPE_CHECKING, Final

import msgspec
import pytest

from datamodel_code_generator import DataModelType
from datamodel_code_generator.parser.jsonschema import JsonSchemaObject
from tests.checks.areas import Area, read_modes
from tests.main.conftest import EXPERIMENTAL_MISSING_IMPORT_WARNING
from tests.main.payload_validation import (
    SCHEMA_CASES,
    PayloadBackend,
    PayloadRuntime,
    backend_acceptance_exclusion_reason,
    load_generated_payload_runtime,
)
from tests.main.payload_validation.conformance import SCHEMA_KEYWORD_KINDS
from tests.main.payload_validation.models import GeneratedModelCache
from tests.main.payload_validation.native_numeric import pydantic_payload_dump
from tests.main.payload_validation.neighbours import model_verdict, value_neighbours
from tests.main.shape_families.families import FAMILIES, MSGSPEC, PYDANTIC_V2
from tests.main.shape_families.kept import BY_DESIGN, DEFAULT, Kept, differences
from tests.main.shape_families.verdict import CONTRACTS, Cell, judge

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from tests.main.payload_validation import SchemaCase
    from tests.main.shape_families.verdict import Truth

MODE_ENV: Final = "DCG_PAYLOAD_PROPERTIES"


@dataclass(frozen=True)
class Mode:
    """Which inputs a mode checks.

    Every ``job_stride``-th generation job and round trip case, and every ``shape_stride``-th shape of a family in the
    round trip, under the contracts named.
    """

    job_stride: int
    shape_stride: int
    contracts: tuple[str, ...]


MODES: Final[Mapping[str, Mode]] = {"sample": Mode(97, 8, ("default",)), "all": Mode(1, 1, tuple(CONTRACTS))}
"""The modes of ``MODE_ENV``: the PR tier's sample (97 is prime, so a stride over input x output model type meets
every output model type) and every input under the default output and the newest dated preset, for the nightly job."""
MODE: Final = MODES[os.environ.get(MODE_ENV) or "sample"]
CHUNK: Final = 150
"""The most generation jobs one test runs, so that the nightly job spreads over the test workers."""

OUTPUT_MODEL_TYPES: Final = tuple(model_type.value for model_type in DataModelType)
COMMON_ARGS: Final = ("--disable-timestamp", "--formatters", "builtin")
GENERATION_PLATFORM: Final = Path(__file__).parents[1] / "data" / "generation_platform"
SERVER_MODEL_TYPES: Final = (DataModelType.PydanticV2BaseModel.value, DataModelType.PydanticV2Dataclass.value)
"""The output model types the FastAPI target accepts."""
RUN_TIMEOUT_SECONDS: Final = 600
HASH_SEEDS: Final = ("0", "1")

CHILD_SCRIPT: Final = """
import contextlib
import io
import json
import sys
import warnings

from datamodel_code_generator.__main__ import main
from tests.checks.areas import CheckFailure

warnings.simplefilter("ignore")
codes = []
for argv in json.loads(open(sys.argv[1], encoding="utf-8").read()):
    print(*argv, file=sys.stderr, flush=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            codes.append(int(main(argv)))
        except CheckFailure as failure:
            codes.append(f"{type(failure).__name__}: {failure}")
json.dump(codes, sys.stdout)
"""
"""Generate every argument list of the JSON file named by the first argument, in order, and write the exit codes.

A generation that a debug check of the suite stops writes the check's message in place of its exit code
(``KNOWN_CHECK_FAILURES``), and the run goes on with the next one. Each argument list goes to stderr before its
generation, so a run that stops names the job it stopped in.
"""


def _same(value: object) -> object:
    return value


READ_KEYWORDS: Final = frozenset(
    {field.alias or name for name, field in JsonSchemaObject.model_fields.items()} | {"$defs", "definitions"}
)
"""The keywords the generator reads as schema: the others are copied into the output as data, in document order."""


def reordered(value: object) -> object:
    """Return a JSON Schema with the key order that is no meaning changed (module docstring)."""
    match value:
        case dict():
            read = [key for key in value if key in READ_KEYWORDS]
            return {
                **{key: KIND_READS.get(SCHEMA_KEYWORD_KINDS.get(key, ""), _same)(value[key]) for key in reversed(read)},
                **{key: item for key, item in value.items() if key not in READ_KEYWORDS},
            }
        case list():
            return [reordered(item) for item in value]
        case _:
            return value


def _names(value: Mapping[str, object]) -> dict[str, object]:
    """Keep the order of a map's names and reorder each schema it holds."""
    return {name: reordered(item) for name, item in value.items()}


def _reversed_keys(value: Mapping[str, object], reads: Mapping[str, Callable[[object], object]]) -> dict[str, object]:
    return {key: reads.get(key, _same)(value[key]) for key in reversed(value)}


KIND_READS: Final[Mapping[str, Callable[[object], object]]] = {
    "schema": reordered,
    "schema_map": _names,
    "dependencies": _names,
}
"""How each kind of ``SCHEMA_KEYWORD_KINDS`` reorders the value of a keyword the generator reads."""
OPENAPI_READS: Final[Mapping[str, Callable[[object], object]]] = {
    "components": partial(_reversed_keys, reads={"schemas": _names})
}
DOCUMENT_READS: Final[Mapping[str, Callable[[object], object]]] = {
    "jsonschema": reordered,
    "openapi": partial(_reversed_keys, reads=OPENAPI_READS),
}
"""How a document of each input file type is reordered."""


@dataclass(frozen=True)
class Job:
    """One generation: its name, its input document or file, the input's file name and its arguments."""

    name: str
    document: object
    """The input document, written as JSON, or None for an input read from ``source``."""
    source: Path | None
    input_file_type: str
    args: tuple[str, ...]
    outputs: tuple[tuple[str, str], ...] = (("--output", "model.py"),)
    """Each output option with the path it writes, under the job's output directory."""


def _shape_jobs(family: str) -> list[Job]:
    return [
        Job(
            f"{family}/{index}/{model_type}/{contract}",
            shape.document,
            None,
            "jsonschema",
            ("--input-file-type", "jsonschema", "--output-model-type", model_type, *COMMON_ARGS, *CONTRACTS[contract]),
        )
        for index, shape in enumerate(FAMILIES[family].shapes())
        for model_type in OUTPUT_MODEL_TYPES
        for contract in MODE.contracts
    ]


DUMP_WARNS: Final = "dump-warns"
DUMP_REJECTED: Final = "dump-rejected"
DUMP_CHANGES: Final = "dump-changes"
ROUND_TRIP_KINDS: Final = (DUMP_WARNS, DUMP_REJECTED, DUMP_CHANGES)
"""How a dump fails the round trip: it warns, the model rejects (or raises on) it, or validates it to another value."""

_NESTED_TAGGED_UNION: Final = {DUMP_WARNS: "#7326"}
SCHEMA_ROUND_TRIP_KEPT: Final[Mapping[tuple[str, str], Mapping[str, str]]] = {
    (PayloadBackend.PYDANTIC_V2.value, f"openapi/{case}"): _NESTED_TAGGED_UNION
    for case in (
        "discriminator_duplicate_value.yaml::components.schemas.GroupedItem",
        "discriminator_enum_tag_variants.yaml::components.schemas.Pets",
        "discriminator_merged_variant_tags_nested_clash.yaml::components.schemas.Pond",
    )
}
"""The known round trip differences, by backend and case: each kind with its issue. Every case named here is sampled.

A discriminated union that lists a ``RootModel`` of a union with the same discriminator warns on every dump (#7326).
"""
SAMPLE_BUCKETS: Final = 10
"""The sample takes one bucket of this many: about a tenth of the cases."""


def _sampled(case_id: str) -> bool:
    """Whether a case is in the sample: a hash of its id falls in the first bucket, so adding a case moves no other."""
    return int.from_bytes(hashlib.sha256(case_id.encode()).digest()[:8], "big") % SAMPLE_BUCKETS == 0


KEPT_CASE_IDS: Final = frozenset(case_id for _, case_id in SCHEMA_ROUND_TRIP_KEPT)
SAMPLED_CASES: Final = sorted(
    (case for case in SCHEMA_CASES if case.id in KEPT_CASE_IDS or _sampled(case.id)), key=lambda case: case.id
)
"""The fixed sample of the payload suite's schemas of ``tests/data``: the cases whose id hashes into one bucket of ten,
and every case ``SCHEMA_ROUND_TRIP_KEPT`` names. Adding an input never moves another case in or out."""


def _case_jobs() -> list[Job]:
    return [
        Job(
            f"{case.id}/{model_type}",
            case.codegen_schema,
            None,
            case.input_file_type,
            (
                "--input-file-type",
                case.input_file_type,
                "--openapi-scopes",
                "schemas",
                "--class-name",
                "Payload",
                "--output-model-type",
                model_type,
                *COMMON_ARGS,
            ),
        )
        for case in SAMPLED_CASES
        for model_type in OUTPUT_MODEL_TYPES
    ]


def _target_job(path: Path, model_type: str, target: str, generator: str) -> Job:
    input_file_type = "asyncapi" if path.name.startswith("asyncapi") else "openapi"
    return Job(
        f"{target}/{path.name}/{model_type}",
        None,
        path,
        input_file_type,
        (
            "--input-file-type",
            input_file_type,
            "--openapi-scopes",
            "schemas",
            "api",
            "--output-model-type",
            model_type,
            *COMMON_ARGS,
            f"--generate-{target}",
            generator,
            f"--{target}-package",
            target,
            f"--{target}-model-package",
            "models",
        ),
        (("--output", "models.py"), (f"--{target}-output", target)),
    )


def _target_jobs() -> list[Job]:
    return [
        *(
            _target_job(path, model_type, "server", "fastapi")
            for path in sorted((GENERATION_PLATFORM / "fastapi").glob("*.yaml"))
            for model_type in SERVER_MODEL_TYPES
        ),
        *(
            _target_job(path, model_type, "client", "httpx2")
            for path in sorted((GENERATION_PLATFORM / "client").glob("*.yaml"))
            for model_type in OUTPUT_MODEL_TYPES
        ),
    ]


def _argv(job: Job, source: Path, directory: Path) -> list[str]:
    outputs = [part for option, path in job.outputs for part in (option, str(directory / path))]
    return ["--input", str(source), *outputs, *job.args]


def _write_input(directory: Path, document: object) -> Path:
    directory.mkdir(parents=True)
    (path := directory / "schema.json").write_text(json.dumps(document), encoding="utf-8")
    return path


def _start(argvs: list[list[str]], seed: str, path: Path) -> subprocess.Popen[str]:
    """Start a fresh process that generates the argument lists, written to ``path``, under a hash seed."""
    path.write_text(json.dumps(argvs), encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, "-c", CHILD_SCRIPT, str(path)],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONHASHSEED": seed},
    )


EXIT: Final = "<exit status>"
"""The name under which an output tree holds the exit status of its generation, or the check that stopped it."""
KNOWN_CHECK_FAILURES: Final[Mapping[str, tuple[str, str]]] = {
    f"client/fields-cycle.yaml/{model_type.value}": ("#7362", "fix_constructor_field_ordering ordering-input")
    for model_type in (
        DataModelType.PydanticV2Dataclass,
        DataModelType.DataclassesDataclass,
        DataModelType.MsgspecStruct,
    )
}
"""The jobs that a debug check of the suite stops (``CheckFailure``), each with its issue and the part of the check's
message that names it: the constructor field order of dataclass-like models reads the fields of an ``allOf`` cycle
before the import pass makes them final (#7362). Held both ways: a job that a check stops other than as an entry
names fails, and so does an entry whose job generates."""


def _trees(process: subprocess.Popen[str], directories: list[Path]) -> list[dict[str, str]]:
    """Wait for a run and return the exit status and the files of each job's output directory, by relative path."""
    codes = json.loads(process.communicate(timeout=RUN_TIMEOUT_SECONDS)[0])
    return [
        {
            EXIT: str(code),
            **{
                path.relative_to(directory).as_posix(): path.read_text(encoding="utf-8", errors="replace")
                for path in sorted(directory.rglob("*"))
                if path.is_file()
            },
        }
        for code, directory in zip(codes, directories, strict=True)
    ]


def _difference(first: Mapping[str, str], second: Mapping[str, str]) -> str:
    """Return the first file whose text differs between two output trees, as a unified diff of its first lines."""
    name = next(name for name in sorted(first.keys() | second.keys()) if first.get(name) != second.get(name))
    diff = difflib.unified_diff(first.get(name, "").splitlines(), second.get(name, "").splitlines(), lineterm="", n=1)
    return f"{name}:\n" + "\n".join(list(diff)[:20])


RUNS: Final = ("first", "reordered", "second")
"""The runs of every job (module docstring); the second and the reordered run are compared with the first."""


def generate_runs(jobs: list[Job], directory: Path) -> dict[str, list[dict[str, str]]]:
    """Generate the jobs in the three runs of the module docstring and return their output trees, by run."""
    inputs = {
        "first": [
            job.source or _write_input(directory / "input" / str(index), job.document) for index, job in enumerate(jobs)
        ],
        "reordered": [
            job.source
            or _write_input(
                directory / "reordered-input" / str(index), DOCUMENT_READS[job.input_file_type](job.document)
            )
            for index, job in enumerate(jobs)
        ],
    }
    inputs["second"] = inputs["first"]
    outputs = {run: [directory / run / str(index) for index in range(len(jobs))] for run in RUNS}
    argvs = {run: list(starmap(_argv, zip(jobs, inputs[run], outputs[run], strict=True))) for run in RUNS}
    processes: list[subprocess.Popen[str]] = []
    try:
        first = _start(argvs["first"] + argvs["reordered"], HASH_SEEDS[0], directory / "first.json")
        processes.append(first)
        second = _start(argvs["second"][::-1], HASH_SEEDS[1], directory / "second.json")
        processes.append(second)
        first_trees = _trees(first, outputs["first"] + outputs["reordered"])
        second_trees = _trees(second, outputs["second"][::-1])[::-1]
    finally:
        for process in processes:
            process.kill()
            process.wait()
    return {"first": first_trees[: len(jobs)], "reordered": first_trees[len(jobs) :], "second": second_trees}


def run_differences(jobs: list[Job], trees: Mapping[str, list[dict[str, str]]]) -> list[str]:
    """Return a line for each job whose second or reordered run differs from its first run, with the first diff."""
    return [
        f"{job.name}: the {run} run differs in " + _difference(trees["first"][index], trees[run][index])
        for run in RUNS[1:]
        for index, job in enumerate(jobs)
        if trees["first"][index] != trees[run][index]
    ]


JOB_GROUPS: Final[Mapping[str, Callable[[], list[Job]]]] = {
    **{f"family-{family}": partial(_shape_jobs, family) for family in FAMILIES},
    "schemas": _case_jobs,
    "targets": _target_jobs,
}
"""Every group of generation jobs, before the mode samples them."""
JOB_CHUNKS: Final = [
    pytest.param(group, start, id=f"{group}-{start // CHUNK}")
    for group, jobs in JOB_GROUPS.items()
    for start in range(0, len(jobs()[:: MODE.job_stride]), CHUNK)
]


@pytest.mark.allow_direct_assert(
    "Each line names a job a debug check stops unlike KNOWN_CHECK_FAILURES, or whose runs differ, with the first diff."
)
@pytest.mark.parametrize(("group", "start"), JOB_CHUNKS)
def test_generation_is_deterministic(group: str, start: int, tmp_path: Path) -> None:
    """Every job writes the same bytes across hash seeds, processes, job order and the key order that is no meaning."""
    jobs = JOB_GROUPS[group]()[:: MODE.job_stride][start : start + CHUNK]
    trees = generate_runs(jobs, tmp_path)
    stopped = {job.name: tree[EXIT] for job, tree in zip(jobs, trees["first"], strict=True) if not tree[EXIT].isdigit()}
    finality = read_modes().get(Area.FACT_FINALITY)
    raising = finality is not None and finality.report_path is None
    known = {job.name: KNOWN_CHECK_FAILURES[job.name] for job in jobs if raising and job.name in KNOWN_CHECK_FAILURES}
    unlike = sorted(
        name
        for name in stopped.keys() | known.keys()
        if name not in stopped or name not in known or known[name][1] not in stopped[name]
    )
    assert not unlike, "debug checks stop jobs unlike KNOWN_CHECK_FAILURES:\n" + "\n".join(
        f"{name}: {stopped.get(name, 'generates')} (known: {known.get(name)})" for name in unlike
    )
    found = run_differences(jobs, trees)
    assert not found, "generation differs between runs:\n" + "\n\n".join(found)


@pytest.mark.allow_direct_assert("The lines of a pure function are compared with the lines each case expects.")
@pytest.mark.parametrize(
    ("other", "expected"),
    [
        pytest.param({EXIT: "0", "model.py": "a\n"}, [], id="equal"),
        pytest.param(
            {EXIT: "1", "model.py": "a\n"},
            [f"job: the second run differs in {EXIT}:\n--- \n+++ \n@@ -1 +1 @@\n-0\n+1"],
            id="exit",
        ),
        pytest.param(
            {EXIT: "0", "model.py": "b\n"},
            ["job: the second run differs in model.py:\n--- \n+++ \n@@ -1 +1 @@\n-a\n+b"],
            id="text",
        ),
        pytest.param(
            {EXIT: "0"}, ["job: the second run differs in model.py:\n--- \n+++ \n@@ -1 +0,0 @@\n-a"], id="missing"
        ),
    ],
)
def test_run_differences_show_the_first_differing_file(other: dict[str, str], expected: list[str]) -> None:
    """A job whose second run differs is named with the first file that differs, the exit status first among them."""
    first = {EXIT: "0", "model.py": "a\n"}
    job = Job("job", None, None, "jsonschema", ())
    assert run_differences([job], {"first": [first], "second": [other], "reordered": [first]}) == expected


DUMPS: Final[Mapping[PayloadBackend, Callable[[PayloadRuntime, object], object]]] = {
    PayloadBackend.MSGSPEC: lambda _runtime, value: msgspec.to_builtins(value),
}
"""The dump of each backend whose runtime is no Pydantic ``TypeAdapter``; the others dump through the adapter."""


def dump(runtime: PayloadRuntime, value: object) -> object:
    """Dump a validated value as JSON values with the backend's own serializer."""
    return DUMPS.get(runtime.backend, lambda runtime, value: pydantic_payload_dump(runtime.adapter, value))(
        runtime, value
    )


def round_trip_kind(runtime: PayloadRuntime, payload: object) -> str | None:
    """Return how the dump of an accepted payload's value fails the round trip, or None when it validates equal."""
    value = runtime.validate_python(payload)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        dumped = dump(runtime, value)
    verdict = model_verdict(runtime, dumped)
    equal = verdict == "accepts" and runtime.validate_python(dumped) == value
    return DUMP_WARNS if caught else None if equal else DUMP_CHANGES if verdict == "accepts" else DUMP_REJECTED


def round_trip_witnesses(runtime: PayloadRuntime, known: Truth, verdicts: tuple[str, ...]) -> dict[str, list[object]]:
    """Return the valid payloads of a shape's pool whose model value fails the round trip, by kind.

    ``verdicts`` are the model's lax verdicts on the pool (``judge``): only the valid payloads it accepts are dumped.
    """
    kinds = [
        (payload, round_trip_kind(runtime, payload))
        for payload, valid, verdict in zip(known.payloads, known.valid, verdicts, strict=True)
        if valid and verdict == "accepts"
    ]
    return {
        kind: [payload for payload, found in kinds if found == kind] for kind in {kind for _, kind in kinds} - {None}
    }


DUMPING_BACKENDS: Final = (PYDANTIC_V2, MSGSPEC)
"""The backends of the shape families that dump (module docstring); TypedDict and dataclasses have no dump."""

ROUND_TRIP_KEPT: Final[tuple[Kept, ...]] = (
    Kept(
        BY_DESIGN,
        "oneOf",
        "tagged",
        DUMP_CHANGES,
        DEFAULT,
        ({"tag": ("enum1-str", "enum2-str"), "nullable": (True,), "backend": (PYDANTIC_V2,)},),
        "a union whose tags also admit null is written without discriminator= (#7102), with or without a mapping, as "
        "Pydantic reads no tag that admits None: smart mode picks the variant of a null tag by its other fields, and "
        "the dump of one variant can validate back as another",
    ),
)
"""The known round trip differences of the shape families, written as ``shape_families/kept.py`` writes its rules."""

ROUND_TRIP_FAMILIES: Final = [
    pytest.param(family, backend, contract, id=f"{family}-{backend}-{contract}")
    for family, definition in FAMILIES.items()
    for backend in definition.backends
    if backend in DUMPING_BACKENDS
    for contract in MODE.contracts
]


@pytest.mark.allow_direct_assert("The round trip verdicts of a family are held to their rule list both ways.")
@pytest.mark.parametrize(("family", "backend", "contract"), ROUND_TRIP_FAMILIES)
@EXPERIMENTAL_MISSING_IMPORT_WARNING
def test_shape_family_dumps_validate_back_equal(family: str, backend: str, contract: str, tmp_path: Path) -> None:
    """The value of every valid payload a shape's model accepts dumps to JSON that validates back to an equal value.

    A cell whose module does not import is the shape families' finding (``test_shape_families.py``), not this test's.
    """
    cells = [
        judge(shape, backend, contract, tmp_path / str(index), witnesses_of=round_trip_witnesses)
        for index, shape in enumerate(FAMILIES[family].shapes())
        if index % MODE.shape_stride == 0
    ]
    found = differences(
        [
            Cell(
                cell.shape,
                cell.backend,
                cell.contract,
                {kind: cell.findings[kind] for kind in ROUND_TRIP_KINDS if kind in cell.findings},
            )
            for cell in cells
        ],
        ROUND_TRIP_KEPT,
    )
    assert not found.uncovered, "round trip differences no rule covers:\n" + "\n".join(found.uncovered)
    assert not found.stale, "rules to narrow or remove:\n" + "\n".join(found.stale)


ROUND_TRIP_BACKENDS: Final = (PayloadBackend.PYDANTIC_V2, PayloadBackend.PYDANTIC_V2_DATACLASS, PayloadBackend.MSGSPEC)
"""The payload suite's backends that dump."""
ROUND_TRIP_CASES: Final = [
    pytest.param(backend, case, id=f"{backend.value}:{case.id}")
    for case in SAMPLED_CASES
    for backend in ROUND_TRIP_BACKENDS
    if backend_acceptance_exclusion_reason(case, backend) is None
]


@pytest.mark.allow_direct_assert("The known differences are a static table; the check lists the entries that break it.")
def test_schema_round_trip_kept_entries_are_well_formed() -> None:
    """Every known difference names a sampled case of a dumping backend, a kind of ``ROUND_TRIP_KINDS`` and an issue."""
    cases = {(param.values[0].value, param.values[1].id) for param in ROUND_TRIP_CASES}
    assert [key for key in SCHEMA_ROUND_TRIP_KEPT if key not in cases] == []
    assert [
        (key, kind, reference)
        for key, kinds in SCHEMA_ROUND_TRIP_KEPT.items()
        for kind, reference in kinds.items()
        if kind not in ROUND_TRIP_KINDS or re.fullmatch(r"#[1-9][0-9]*", reference) is None
    ] == []


@pytest.fixture(scope="module")
def generated_model_cache(tmp_path_factory: pytest.TempPathFactory) -> GeneratedModelCache:
    """Cache the generated payload runtimes of the sampled cases."""
    return GeneratedModelCache({"base": tmp_path_factory.mktemp("generation_properties"), "adapters": {}})


@pytest.mark.allow_direct_assert("The kinds a case shows are held to its known differences both ways, with witnesses.")
@pytest.mark.parametrize(("backend", "case"), ROUND_TRIP_CASES[:: MODE.job_stride])
@EXPERIMENTAL_MISSING_IMPORT_WARNING
def test_schema_dumps_validate_back_equal(
    backend: PayloadBackend, case: SchemaCase, generated_model_cache: GeneratedModelCache
) -> None:
    """The value of every valid value neighbour a model accepts dumps to JSON that validates back to an equal value.

    The kinds a case shows equal its entry of ``SCHEMA_ROUND_TRIP_KEPT``: a new difference fails, and so does a fix
    until it removes the entry.
    """
    runtime = load_generated_payload_runtime(case, generated_model_cache, backend)
    found = [
        (kind, neighbour.label)
        for neighbour in value_neighbours(case)
        if neighbour.valid and model_verdict(runtime, neighbour.payload) == "accepts"
        if (kind := round_trip_kind(runtime, neighbour.payload)) is not None
    ]
    expected = SCHEMA_ROUND_TRIP_KEPT.get((backend.value, case.id), {})
    assert {kind for kind, _ in found} == expected.keys(), f"{case.id}: known {dict(expected)}, found:\n" + "\n".join(
        f"{kind}: {label}" for kind, label in found
    )
