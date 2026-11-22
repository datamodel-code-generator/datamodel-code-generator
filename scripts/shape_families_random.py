"""Draw compositions of the shape families and compare their output with a base: the last release, or a merge base.

Usage (from the repository root):
  python -m scripts.shape_families_random [BASE] [--identity] [--budget SECONDS] [--examples N] [--seed N]
      [--database DIR] [--json OUT] [--base-python PYTHON] [--cache-dir DIR] [--out DIR]

An example is a composition of the shape families (`tests/main/shape_families/compose.py`: a family's shape placed
inside another's, two or three levels deep), one backend every level's family is generated for, and one contract of
the family verdict (`verdict.CONTRACTS`: the default output and the newest dated preset). A composition for which the
payload suite finds no seed (a payload the validator accepts, `value_neighbours`) is left out: it is a contradiction
no author writes, or too intricate for the seed, and the run counts it.

Each example is a cell of the behaviour gate (`scripts/behaviour_diff.py`, `composition_cell`): its label is
`composition:<levels>/` and `<family>/<parameter>=<value>,...` for each level, outermost first (`label`), so a
`[[change.cells]]` selector names compositions as it names family cells, with `input_file_type = "jsonschema"`
(`composition:*/**/tagged/**`: every composition with a `tagged` level). Both sides generate it with the
gate's arguments through the gate's warm workers (`behaviour_diff.gate_of`), and the gate judges the two outcomes
(`behaviour_diff.judge_outcomes`). So what a comparison ignores is decided in one place, `compare_release`'s outcome
of a run (`Runner.generate`): the paths of the run's directory, output and package read `<tmp>`, `<output>` and
`<package>`, the version line of headers and of `--version` reads `<version>`, traceback frames are dropped from
messages, and every generation runs with `--disable-timestamp`, so no timestamp is written. Anything else that
differs is a difference.

Two modes:

- head against the last release (the default; BASE a released version, default the latest tag as `git describe
  --tags --abbrev=0` names it, or a git revision, which is compared at its merge base with HEAD and needs a release
  tag in HEAD's history): an example is a finding of one of these kinds:
  - `generation-fails`: the candidate does not generate it;
  - a kind of `verdict.KINDS`: its model differs from the validator (`verdict.judge`, the families' verdict);
  - where the two outcomes differ (exit status, output, warnings or messages: `compare_release.cell_facts`) and the
    change records do not cover the cell (the records whose fragments BASE lacks, `[[change.cells]]` in
    `docs/data/migration/unreleased/*.toml`, judged with their categories' evidence as the gate judges its cells):
    `base-fails` where BASE does not generate it, `moved` where their models decide a payload of the pool
    differently (the model's verdict in lax mode and in strict mode over the payload's JSON text) or one module
    imports and the other does not (`moves.py`), else `undeclared`.

  BASE generates only the contracts whose options it has (`behaviour_diff.opt_in_reason`): a dated preset newer than
  BASE is judged against the validator only. A backend BASE lacks (a value of `--output-model-type` its options do
  not have) is not drawn, and the run says so; the run refuses when BASE lacks an option every generation uses
  (`common_args`) or every backend. A validator finding is known when a rule covers it (`compose.covering`): a rule of
  `shape_families/kept.py` that covers one level's own part, or a rule over the composition's parameters
  (`compose.COMPOSED`); the default output's differences are frozen and the newest preset's are its to-do list, as
  for the families themselves. A difference from BASE is known only when a change record covers it.

- `--identity`, a branch against its merge base (for refactor rows): BASE is the branch's upstream (default
  `origin/main`), and the run compares this checkout with the merge base of BASE and HEAD. Every example whose
  outcomes differ in any way (`behaviour_diff.identity_verdict`: exit status, output, warnings, handled exceptions,
  messages, refused single-file attempt) is a finding of kind `differs`; nothing covers it, and the validator is not
  asked, as both sides share its differences. The run refuses when the merge base lacks a contract's options.

Hypothesis draws `ROUND` examples per round, round `n` with the seed `SEED + n`, until the budget is spent or
`--examples` examples were judged (`DEFAULT_EXAMPLES` by default, each generation stopped after `TIMEOUT` seconds). A
round that meets a new finding shrinks it (towards fewer levels and the first value of each dimension) and prints the
smallest composition with its witness, schema and command; the round's later examples and its shrinking only count
findings of that kind and family chain, and each kind and family chain is reported once a run. `--database` keeps the
failing examples, so the next run replays them first. Exit 1 on a finding, and in `--identity`
when the run's evidence is incomplete (an input group compared nothing, `compare_release.Evidence.group_problems`).
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

from hypothesis import HealthCheck, Phase, Verbosity, assume, given, seed, settings
from hypothesis import strategies as st
from hypothesis.database import DirectoryBasedExampleDatabase

import datamodel_code_generator
from scripts import compare_release
from scripts.behaviour_diff import (
    COMPOSITION_PREFIX,
    NOT_COMPARED_STOPPED,
    Cell,
    Gate,
    gate_of,
    judge_outcomes,
    opt_in_reason,
    option_inventory,
    plan_surface_problems,
    repository,
)
from tests.capabilities import missing
from tests.conftest import expected_environment_facts
from tests.main.payload_validation.neighbours import value_neighbours
from tests.main.shape_families.compose import COMPOSED, COMPOSITION, backends, compose, covering, describe, levels
from tests.main.shape_families.families import FAMILY_BACKENDS, TYPED_DICT
from tests.main.shape_families.kept import KEPT
from tests.main.shape_families.moves import moved, verdicts
from tests.main.shape_families.verdict import CONTRACTS, judge, schema_case

if TYPE_CHECKING:
    from tests.main.shape_families.compose import Level
    from tests.main.shape_families.families import Shape

ROOT: Final = Path(__file__).resolve().parents[1]
ROUND: Final = 25
"""The examples Hypothesis draws per round."""
DEFAULT_BUDGET: Final = 1500
"""Seconds of drawing a run spends by default, shrinking of the last finding aside."""
DEFAULT_EXAMPLES: Final = 2000
"""The examples a run judges at most by default, whatever its budget."""
TIMEOUT: Final = 30
"""Seconds before one generation fails; a composition generates in well under one."""
IDENTITY_UPSTREAM: Final = "origin/main"
"""The branch whose merge base with HEAD an identity run compares with, by default."""
GENERATION_FAILS: Final = "generation-fails"
BASE_FAILS: Final = "base-fails"
MOVED: Final = "moved"
UNDECLARED: Final = "undeclared"
DIFFERS: Final = "differs"
COMPARISONS: Final = frozenset({GENERATION_FAILS, BASE_FAILS, MOVED, UNDECLARED, DIFFERS})
"""The kinds of the comparison with the base: only a change record covers them, never a rule."""
RULES: Final = (*KEPT, *COMPOSED)


BACKEND_OPTION: Final = "--output-model-type"
REQUESTED: Final = tuple(dict.fromkeys(backend for family in FAMILY_BACKENDS.values() for backend in family))
"""The backends the families are generated for, each once, in the order the families name them."""


def common_args(schema: Path) -> list[str]:
    """Return the arguments every generation has, whatever the backend and the contract: the gate's (`Cell.args`)."""
    return ["--input", str(schema), "--input-file-type", "jsonschema", *compare_release.COMMON_ARGS]


