"""Compose the shape families and hold a fixed sample of compositions to the known differences.

The composition library (``tests/main/shape_families/compose.py``) places a family's shape inside another's; the
nightly random tier (``scripts/shape_families_random.py``) draws them with Hypothesis and compares each with the last
release. Here every placement holds the first plain shape of every family once (``SAMPLE``), so a change of the
library, a rule or the generator that moves a sampled composition fails in the PR suite, and the helpers the random
tier compares two generators with (``shape_families/moves.py``) run on generated modules. The random tier's
comparison is the behaviour gate's: a composition is a gate cell, a seeded difference is a finding against a merge
base, and against a revision it is one unless a change record covers it.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import jsonschema
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from scripts._behaviour_classify import covers
from scripts._change_records import CellSelector
from scripts.compare_release import COMMON_ARGS
from scripts.legacy_rows import LEGACY_ROWS
from scripts.shape_families_random import (
    BACKEND_OPTION,
    COMPARISONS,
    REQUESTED,
    RULES,
    Example,
    RefusedError,
    absent_common_option,
    base_backends,
    common_args,
    composition_cell,
    examples,
    explorer_of,
    generation_args,
    run,
)
from tests.capabilities import needs
from tests.main.shape_families.compose import (
    COMPOSED,
    COMPOSITION,
    MAX_LEVELS,
    PLACEMENTS,
    PREFIXES,
    backends,
    compose,
    covering,
    describe,
    fits,
    levels,
    placed_levels,
    plain_levels,
)
from tests.main.shape_families.families import FAMILIES, MSGSPEC, PYDANTIC_V2, REF, TYPED_DICT
from tests.main.shape_families.kept import ISSUE, KEPT, LEGACY_ROW
from tests.main.shape_families.moves import moved, verdicts
from tests.main.shape_families.verdict import CONTRACTS, KINDS, judge

if TYPE_CHECKING:
    from tests.main.shape_families.compose import Level
    from tests.main.shape_families.kept import Kept

PLACED_BOUND = {"container": 24, "extend": 16, "union": 48, "tagged": 32, "mixin": 18}
"""The number of placed levels of each family, as the module docstring of the composition library states them."""


def _first(family: str, levels_: tuple[Level, ...]) -> Level:
    return next(level for level in levels_ if level.family == family)


SAMPLE = tuple(
    (_first(outer, placed_levels()), _first(inner, plain_levels())) for outer in PLACEMENTS for inner in FAMILIES
)
"""Each placement's first level around each family's first plain shape: 25 compositions of two levels."""


@pytest.mark.allow_direct_assert("The level counts are compared with the bound the library's docstring states.")
def test_compositions_enumerate_their_stated_bound() -> None:
    """Each placement yields the levels the docstring states, and every plain shape of the families is a level."""
    assert {name: sum(level.family == name for level in placed_levels()) for name in PLACEMENTS} == PLACED_BOUND
    assert len(plain_levels()) == sum(len(family.shapes()) for family in FAMILIES.values())


@pytest.mark.allow_direct_assert("Each check lists the definitions or references of a drawn composition that break it.")
@settings(derandomize=True, database=None, max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(chain=levels())
def test_drawn_compositions_are_closed_documents(chain: tuple[Level, ...]) -> None:
    """A drawn composition is a valid 2020-12 document whose references all resolve inside it.

    It keeps only the definitions its checked definition reaches, and the plain shape's under its level's prefix.
    """
    shape = compose(chain)
    assert isinstance(definitions := shape.document["$defs"], dict)
    jsonschema.Draft202012Validator.check_schema(shape.schema)
    assert 2 <= len(chain) <= MAX_LEVELS
    assert shape.family == COMPOSITION
    assert shape.name == chain[0].name
    assert shape.name in definitions
    assert [name for name in _referenced(definitions) if name not in definitions] == []
    assert [name for name in definitions if name != shape.name and name not in _referenced(definitions)] == []
    assert PREFIXES[len(chain) - 1] + chain[-1].name in definitions


def _referenced(value: object) -> set[str]:
    if isinstance(value, dict):
        return set().union(*map(_referenced, value.values()))
    if isinstance(value, list):
        return set().union(*map(_referenced, value))
    return {value.removeprefix(REF)} if isinstance(value, str) and value.startswith(REF) else set()


SAMPLE_CELLS = [
    pytest.param(contract, backend, marks=needs("typed-dict-validation") if backend == TYPED_DICT else ())
    for contract in CONTRACTS
    for backend in dict.fromkeys(backend for chain in SAMPLE for backend in backends(chain))
]
"""Each contract with each backend of the sample; TypedDict needs a Pydantic that validates ``typing.TypedDict``."""


@pytest.mark.allow_direct_assert("The sampled cells are held to the rules; each line names a cell.")
@pytest.mark.parametrize(("contract", "backend"), SAMPLE_CELLS)
def test_sampled_compositions_differ_only_as_known(contract: str, backend: str, tmp_path: Path) -> None:
    """Every difference of a sampled composition from the validator is one a family rule or a composition rule covers.

    Family rules cover the part of the level they describe (``compose.covering``), composition rules the rest.
    """
    uncovered = [
        f"{describe(chain)}: {kind} {witness}"
        for index, chain in enumerate(SAMPLE)
        if backend in backends(chain)
        for kind, witness in judge(compose(chain), backend, contract, tmp_path / str(index)).findings.items()
        if covering((*KEPT, *COMPOSED), chain, backend, contract, kind) is None
    ]
    assert uncovered == []


def _view_domain(family: str) -> dict[str, tuple[object, ...]]:
    """Return the values each parameter of a placed level's view takes (``compose.views``), the backend among them."""
    placement = PLACEMENTS[family]
    return {
        **{name: (value,) for name, value in placement.shown.items()},
        **dict(placement.dimensions),
        "inner": tuple(FAMILIES),
        "inner union": (False, True),
        "inner declares": (False, True),
        "inner requires": (False, True),
        "backend": FAMILIES[family].backends,
    }


