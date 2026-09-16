"""Keep CI matrix coverage and gate behavior consistent while sharing steps."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from scripts.ci_coverage import EXPECTED_NAMES
from scripts.select_ci_test_shard import PROFILES
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ROOT / "tests/data/expected/ci_coverage"


def test_workflow_matrix_and_coverage_contract() -> None:
    """Changing dependency groups must preserve every existing test environment."""
    workflow = yaml.load((ROOT / ".github/workflows/test.yaml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    jobs = workflow["jobs"]
    groups = {name: job["strategy"]["matrix"]["include"] for name, job in jobs.items() if "strategy" in job}
    names = []
    producers = []
    for name, entries in groups.items():
        for entry in entries:
            if entry.get("coverage", "true") != "true":
                continue
            producers.append(name)
            env = entry.get("coverage_name", entry["tox_env"])
            shard = f"-shard{entry['shard']}" if "shard" in entry else ""
            names.append(f".coverage.{env}{shard}-{entry.get('os', 'ubuntu-24.04')}")
    configurations = sorted(
        f"{entry['tox_env']}:{entry.get('os', 'ubuntu-24.04')}:{entry.get('shard', '')}/{entry.get('shard_total', '')}"
        f":{entry.get('profile', '')}"
        for entries in groups.values()
        for entry in entries
    )
    shard_entries = [entry for entries in groups.values() for entry in entries if "shard" in entry]
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
        "shard_profiles_known": all(entry.get("profile") in PROFILES for entry in shard_entries),
        "shard_profiles_used": sorted({entry["profile"] for entry in shard_entries}),
        "coverage_count": len(names),
        "coverage_unique": len(set(names)),
        "expected_names_match": set(names) == EXPECTED_NAMES,
        "coverage_needs_only_producers": set(jobs["coverage"]["needs"]) == set(producers),
        "gate_needs_all_jobs": set(jobs["test-gate"]["needs"]) == set(jobs) - {"test-gate"},
        "gate_always": jobs["test-gate"]["if"],
        "concurrency": workflow["concurrency"],
        "triggers": workflow["on"],
        "permissions": workflow["permissions"],
        "cache_paths": sorted({step["with"]["cache-local-path"] for step in all_setup_steps}),
        "cache_dependencies": sorted({step["with"]["cache-dependency-glob"] for step in all_setup_steps}),
        "shared_shard_steps": jobs["test-shard"]["steps"] is jobs["test-shard-nocov"]["steps"],
        "shared_http_steps": jobs["test-http-backends"]["steps"] is jobs["test-http-backends-nocov"]["steps"],
        "checkout_credentials": sorted({
            step["with"]["persist-credentials"]
            for job in jobs.values()
            for step in job["steps"]
            if "checkout@" in step.get("uses", "")
        }),
    }
    assert_output(json.dumps(output, sort_keys=True, indent=2) + "\n", EXPECTED / "workflow.txt")


def test_gate_rejects_every_unsuccessful_group() -> None:
    """Execute the actual workflow gate for each group's failure, skip and cancellation."""
    jobs = yaml.load((ROOT / ".github/workflows/test.yaml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)["jobs"]
    gate = jobs["test-gate"]
    success = {name: {"result": "success"} for name in gate["needs"]}
    cases = {"success": success, "empty": {}}
    cases.update({
        f"{name}:{state}": {**success, name: {"result": state}}
        for name in success
        for state in ("failure", "cancelled", "skipped")
    })
    outputs = {}
    for name, needs in cases.items():
        result = subprocess.run(
            [sys.executable, "-c", gate["steps"][0]["run"]],
            env={**os.environ, "NEEDS": json.dumps(needs)},
            capture_output=True,
            text=True,
            check=False,
        )
        outputs[name] = {
            "exit": result.returncode,
            "results_match": json.loads(result.stdout) == {group: value["result"] for group, value in needs.items()},
            "stderr": result.stderr,
        }
    assert_output(json.dumps(outputs, sort_keys=True, indent=2) + "\n", EXPECTED / "gate.txt")