def label(chain: tuple[Level, ...]) -> str:
    """Return a composition's cell label: its number of levels, then each level's family and parameters, outer first.

    `composition:<levels>/<family>/<parameter>=<value>,...`, a family and its parameters per level, joined by `/` as
    the gate labels a family cell; so `composition:*/**/tagged/**` names every composition with a `tagged` level.
    """
    return f"{COMPOSITION_PREFIX}{len(chain)}/" + "/".join(
        f"{level.family}/" + ",".join(f"{name}={value}" for name, value in level.params) for level in chain
    )


def composition_cell(schema: Path, chain: tuple[Level, ...], backend: str, contract: str) -> Cell:
    """Return the gate cell of a composition written to ``schema``, one backend and one contract."""
    return Cell(schema, label(chain), "jsonschema", backend, CONTRACTS[contract], COMPOSITION)


def generation_args(schema: Path, backend: str, contract: str) -> list[str]:
    """Return the arguments both sides generate a composition with (the runner adds `--output`)."""
    return composition_cell(schema, (), backend, contract).args()


def absent_common_option(inventory: dict[str, list[str] | None]) -> str | None:
    """Return the first option or value of `common_args` that a base's option inventory lacks."""
    return opt_in_reason(common_args(Path("schema.json")), inventory)


def base_backends(inventory: dict[str, list[str] | None]) -> tuple[str, ...]:
    """Return the backends of `REQUESTED` that a base's option inventory has as a value of `BACKEND_OPTION`."""
    return tuple(backend for backend in REQUESTED if opt_in_reason([BACKEND_OPTION, backend], inventory) is None)