def _reference_holds(rule: Kept) -> bool:
    """Return whether a composition rule's reference is an issue (``#n``) or a legacy row of its basis."""
    if rule.basis == LEGACY_ROW:
        return rule.reference in {row.name for row in LEGACY_ROWS}
    return rule.basis == ISSUE and re.fullmatch(r"#[1-9][0-9]*", rule.reference) is not None


@pytest.mark.allow_direct_assert("The composition rules are a static table; each check lists the rules that break it.")
def test_composition_rules_are_well_formed() -> None:
    """Every composition rule names a placement, kind, contracts, issue or legacy row and view values that exist."""
    assert [
        rule.reference
        for rule in COMPOSED
        if rule.family not in PLACEMENTS
        or rule.kind not in KINDS
        or not _reference_holds(rule)
        or not rule.contracts <= CONTRACTS.keys()
    ] == []
    assert [
        (rule.reference, name, values)
        for rule in COMPOSED
        for box in rule.boxes
        for name, values in box.items()
        if not set(values) <= set(_view_domain(rule.family).get(name, ()))
    ] == []


@pytest.mark.allow_direct_assert("The filter's answers on two compositions are compared.")
def test_compositions_without_a_common_value_are_left_out() -> None:
    """A mix declaring ``p`` an integer around a mix whose ``p`` is a string has no valid ``p``: no author writes it.

    The same mix around a string ``p`` is kept, as is a level that declares nothing beside its inner (``container``).
    A ``p`` inside the elements of a container is another object's: the integer mix around a container of the string
    mix is kept (``{"p": 1, "f": [{"p": "a"}]}`` is valid).
    """
    mixes = {dict(level.params)["second"]: level for level in placed_levels() if level.family == "mixin"}
    strings = next(level for level in plain_levels() if level.family == "mixin")
    assert (
        fits((mixes["int"], strings)),
        fits((mixes["str"], strings)),
        fits((_first("container", placed_levels()), strings)),
        fits((mixes["int"], _first("container", placed_levels()), strings)),
    ) == (
        False,
        True,
        True,
        True,
    )


