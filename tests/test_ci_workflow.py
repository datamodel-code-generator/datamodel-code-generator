"""Keep CI matrix coverage and gate behavior consistent while sharing steps."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts._source_model import SourceModule, bases
from scripts.ci_coverage import EXPECTED_NAMES, PULL_REQUEST_NAMES
from scripts.select_ci_test_shard import PROFILES
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
MAIN_ONLY_IF = "github.event_name != 'pull_request'"
TEST_TARGET = re.compile(r"(?<![\w/.\-])(?:\./)?tests/[\w/.\-]*(?:::[\w:]+)?")
ANY_MEMBER = "::*"
MISSING_TARGETS_MESSAGE = (
    "tox.ini or a workflow names a test path or node id the repository lacks, so the lane that runs it collects "
    "nothing and fails: rename it in tox.ini or .github/ (both code-owned) with the test."
)


def test_workflow_matrix_and_coverage_contract() -> None:
    """Changing dependency groups must preserve every existing test environment."""
    workflow = yaml.load((ROOT / ".github/workflows/test.yaml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    jobs = workflow["jobs"]
    groups = {name: job["strategy"]["matrix"]["include"] for name, job in jobs.items() if "strategy" in job}
    names = []
    main_only_names = set()
    producers = []
    for name, entries in groups.items():
        for entry in entries:
            if entry.get("coverage", "true") != "true":
                continue
            producers.append(name)
            env = entry.get("coverage_name", entry["tox_env"])
            shard = f"-shard{entry['shard']}" if "shard" in entry else ""
            names.append(f".coverage.{env}{shard}-{entry.get('os', 'ubuntu-24.04')}")
            if jobs[name].get("if") == MAIN_ONLY_IF:
                main_only_names.add(names[-1])
    main_only_jobs = sorted(name for name, job in jobs.items() if job.get("if") == MAIN_ONLY_IF)
    configurations = sorted(
        f"{entry['tox_env']}:{entry.get('os', 'ubuntu-24.04')}:{entry.get('shard', '')}/{entry.get('shard_total', '')}"
        f":{entry.get('profile', '')}"
        for entries in groups.values()
        for entry in entries
    )
    shard_entries = [entry for name in ("test-shard", "test-shard-main", "test-shard-nocov") for entry in groups[name]]
    shard_groups: dict[tuple[str, str, str, str], list[tuple[int, int]]] = {}
    for name, entries in groups.items():
        for entry in entries:
            if "shard" in entry:
                key = (name, entry["tox_env"], entry["os"], entry.get("profile", ""))
                shard_groups.setdefault(key, []).append((int(entry["shard"]), int(entry["shard_total"])))
    all_setup_steps = [step for job in jobs.values() for step in job["steps"] if "setup-uv@" in step.get("uses", "")]
    output = {
        "configurations": sorted(configurations),
        "job_count": len(configurations) + sum("strategy" not in job for job in jobs.values()),
        "extra_tests": sorted(
            f"{entry['tox_env']}:shard{entry['shard']}:{entry['extra_tests']}"
            for entries in groups.values()
            for entry in entries
            if entry.get("extra_tests")
        ),
        "shard_profile_mapping": jobs["test-shard"]["env"]["SHARD_PROFILE"],
        "shards_complete": all(
            sorted(shards) == [(index, len(shards)) for index in range(1, len(shards) + 1)]
            for shards in shard_groups.values()
        ),
        "shard_profiles_known": all(entry.get("profile") in PROFILES for entry in shard_entries),
        "exclude_tests": sorted({entry["exclude_tests"] for entry in shard_entries if entry.get("exclude_tests")}),
        "exclude_only_without_coverage": all(
            bool(entry.get("exclude_tests")) == (entry["coverage"] == "false") for entry in shard_entries
        ),
        "shard_profiles_used": sorted({entry["profile"] for entry in shard_entries}),
        "coverage_count": len(names),
        "coverage_unique": len(set(names)),
        "expected_names_match": set(names) == EXPECTED_NAMES,
        "main_only_names_match": main_only_names == EXPECTED_NAMES - PULL_REQUEST_NAMES,
        "main_only_jobs": main_only_jobs,
        "gate_main_only_jobs_match": jobs["test-gate"]["steps"][0]["env"]["MAIN_ONLY_JOBS"]
        == f"${{{{ github.event_name == 'pull_request' && '{' '.join(main_only_jobs)}' || '' }}}}",
        "coverage_needs_only_producers": set(jobs["coverage"]["needs"]) == set(producers),
        "coverage_if": jobs["coverage"]["if"],
        "coverage_if_reads_every_need": all(f"needs.{name}.result" in jobs["coverage"]["if"] for name in producers),
        "gate_needs_all_jobs": set(jobs["test-gate"]["needs"]) == set(jobs) - {"test-gate"},
        "gate_always": jobs["test-gate"]["if"],
        "concurrency": workflow["concurrency"],
        "triggers": workflow["on"],
        "permissions": workflow["permissions"],
        "cache_paths": sorted({step["with"]["cache-local-path"] for step in all_setup_steps}),
        "cache_dependencies": sorted({step["with"]["cache-dependency-glob"] for step in all_setup_steps}),
        "shared_shard_steps": all(
            jobs[name]["steps"] is jobs["test-shard"]["steps"]
            for name in ("test-shard-main", "test-shard-nocov", "test-shard-nocov-main")
        ),
        "shared_http_steps": jobs["test-http-backends"]["steps"] is jobs["test-http-backends-nocov"]["steps"],
        "checkout_credentials": sorted({
            step["with"]["persist-credentials"]
            for job in jobs.values()
            for step in job["steps"]
            if "checkout@" in step.get("uses", "")
        }),
    }
    assert_output(json.dumps(output, sort_keys=True, indent=2) + "\n")


def test_gate_rejects_every_unsuccessful_group() -> None:
    """Execute the actual workflow gate for each group's failure, skip and cancellation, including mid-stack runs."""
    jobs = yaml.load((ROOT / ".github/workflows/test.yaml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)["jobs"]
    gate = jobs["test-gate"]
    success = {name: {"result": "success"} for name in gate["needs"]}
    cases = {"success": success, "empty": {}}
    cases.update({
        f"{name}:{state}": {**success, name: {"result": state}}
        for name in success
        for state in ("failure", "cancelled", "skipped")
    })
    always_on = gate["steps"][0]["env"].get("ALWAYS_ON_JOBS", "").split()
    skipped = {name: {"result": "success" if name in always_on else "skipped"} for name in gate["needs"]}
    mid_stack_cases = {"mid-stack:skipped": skipped, "mid-stack:empty": {}}
    mid_stack_cases.update({
        f"mid-stack:{name}:{state}": {**skipped, name: {"result": state}}
        for name in skipped
        for state in ("success", "failure", "cancelled", "skipped")
        if state != skipped[name]["result"]
    })
    main_only_jobs = [name for name in gate["needs"] if jobs[name].get("if") == MAIN_ONLY_IF]
    pull_request_cases = {
        f"pull-request:{name}:{state}": {
            **success,
            **{job: {"result": "skipped"} for job in main_only_jobs},
            name: {"result": state},
        }
        for name in success
        for state in ("success", "failure", "cancelled", "skipped")
    }
    full_runs = {
        **dict.fromkeys(cases, "true"),
        **dict.fromkeys(mid_stack_cases, "false"),
        **dict.fromkeys(pull_request_cases, "true"),
    }
    cases.update(mid_stack_cases)
    cases.update(pull_request_cases)
    outputs = {}
    for name, needs in cases.items():
        result = subprocess.run(
            [sys.executable, "-c", gate["steps"][0]["run"]],
            env={
                **os.environ,
                "NEEDS": json.dumps(needs),
                "FULL_TEST_RUN": full_runs[name],
                "MAIN_ONLY_JOBS": " ".join(main_only_jobs) if name in pull_request_cases else "",
                "ALWAYS_ON_JOBS": " ".join(always_on),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        outputs[name] = {
            "exit": result.returncode,
            "results_match": json.loads(result.stdout) == {group: value["result"] for group, value in needs.items()},
            "stderr": result.stderr,
        }
    assert_output(json.dumps(outputs, sort_keys=True, indent=2) + "\n")


def defined_tests(model: SourceModule) -> set[str]:
    """Return the node ids below a test module's path that its source defines, read through the source model.

    Each module-level name, each class and function nested in classes (``Class::method``), and each test a class
    inherits from a class of the module. A class with a base from elsewhere may inherit any name: it gives
    ``Class::*``.
    """
    module = model.module_scope
    found = set(module.bindings)
    classes = {
        scope.qualname: scope for scope in model.scopes if scope.kind == "class" and "<locals>" not in scope.qualname
    }
    found |= {
        scope.qualname.replace(".", "::")
        for scope in model.scopes
        if scope.kind == "function" and "<locals>" not in scope.qualname
    }
    found |= {name.replace(".", "::") for name in classes}
    for name, scope in classes.items():
        pending, seen = [scope], set()
        while pending:
            current = pending.pop()
            seen.add(current.qualname)
            found |= {f"{name.replace('.', '::')}::{member}" for member in model.class_members(current, spelled=True)}
            for base in bases(current):
                origin = model.origin(base) or ""
                inner = classes.get(origin.removeprefix(f"{model.module}."))
                if inner is not None and origin.startswith(f"{model.module}.") and inner.qualname not in seen:
                    pending.append(inner)
                elif inner is None and origin != "builtins.object":
                    found.add(f"{name.replace('.', '::')}{ANY_MEMBER}")
    return found


def missing_test_targets(root: Path, files: dict[str, str]) -> list[str]:
    """Return every path and node id below ``tests/`` that the given files (name and text) name and ``root`` lacks."""
    models: dict[Path, set[str]] = {}
    found = []
    for file, text in files.items():
        for target in sorted(set(TEST_TARGET.findall(text))):
            path, _, node = target.removeprefix("./").partition("::")
            where = root / path
            if not where.exists():
                found.append(f"{file}: {path} does not exist")
                continue
            if not node:
                continue
            if where not in models:
                dotted = ".".join(Path(path).with_suffix("").parts)
                models[where] = defined_tests(SourceModule(where.read_text(encoding="utf-8"), path, dotted))
            open_classes = {name.removesuffix(ANY_MEMBER) for name in models[where] if name.endswith(ANY_MEMBER)}
            if node not in models[where] and node.rpartition("::")[0] not in open_classes:
                found.append(f"{file}: {path} defines no {node}")
    return found


def _ci_files() -> dict[str, str]:
    paths = [ROOT / "tox.ini", *sorted((ROOT / ".github").rglob("*.y*ml"))]
    return {path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8") for path in paths}


@pytest.mark.allow_direct_assert("a missing target fails on its own, never through a stored output a fix run accepts")
def test_ci_files_name_only_existing_tests() -> None:
    """Every test path and node id that tox.ini and the workflows name exists; a pinned lane never runs nothing."""
    missing = missing_test_targets(ROOT, _ci_files())
    assert not missing, MISSING_TARGETS_MESSAGE
    assert_output(
        "Test targets that CI files name and the repository lacks:\n"
        + "".join(f"{line}\n" for line in missing or ["none"])
    )


@pytest.mark.allow_direct_assert("the seeded rename must be found, not only stored")
def test_a_renamed_test_id_is_missing() -> None:
    """A renamed node id and a path that is gone are reported; existing ones (parametrized too) are not."""
    listed = {
        "tox.ini": "commands = pytest tests/test_ci_workflow.py::test_ci_files_name_only_existing_tests_renamed "
        "tests/test_ci_workflow.py::test_ci_files_name_only_existing_tests "
        "tests/parser/test_jsonschema.py::test_schema_validator_helpers_disabled[x] tests/gone.py\n"
    }
    missing = missing_test_targets(ROOT, listed)
    assert "tox.ini: tests/test_ci_workflow.py defines no test_ci_files_name_only_existing_tests_renamed" in missing
    assert_output("".join(f"{line}\n" for line in missing))


@pytest.mark.allow_direct_assert("each spelling of a path must decide its target, not only stored")
def test_a_dot_slash_path_is_read_like_a_plain_one() -> None:
    """A target spelled with a leading ``./`` is checked, and a path inside ``.tox`` or ``../`` is not a target."""
    listed = {
        "tox.ini": "commands = pytest ./tests/test_ci_workflow.py::test_ci_files_name_only_existing_tests_renamed "
        "./tests/gone.py ./tests/test_ci_workflow.py::test_ci_files_name_only_existing_tests "
        ".tox/py/tests/never.py ../tests/never.py {toxinidir}/tests/never.py\n"
    }

    assert missing_test_targets(ROOT, listed) == [
        "tox.ini: tests/gone.py does not exist",
        "tox.ini: tests/test_ci_workflow.py defines no test_ci_files_name_only_existing_tests_renamed",
    ]


@pytest.mark.allow_direct_assert("each inheritance shape must decide its target, not only stored")
def test_inherited_tests_are_defined(tmp_path: Path) -> None:
    """Methods a class inherits from classes of its module are its tests; a base from elsewhere may give any name."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_shapes.py").write_text(
        "from elsewhere import Mixin\n\n\nclass Base:\n    def test_a(self): ...\n\n\n"
        "class Left(Base): ...\n\n\nclass Right(Base, object): ...\n\n\nclass Both(Left, Right): ...\n\n\n"
        "class Open(Mixin): ...\n",
        encoding="utf-8",
    )
    listed = {
        "tox.ini": " ".join(
            f"tests/test_shapes.py::{node}"
            for node in ("Both::test_a", "Right::test_a", "Both::test_b", "Open::test_anything", "Base::test_b")
        )
    }
    missing = missing_test_targets(tmp_path, listed)
    assert missing == [
        "tox.ini: tests/test_shapes.py defines no Base::test_b",
        "tox.ini: tests/test_shapes.py defines no Both::test_b",
    ]
    assert_output("".join(f"{line}\n" for line in missing))