@dataclass(frozen=True)
class Example:
    """A composition, one of its backends and one contract."""

    chain: tuple[Level, ...]
    backend: str
    contract: str


def judged_backends(chain: tuple[Level, ...], available: tuple[str, ...]) -> tuple[str, ...]:
    """Return the backends of a composition that the base generates (``available``) and this interpreter can judge.

    TypedDict needs the test suite's capability ``typed-dict-validation`` (Pydantic validates ``typing.TypedDict`` from
    Python 3.12 on).
    """
    lacking = bool(missing(("typed-dict-validation",), expected_environment_facts()))
    return tuple(
        backend for backend in backends(chain) if backend in available and not (lacking and backend == TYPED_DICT)
    )


def examples(available: tuple[str, ...]) -> st.SearchStrategy[Example]:
    """Return the strategy of examples: a composition, then one of the backends it is judged for and a contract.

    A composition with no backend in ``available`` that this interpreter can judge is not drawn.
    """
    return (
        levels()
        .map(lambda chain: (chain, judged_backends(chain, available)))
        .filter(lambda drawn: bool(drawn[1]))
        .flatmap(
            lambda drawn: st.builds(
                Example, st.just(drawn[0]), st.sampled_from(drawn[1]), st.sampled_from(tuple(CONTRACTS))
            )
        )
    )


@dataclass(frozen=True)
class Finding:
    """One example that differs, how, and what shows it: a payload as JSON or an error."""

    kind: str
    example: Example
    witness: str
    detail: str = ""

    @property
    def key(self) -> tuple[str, tuple[str, ...]]:
        """The class a run reports once: the kind and the family of each level."""
        return self.kind, tuple(level.family for level in self.example.chain)

    def record(self, shape: Shape) -> dict[str, object]:
        """Return the finding as JSON data, with the composition's schema."""
        return {
            "kind": self.kind,
            "levels": [{"family": level.family, "params": dict(level.params)} for level in self.example.chain],
            "backend": self.example.backend,
            "contract": self.example.contract,
            "witness": self.witness,
            "detail": self.detail,
            "schema": shape.document,
        }


class _FoundError(Exception):
    """Raised inside a round for a new finding, so that Hypothesis shrinks the example."""

    def __init__(self, finding: Finding) -> None:
        super().__init__(finding.kind)
        self.finding = finding