def _inventory(*lacking: str) -> dict[str, list[str] | None]:
    """Return the option inventory of a base that has every option the random tier uses, but the ``lacking`` ones."""
    full: dict[str, list[str] | None] = {arg: None for arg in common_args(Path("schema.json")) if arg.startswith("--")}
    full["--formatters"] = ["black", "isort"]
    full[BACKEND_OPTION] = list(REQUESTED)
    return {
        name: [choice for choice in choices if choice not in lacking] if choices is not None else None
        for name, choices in full.items()
        if name not in lacking
    }


@pytest.mark.allow_direct_assert("The shared options a release lacks are named.")
def test_shared_options_are_checked_against_the_base_inventory() -> None:
    """A base release without ``--formatters`` is named before any generation, as a contract option it lacks is."""
    assert (absent_common_option(_inventory()), absent_common_option(_inventory("--formatters"))) == (
        None,
        "--formatters",
    )


@pytest.mark.allow_direct_assert("The backends drawn against a base without one backend are compared.")
@settings(max_examples=30, deadline=None, suppress_health_check=list(HealthCheck))
@given(data=st.data())
def test_a_backend_the_base_lacks_is_skipped(data: st.DataObject) -> None:
    """A base without one backend still runs: its shared options pass, and only the other backends are drawn."""
    inventory = _inventory(PYDANTIC_V2)
    available = base_backends(inventory)
    example = data.draw(examples(available))
    assert (absent_common_option(inventory), available, example.backend in available) == (
        None,
        tuple(backend for backend in REQUESTED if backend != PYDANTIC_V2),
        True,
    )


@pytest.mark.allow_direct_assert("The backends and the description of a composition are compared with its levels'.")
def test_composition_backends_are_shared_by_every_level() -> None:
    """A composition is generated for the backends every level's family is, in the first level's order.

    It is described one level per line, outermost first, a placed level saying that it holds the next.
    """
    container, tagged, extend = (_first(name, plain_levels()) for name in ("container", "tagged", "extend"))
    held = _first("container", placed_levels())
    assert backends((held, container)) == FAMILIES["container"].backends
    assert backends((held, tagged)) == (PYDANTIC_V2, TYPED_DICT, MSGSPEC)
    assert backends((held, extend)) == (PYDANTIC_V2,)
    assert describe((held, tagged)) == (
        "container holds (container=items, nullable=False, required=False)\n"
        "  tagged (tag=const-str, nullable=False, mapping=False, kind=oneOf)"
    )


@pytest.mark.allow_direct_assert("The verdict vectors of generated modules are compared.")
def test_moves_compare_the_verdicts_of_two_modules(tmp_path: Path) -> None:
    """Two modules of one composition move where they decide a payload differently or only one of them imports.

    The default output and the newest preset are two generators that differ on a nullable map's values (#6305). A
    module of a discriminated union whose tag admits null, made to fail its import, moves against the one that
    imports with an empty witness; two modules that both fail do not move.
    """
    container = _first("container", placed_levels())
    nullable_map = next(
        level
        for level in plain_levels()
        if level.family == "container"
        and dict(level.params) == {"container": "map", "element": "str", "nullable": True, "required": False}
    )
    nullable_tag = next(
        level for level in plain_levels() if level.family == "tagged" and dict(level.params)["nullable"]
    )
    outputs = {}
    for name, chain in (("map", (container, nullable_map)), ("tag", (container, nullable_tag))):
        shape = compose(chain)
        for contract in CONTRACTS:
            judge(shape, PYDANTIC_V2, contract, tmp_path / name / contract)
            outputs[name, contract] = shape, verdicts(tmp_path / name / contract / "model.py", shape, PYDANTIC_V2)
    shape, before = outputs["map", "default"]
    _, after = outputs["map", "preset"]
    assert moved(shape, before, before) is None
    assert moved(shape, before, after) == (
        '{"f": [{"f": {"zzz": 1}}]}',
        "before: accepts, strict accepts; after: rejects, strict rejects",
    )
    shape, after = outputs["tag", "default"]
    broken = tmp_path / "tag" / "broken.py"
    broken.write_text(
        (tmp_path / "tag" / "default" / "model.py").read_text(encoding="utf-8") + "\nraise ImportError('broken')\n",
        encoding="utf-8",
    )
    before = verdicts(broken, shape, PYDANTIC_V2)
    witness, detail = moved(shape, before, after) or ("-", "")
    assert (
        witness,
        detail.startswith("before: import-fails: ImportError"),
        detail.endswith("; after: imports"),
    ) == (
        "",
        True,
        True,
    )
    assert moved(shape, before, (before[0] + "!",)) is None