@dataclass
class Explorer:
    """Judges examples with the candidate in this process and both sides in the gate's warm workers."""

    gate: Gate
    compared: frozenset[str]
    """The contracts BASE has the options of."""
    backends: tuple[str, ...]
    """The backends BASE generates (`base_backends`); the others are not drawn."""
    scratch: Path
    label: str
    """The base as the run names it."""
    counts: Counter[str] = field(default_factory=Counter)

    def close(self) -> None:
        """Stop the gate's workers."""
        for runner in (self.gate.candidate, self.gate.base, self.gate.rerun):
            if runner is not None:
                runner.close()

    def judgeable(self, example: Example) -> bool:
        """Return whether the payload suite finds a seed for an example's composition; count it where it does not."""
        if not (found := bool(value_neighbours(schema_case(compose(example.chain))))):
            self.counts["no seed"] += 1
        return found

    def findings(self, example: Example) -> list[Finding]:
        """Return the findings no rule covers of one judgeable example."""
        shape = compose(example.chain)
        directory = Path(tempfile.mkdtemp(dir=self.scratch))
        try:
            found = self._found(example, shape, directory)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        self.counts["judged"] += 1
        self.counts[f"judged {example.contract}"] += 1
        return [
            finding
            for finding in found
            if finding.kind in COMPARISONS
            or covering(RULES, example.chain, example.backend, example.contract, finding.kind) is None
        ]

    def _found(self, example: Example, shape: Shape, directory: Path) -> list[Finding]:
        schema = directory / "schema.json"
        schema.write_text(json.dumps(shape.document), encoding="utf-8")
        cell = composition_cell(schema, example.chain, example.backend, example.contract)
        candidate = self.gate.candidate.generate(cell.args(), directory)
        base = self.gate.base.generate(cell.args(), directory) if example.contract in self.compared else None
        if self.gate.identity and base is not None:
            return self._identical(example, cell, (base, candidate), directory)
        if not candidate.ok:
            status = "" if base is None else f" (BASE {'generates it' if base.ok else 'fails too'})"
            return [Finding(GENERATION_FAILS, example, candidate.error, f"the candidate fails{status}")]
        judged = judge(shape, example.backend, example.contract, directory / "judge")
        found = [Finding(kind, example, witness) for kind, witness in judged.findings.items()]
        if base is None:
            return found
        return [*self._undeclared(example, shape, cell, (base, candidate), directory), *found]

    def _undeclared(
        self,
        example: Example,
        shape: Shape,
        cell: Cell,
        outcomes: tuple[compare_release.Outcome, compare_release.Outcome],
        directory: Path,
    ) -> list[Finding]:
        """Return the finding of an example whose outcomes differ where no change record covers it, or none.

        It names how they differ: BASE fails, the models decide a payload differently, or neither (`undeclared`).
        """
        self.counts["compared"] += 1
        result = judge_outcomes(self.gate, cell, outcomes, directory)
        if result.judged is None or result.judged.cls == "accepted":
            self.counts["same outcomes" if result.judged is None else "declared"] += 1
            return []
        reasons = "; ".join(result.judged.notes)
        if not (base := outcomes[0]).ok:
            return [Finding(BASE_FAILS, example, base.error, f"BASE fails, the candidate generates it: {reasons}")]
        before, after = (verdicts(directory / side / "model.py", shape, example.backend) for side in ("base", "cand"))
        if (move := moved(shape, before, after)) is not None:
            return [Finding(MOVED, example, move[0], f"{move[1]}: {reasons}")]
        return [Finding(UNDECLARED, example, "; ".join(result.lines[:3]), reasons)]

    def _identical(
        self,
        example: Example,
        cell: Cell,
        outcomes: tuple[compare_release.Outcome, compare_release.Outcome],
        directory: Path,
    ) -> list[Finding]:
        """Return the `differs` finding of an example whose two outcomes differ in any way, or none."""
        self.counts["compared"] += 1
        verdict = judge_outcomes(self.gate, cell, outcomes, directory).verdict
        stopped = verdict.cls == "both-fail" and NOT_COMPARED_STOPPED in verdict.notes
        self.counts["identity not compared" if stopped else f"identity {verdict.cls}"] += 1
        if verdict.cls != "violation":
            return []
        return [Finding(DIFFERS, example, verdict.notes[0], "; ".join(verdict.notes[1:]))]


def report(finding: Finding, out: Path | None, index: int) -> str:
    """Return the text of a finding: the smallest composition, its witness, schema and command."""
    shape = compose(finding.example.chain)
    document = json.dumps(shape.document)
    schema = "composition.json"
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        (target := out / f"finding-{index}.json").write_text(json.dumps(shape.document, indent=2), encoding="utf-8")
        schema = str(target)
    command = ["datamodel-codegen", *generation_args(Path(schema), finding.example.backend, finding.example.contract)]
    lines = [
        f"finding: {finding.kind} ({finding.example.backend}, {finding.example.contract})",
        *(f"    {line}" for line in describe(finding.example.chain).splitlines()),
        f"  checked definition: {shape.name}",
        f"  witness: {finding.witness}",
        *([f"  {finding.detail}"] if finding.detail else []),
        f"  command: {shlex.join(command)}",
        *([] if out is not None else [f"  schema: {document}"]),
    ]
    return "\n".join(lines)


def _round(
    explorer: Explorer, ns: argparse.Namespace, number: int, deadline: float, reported: set[object]
) -> Finding | None:
    """Run round ``number``: return the smallest example of the first new finding it meets, or None.

    Once a round meets a finding, its later examples and its shrinking count only findings of that finding's key, and
    neither the budget nor the example count stops it, so the example Hypothesis shrinks keeps failing alike.
    """
    target: list[tuple[str, tuple[str, ...]]] = []

    @settings(
        database=DirectoryBasedExampleDatabase(ns.database) if ns.database else None,
        deadline=None,
        max_examples=ROUND,
        phases=(Phase.explicit, Phase.reuse, Phase.generate, Phase.shrink),
        suppress_health_check=list(HealthCheck),
        report_multiple_bugs=False,
        print_blob=False,
        verbosity=Verbosity.quiet,
    )
    @seed(ns.seed + number)
    @given(example=examples(explorer.backends))
    def check(example: Example) -> None:
        if not target and (time.monotonic() >= deadline or explorer.counts["judged"] >= ns.examples):
            return
        assume(ns.identity or explorer.judgeable(example))
        new = [
            finding
            for finding in explorer.findings(example)
            if finding.key not in reported and (not target or finding.key == target[0])
        ]
        if new:
            target[:] = [new[0].key]
            raise _FoundError(new[0])

    try:
        check()
    except _FoundError as found:
        return found.finding
    return None


def explore(explorer: Explorer, ns: argparse.Namespace) -> list[Finding]:
    """Draw rounds of examples until the budget or the example count is spent; return the findings, once each."""
    deadline = time.monotonic() + ns.budget
    findings: list[Finding] = []
    number = 0
    while time.monotonic() < deadline and explorer.counts["judged"] < ns.examples:
        if (finding := _round(explorer, ns, number, deadline, {found.key for found in findings})) is not None:
            findings.append(finding)
            print(report(finding, ns.out, len(findings)), flush=True)
        number += 1
    explorer.counts["rounds"] = number
    return findings


class RefusedError(Exception):
    """The base cannot run the tier: the run stops before it draws anything (exit 2)."""


def _latest_tag() -> str:
    return compare_release.git(repository(), "describe", "--tags", "--abbrev=0").stdout.strip()


def base_of(ns: argparse.Namespace) -> str:
    """Return the base a run compares with: a release or revision, or for `--identity` the merge base with HEAD.

    The revisions are the gate's repository's (`behaviour_diff.repository`), this checkout's by default.
    """
    if not ns.identity:
        return ns.base or _latest_tag()
    upstream = ns.base or IDENTITY_UPSTREAM
    if not (merge_base := compare_release.git(repository(), "merge-base", upstream, "HEAD").stdout.strip()):
        msg = f"{upstream} and HEAD have no merge base: fetch the history"
        raise RefusedError(msg)
    return merge_base


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "base",
        nargs="?",
        help="released version or git revision, compared at its merge base with HEAD (default: the latest tag); "
        f"with --identity, the branch whose merge base with HEAD is compared (default: {IDENTITY_UPSTREAM})",
    )
    parser.add_argument(
        "--identity", action="store_true", help="compare with the merge base: any difference of an example is a finding"
    )
    parser.add_argument("--base-python", type=Path, help="interpreter with the base release already installed")
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET, help="seconds of drawing")
    parser.add_argument("--examples", type=int, default=DEFAULT_EXAMPLES, help="stop after judging this many examples")
    parser.add_argument("--seed", type=int, default=0, help="the seed of the first round")
    parser.add_argument("--database", type=Path, help="directory that keeps failing examples between runs")
    parser.add_argument("--json", type=Path, help="write the findings and counts as JSON")
    parser.add_argument("--out", type=Path, help="write each finding's schema into this directory")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "shape-compositions",
        help="where a release base is installed or a revision extracted",
    )
    return parser.parse_args(argv)