@pytest.mark.allow_direct_assert("The cell of a composition and the selectors that name it are compared.")
def test_compositions_are_gate_cells_named_by_change_records() -> None:
    """A composition is a gate cell labelled by each level's family and parameters, generated with the gate's arguments.

    A record's ``[[change.cells]]`` selector names it by that label and the input file type, as it names family cells:
    a selector of the outer family covers only compositions that start with it, one with ``**/`` any that holds it,
    and one without an input file type none.
    """
    tagged, container = (_first(name, placed_levels()) for name in ("tagged", "container"))
    extend = _first("extend", plain_levels())
    cells = [composition_cell(Path("c.json"), (outer, extend), PYDANTIC_V2, "preset") for outer in (tagged, container)]
    selectors = [
        CellSelector(("composition:*/tagged/**",), input_file_type="jsonschema"),
        CellSelector(("composition:*/**/extend/*",), input_file_type="jsonschema"),
        CellSelector(("composition:*/**",)),
    ]
    assert (
        cells[0].label,
        cells[0].args() == generation_args(Path("c.json"), PYDANTIC_V2, "preset"),
        sorted(cells[0].args()),
        [[covers(selector, cell) for cell in cells] for selector in selectors],
    ) == (
        (
            "composition:2/tagged/tag=const-str,nullable=False,mapping=False,kind=oneOf/"
            "extend/leaf=str,how=require,via=direct,form=member,child_first=True"
        ),
        True,
        sorted([*common_args(Path("c.json")), BACKEND_OPTION, PYDANTIC_V2, *CONTRACTS["preset"]]),
        [[True, False], [True, True], [False, False]],
    )
    assert set(COMMON_ARGS) <= set(common_args(Path("c.json")))


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


SEEDED_MAIN = """

import pathlib

_unseeded_main = main


def main(args=None):
    \"\"\"Generate, then add a comment to the output of every document with a discriminator: a seeded difference.\"\"\"
    code = _unseeded_main(args)
    arguments = list(args or ())
    source, output = (pathlib.Path(arguments[arguments.index(flag) + 1]) for flag in ("--input", "--output"))
    if "discriminator" in source.read_text(encoding="utf-8") and output.is_file():
        output.write_text(output.read_text(encoding="utf-8") + "# seeded\\n", encoding="utf-8")
    return code
"""
"""The tail of the candidate's ``__main__.py`` in ``test_a_seeded_difference_is_found_unless_a_record_covers_it``."""
OUTPUT_TEXT_RECORD = """[[change]]
id = "seeded-comment"
category = "output-text"
title = "A comment after the models of a tagged union"
summary = "The models of a document with a discriminator end with a comment."
refs = ["#1"]
kind = "output-text"
action = "none"

[[change.cells]]
input = ["composition:*/**/tagged/**"]
input_file_type = "jsonschema"
backends = "*"
options = "*"
"""
"""The record of the seeded difference: it covers every composition that has a tagged level."""
SEEDED_CASES = {
    "identity": (True, False, {"tagged": ["differs"], "container": []}),
    "undeclared": (False, False, {"tagged": ["undeclared"], "container": []}),
    "declared": (False, True, {"tagged": [], "container": []}),
}
"""Each mode: whether it compares with the merge base, whether the candidate has the record, and its findings."""


def _seeded_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **options: object) -> argparse.Namespace:
    """Return the arguments of a run whose candidate is the working tree that seeds a difference, its base its commit.

    A `record` (default none) is written as the one change record of the working tree; `identity` is the mode.
    """
    repo = tmp_path / "repo"
    shutil.copytree(
        Path(__file__).parents[2] / "src", repo / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    _git(repo, "init", "-q")
    _git(repo, "add", "-A", ".")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "tag", "base")
    main_module = repo / "src" / "datamodel_code_generator" / "__main__.py"
    main_module.write_text(main_module.read_text(encoding="utf-8") + SEEDED_MAIN, encoding="utf-8")
    if record := options.get("record"):
        fragments = repo / "docs" / "data" / "migration" / "unreleased"
        fragments.mkdir(parents=True)
        (fragments / "seeded-comment.toml").write_text(str(record), encoding="utf-8")
    monkeypatch.setenv("BEHAVIOUR_DIFF_REPOSITORY", str(repo))
    identity = bool(options.get("identity"))
    return argparse.Namespace(
        base="HEAD" if identity else _git(repo, "rev-parse", "HEAD"),
        identity=identity,
        base_python=options.get("base_python"),
        cache_dir=tmp_path / "cache",
    )