def explorer_of(ns: argparse.Namespace, scratch: Path) -> Explorer:
    """Return the explorer of a run: the gate of its base (`behaviour_diff.gate_of`), its contracts and backends.

    Raises `RefusedError` where a change record the gate judges by has a problem or names a surface the latest
    tag lacks (as the gate fails its run, no composition is approved by such a record), where the base lacks an
    option every generation uses or every backend, or, for `--identity`, a contract's options, a merge base with
    HEAD, or `--base-python` (the merge base is built).
    """
    if ns.identity and ns.base_python is not None:
        msg = "--identity compares with the merge base, built by the run: --base-python names another base"
        raise RefusedError(msg)
    base = base_of(ns)
    cache_dir = ns.cache_dir.absolute()
    gate = gate_of([
        base,
        "--timeout",
        str(TIMEOUT),
        "--cache-dir",
        str(cache_dir),
        "--candidate-python",
        str(Path(sys.executable).absolute()),
        *(["--identity"] if ns.identity else []),
        *(["--base-python", str(ns.base_python.absolute())] if ns.base_python is not None else []),
    ])
    inventory = option_inventory(gate.base.side.python, gate.base.env)
    if problems := [*(gate.plan.problems if gate.plan else ()), *plan_surface_problems(gate, inventory, cache_dir)]:
        msg = "the change records have problems:\n" + "\n".join(f"records: {problem}" for problem in problems)
        raise RefusedError(msg)
    label = str(ns.base_python or base)
    if (absent := absent_common_option(inventory)) is not None:
        msg = f"the base {label} has no {absent}, which every generation uses"
        raise RefusedError(msg)
    available = base_backends(inventory)
    for backend in REQUESTED:
        if backend not in available:
            print(f"the base {label} has no {BACKEND_OPTION} {backend}: its examples are skipped", flush=True)
    if not available:
        msg = f"the base {label} has none of the backends {', '.join(REQUESTED)}"
        raise RefusedError(msg)
    compared = frozenset(name for name, options in CONTRACTS.items() if opt_in_reason(list(options), inventory) is None)
    if ns.identity and (lacking := sorted(CONTRACTS.keys() - compared)):
        msg = f"the merge base {label} lacks the options of the contracts {', '.join(lacking)}"
        raise RefusedError(msg)
    return Explorer(gate, compared, available, scratch, label)


def main(argv: list[str] | None = None) -> int:
    """Run the random composition tier of this checkout."""
    ns = _parse_args(argv)
    package = Path(datamodel_code_generator.__file__).resolve().parent
    if not package.is_relative_to(ROOT / "src"):
        print(f"datamodel_code_generator is imported from {package}, not from {ROOT / 'src'}", file=sys.stderr)
        return 2
    return run(ns)


def run(ns: argparse.Namespace) -> int:
    """Run the tier for parsed arguments; return 1 on a finding or on incomplete identity evidence, else 0."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="shape-compositions-") as scratch:
        try:
            explorer = explorer_of(ns, Path(scratch))
        except RefusedError as refused:
            print(refused, file=sys.stderr)
            return 2
        try:
            findings = explore(explorer, ns)
        finally:
            explorer.close()
    elapsed = time.monotonic() - started
    counts = dict(sorted(explorer.counts.items()))
    mode = "identity with the merge base" if ns.identity else "change records judge the differences"
    contracts = ", ".join(sorted(explorer.compared)) or "none"
    print(
        f"base: {explorer.label} ({mode}); contracts compared with it: {contracts}; "
        f"backends drawn: {', '.join(explorer.backends)}\n"
        f"counts: {json.dumps(counts)}\n"
        f"summary: {len(findings)} finding(s) in {elapsed:.0f} s"
    )
    unseen = explorer.gate.evidence.group_problems() if ns.identity else []
    if unseen:
        print(f"not compared: {', '.join(unseen)}")
    if ns.json is not None:
        data = {
            "base": explorer.label,
            "identity": ns.identity,
            "compared": sorted(explorer.compared),
            "backends": list(explorer.backends),
            "counts": counts,
            "seconds": round(elapsed),
            "findings": [finding.record(compose(finding.example.chain)) for finding in findings],
        }
        ns.json.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return 1 if findings or unseen else 0


if __name__ == "__main__":
    sys.exit(main())