@pytest.mark.allow_direct_assert("The findings of each mode on two compositions are compared.")
@needs("posix")
@pytest.mark.parametrize("case", sorted(SEEDED_CASES))
def test_a_seeded_difference_is_found_unless_a_record_covers_it(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate that adds a comment to the models of tagged unions differs from its base on a tagged composition.

    The candidate is a working tree whose ``__main__.py`` adds the comment; its base is its commit. Against the merge
    base (``--identity``) the tagged composition is a finding and the container one is not. Against the revision, the
    change records judge the difference: without a record it is ``undeclared`` (the models decide the payloads
    alike), and an ``output-text`` record whose selector names the tagged compositions covers it.
    """
    identity, recorded, wanted = SEEDED_CASES[case]
    ns = _seeded_run(tmp_path, monkeypatch, identity=identity, record=OUTPUT_TEXT_RECORD if recorded else None)
    explorer = explorer_of(ns, tmp_path)
    extend = _first("extend", plain_levels())
    try:
        found = {
            outer: [
                finding.kind
                for finding in explorer.findings(
                    Example((_first(outer, placed_levels()), extend), PYDANTIC_V2, "default")
                )
            ]
            for outer in ("tagged", "container")
        }
    finally:
        explorer.close()
    assert (found, explorer.counts["declared"]) == (wanted, int(recorded))


@pytest.mark.allow_direct_assert("The refusals of a run and the kinds the rules may cover are compared.")
@needs("posix")
def test_a_run_refuses_what_the_gate_would_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A change record with a problem approves no composition: the run refuses, as the gate fails its run.

    The record of the seeded difference without its `refs` has the problem `entry seeded-comment has no refs`; with
    `--identity` no interpreter of another base may be named, and no rule covers a comparison's finding.
    """
    record = OUTPUT_TEXT_RECORD.replace('refs = ["#1"]\n', "")
    ns = _seeded_run(tmp_path / "records", monkeypatch, record=record)
    with pytest.raises(RefusedError, match="entry seeded-comment has no refs"):
        explorer_of(ns, tmp_path)
    ns = _seeded_run(tmp_path / "identity", monkeypatch, identity=True, base_python=Path(sys.executable))
    with pytest.raises(RefusedError, match="--base-python"):
        explorer_of(ns, tmp_path)
    assert COMPARISONS.isdisjoint(rule.kind for rule in RULES)


@pytest.mark.allow_direct_assert("The refusal of a record that names an unreleased option is compared.")
@needs("posix")
def test_a_run_refuses_a_selector_that_names_an_unreleased_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A selector naming an option the latest tag lacks is a problem of the gate and refuses the run as well."""
    record = OUTPUT_TEXT_RECORD.replace('options = "*"', 'options = [["--no-such-option"]]')
    ns = _seeded_run(tmp_path, monkeypatch, record=record)
    with pytest.raises(RefusedError, match="--no-such-option"):
        explorer_of(ns, tmp_path)


@pytest.mark.allow_direct_assert(
    "The exit status and the message of an identity run that compared nothing are compared."
)
@needs("posix")
def test_an_identity_run_that_compared_nothing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An identity run whose evidence has no compared input group exits 1 with its own message, as the gate does."""
    ns = _seeded_run(tmp_path, monkeypatch, identity=True)
    ns.examples, ns.budget, ns.seed = 0, 1.0, 0
    ns.database = ns.json = ns.out = None
    code = run(ns)
    out = capsys.readouterr().out
    assert (code, "not compared: no input group" in out, "0 finding(s)" in out) == (1, True, True)
