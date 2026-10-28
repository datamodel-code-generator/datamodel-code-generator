"""Generate the conformance dashboard documentation page.

Usage:
    python scripts/build_conformance_docs.py
    python scripts/build_conformance_docs.py --check
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DOCS_PATH = ROOT / "docs" / "conformance.md"
TOX_PATH = ROOT / "tox.ini"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "test.yaml"
JSON_SCHEMA_SUITE_METADATA_PATH = ROOT / "tests" / "main" / "payload_validation" / "json_schema_suite.py"

TOX_COMMAND_RUNNER_RE = re.compile(r"\bpython\s+(?P<script>scripts/run_[\w_]+\.py)\b")
WORKFLOW_JOB_RE = re.compile(r"^  (?P<job_id>[A-Za-z0-9_-]+):\s*$")
WORKFLOW_JOB_NAME_RE = re.compile(r"^    name:\s*(?P<name>.+?)\s*$", re.MULTILINE)
WORKFLOW_CLONE_RE = re.compile(
    r"git clone\b(?P<options>[^\n]*?)\s+(?P<url>https://[^\s]+)\s+(?P<path>\S+)",
)
WORKFLOW_CHECKOUT_RE = re.compile(r"git -C (?P<path>\S+) checkout (?P<ref>[0-9a-f]{40})")
WORKFLOW_CACHE_PATH_RE = re.compile(r"^\s+path:\s*(?P<path>\S+)\s*$", re.MULTILINE)
WORKFLOW_SPARSE_CHECKOUT_RE = re.compile(r"git -C \S+ sparse-checkout set (?P<paths>[^\n]+)")
WORKFLOW_EXPECTED_COUNT_RE = re.compile(r"--expected-(?P<name>[a-z-]+)\s+(?P<count>\d+)")
WORKFLOW_TOX_RUN_RE = re.compile(r"\btox\s+run\b[^\n]*\s-e\s+(?P<tox_env>[A-Za-z0-9_-]+)\b")
WORKFLOW_TOX_INPUT_PATH_RE = re.compile(r"(?P<path>\.tox/[^\s\\]+)")


@dataclass(frozen=True)
class ConformanceSuiteSpec:
    """Manual suite prose that cannot be derived from local metadata."""

    suite_name: str
    job_id: str
    input_scope: str
    source_label: str
    what_it_proves: str

    @property
    def tox_env(self) -> str:
        """The conformance tox env currently matches the workflow job id."""
        return self.job_id


@dataclass(frozen=True)
class WorkflowJobMetadata:
    """Conformance metadata derived from the test workflow."""

    job_id: str
    job_name: str
    clone_url: str | None
    checkout_ref: str | None
    cache_enabled: bool
    sparse_checkout_paths: tuple[str, ...]
    expected_counts: dict[str, int]


@dataclass(frozen=True)
class JsonSchemaSuiteCounts:
    """JSON-Schema-Test-Suite counts derived from pytest metadata."""

    default_drafts: tuple[str, ...]
    group_counts_by_draft: dict[str, int]
    test_counts_by_draft: dict[str, int]


@dataclass(frozen=True)
class ConformanceSuiteRow:
    """Rendered conformance dashboard row."""

    suite_name: str
    input_scope: str
    source_corpus: str
    runner_script: str
    tox_env: str
    ci_job: str
    expected_count: str
    external_checkout: str
    what_it_proves: str


CONFORMANCE_SUITES = (
    ConformanceSuiteSpec(
        suite_name="W3C XML Schema Test Suite",
        job_id="w3c-xmlschema-e2e",
        input_scope="XML Schema (`.xsd`) generation from valid schema documents",
        source_label="w3c/xsdtests",
        what_it_proves=(
            "CI exercises XML Schema generation against valid schema documents in the pinned W3C test suite and "
            "imports the generated Python modules."
        ),
    ),
    ConformanceSuiteSpec(
        suite_name="JSON-Schema-Test-Suite",
        job_id="jsonschema-suite-conformance",
        input_scope="JSON Schema generated Pydantic v2 model validation for configured drafts",
        source_label="json-schema-org/JSON-Schema-Test-Suite",
        what_it_proves=(
            "CI exercises generated Pydantic v2 models against JSON-Schema-Test-Suite expectations for the "
            "configured drafts, with unsupported cases classified in tests."
        ),
    ),
    ConformanceSuiteSpec(
        suite_name="AsyncAPI spec JSON Schemas",
        job_id="asyncapi-spec-json-schemas-e2e",
        input_scope="Stable AsyncAPI specification JSON Schema files used as JSON Schema input",
        source_label="asyncapi/spec-json-schemas",
        what_it_proves=(
            "CI exercises generation and import of Python models from stable AsyncAPI specification JSON Schema files, "
            "including local handling of HTTP references."
        ),
    ),
    ConformanceSuiteSpec(
        suite_name="Apache Avro schema pass corpus",
        job_id="apache-avro-schema-pass-e2e",
        input_scope="Apache Avro schema pass files from the upstream C test corpus",
        source_label="apache/avro",
        what_it_proves=(
            "CI exercises Avro generation against upstream pass-corpus schemas and imports the generated Python module."
        ),
    ),
    ConformanceSuiteSpec(
        suite_name="Protocol Buffers official corpus",
        job_id="protobuf-official-e2e",
        input_scope="Official Protocol Buffers `.proto` files selected by the local runner",
        source_label="protocolbuffers/protobuf",
        what_it_proves=(
            "CI exercises Protocol Buffers generation against supported files in the pinned official corpus subset and "
            "imports the generated Python module."
        ),
    ),
)
CONFORMANCE_TOX_ENVS = frozenset(spec.tox_env for spec in CONFORMANCE_SUITES)


def _literal_assignment(tree: ast.Module, name: str) -> Any:
    """Return a top-level literal assignment from a Python source AST."""
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ):
            return ast.literal_eval(node.value)
    msg = f"Could not find literal assignment {name} in {JSON_SCHEMA_SUITE_METADATA_PATH}"
    raise ValueError(msg)


def load_json_schema_suite_counts(path: Path = JSON_SCHEMA_SUITE_METADATA_PATH) -> JsonSchemaSuiteCounts:
    """Load default JSON-Schema-Test-Suite counts without importing test modules."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return JsonSchemaSuiteCounts(
        default_drafts=tuple(_literal_assignment(tree, "DEFAULT_JSON_SCHEMA_TEST_SUITE_TARGET_DRAFTS")),
        group_counts_by_draft=dict(_literal_assignment(tree, "EXPECTED_JSON_SCHEMA_SUITE_GROUP_COUNTS_BY_DRAFT")),
        test_counts_by_draft=dict(_literal_assignment(tree, "EXPECTED_JSON_SCHEMA_SUITE_TEST_COUNTS_BY_DRAFT")),
    )


def load_tox_runner_scripts(path: Path = TOX_PATH) -> dict[str, str]:
    """Return tox env names mapped to local e2e/conformance runner scripts."""
    parser = ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")

    runner_scripts: dict[str, str] = {}
    for section in parser.sections():
        if not section.startswith("testenv:"):
            continue
        env_name = section.removeprefix("testenv:")
        if (command := parser.get(section, "commands", fallback="")) and (
            match := TOX_COMMAND_RUNNER_RE.search(command)
        ):
            runner_scripts[env_name] = match.group("script")
    return runner_scripts


def discover_conformance_tox_envs(path: Path = TOX_PATH) -> dict[str, str]:
    """Discover current conformance/e2e tox envs backed by local runner scripts."""
    return {
        env_name: script
        for env_name, script in load_tox_runner_scripts(path).items()
        if env_name.endswith("-e2e") or "conformance" in env_name
    }


def _workflow_job_blocks(path: Path) -> dict[str, str]:
    """Split the workflow into job blocks for lightweight metadata extraction."""
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: dict[str, str] = {}
    current_job_id: str | None = None
    current_block: list[str] = []
    in_jobs = False

    for line in lines:
        if not in_jobs:
            if line == "jobs:":
                in_jobs = True
            continue
        if current_job_id is not None and line and not line.startswith((" ", "\t")) and ":" in line:
            blocks[current_job_id] = "\n".join(current_block)
            current_job_id = None
            break
        if match := WORKFLOW_JOB_RE.match(line):
            if current_job_id is not None:
                blocks[current_job_id] = "\n".join(current_block)
            current_job_id = match.group("job_id")
            current_block = [line]
            continue
        if current_job_id is not None:
            current_block.append(line)

    if current_job_id is not None:
        blocks[current_job_id] = "\n".join(current_block)
    return blocks


def _workflow_step_blocks(block: str) -> list[str]:
    """Split a workflow job block into top-level step blocks."""
    steps: list[str] = []
    current_step: list[str] = []

    for line in block.splitlines():
        if line.startswith("      - "):
            if current_step:
                steps.append("\n".join(current_step))
            current_step = [line]
            continue
        if current_step:
            current_step.append(line)

    if current_step:
        steps.append("\n".join(current_step))
    return steps


def _workflow_path_is_inside(path: str | None, base_path: str | None) -> bool:
    """Return whether a workflow path is the base path or below it."""
    if path is None or base_path is None:
        return False
    return path == base_path or path.startswith(f"{base_path}/")


def _workflow_tox_input_path(step: str) -> str | None:
    """Return the first .tox input path from a tox run step."""
    if match := WORKFLOW_TOX_INPUT_PATH_RE.search(step):
        return match.group("path")
    return None


def _workflow_clone_step(steps: list[str], input_path: str | None, end_index: int) -> str:
    """Find the nearest preceding clone step for a tox run input path."""
    if input_path is None:
        return ""

    for index in range(end_index - 1, -1, -1):
        step = steps[index]
        if (match := WORKFLOW_CLONE_RE.search(step)) and _workflow_path_is_inside(input_path, match.group("path")):
            return step
    return ""


def _workflow_cache_enabled(steps: list[str], input_path: str | None, end_index: int) -> bool:
    """Return whether a preceding cache step covers a tox run input path."""
    if input_path is None:
        return False

    for index in range(end_index):
        step = steps[index]
        if (
            "uses: actions/cache" in step
            and (match := WORKFLOW_CACHE_PATH_RE.search(step))
            and _workflow_path_is_inside(input_path, match.group("path"))
        ):
            return True
    return False


def _workflow_suite_jobs(job_id: str, job_name: str, steps: list[str]) -> dict[str, WorkflowJobMetadata]:
    """Build suite-keyed metadata from consolidated e2e workflow steps."""
    suite_jobs: dict[str, WorkflowJobMetadata] = {}

    for index, step in enumerate(steps):
        if not (tox_match := WORKFLOW_TOX_RUN_RE.search(step)):
            continue
        if (tox_env := tox_match.group("tox_env")) not in CONFORMANCE_TOX_ENVS:
            continue

        input_path = _workflow_tox_input_path(step)
        end_index = index + 1
        clone_step = _workflow_clone_step(steps, input_path, end_index)

        clone_url: str | None = None
        if match := WORKFLOW_CLONE_RE.search(clone_step):
            clone_url = match.group("url")

        checkout_ref: str | None = None
        if match := WORKFLOW_CHECKOUT_RE.search(clone_step):
            checkout_ref = match.group("ref")

        sparse_checkout_paths: tuple[str, ...] = ()
        if match := WORKFLOW_SPARSE_CHECKOUT_RE.search(clone_step):
            sparse_checkout_paths = tuple(match.group("paths").split())

        suite_jobs[tox_env] = WorkflowJobMetadata(
            job_id=job_id,
            job_name=job_name,
            clone_url=clone_url,
            checkout_ref=checkout_ref,
            cache_enabled=_workflow_cache_enabled(steps, input_path, end_index),
            sparse_checkout_paths=sparse_checkout_paths,
            expected_counts={
                match.group("name"): int(match.group("count")) for match in WORKFLOW_EXPECTED_COUNT_RE.finditer(step)
            },
        )

    return suite_jobs


def load_workflow_jobs(path: Path = WORKFLOW_PATH) -> dict[str, WorkflowJobMetadata]:
    """Load workflow job metadata needed by the conformance dashboard."""
    jobs: dict[str, WorkflowJobMetadata] = {}
    for job_id, block in _workflow_job_blocks(path).items():
        job_name = job_id
        if match := WORKFLOW_JOB_NAME_RE.search(block):
            job_name = match.group("name").strip("\"'")
        steps = _workflow_step_blocks(block)

        clone_url: str | None = None
        if match := WORKFLOW_CLONE_RE.search(block):
            clone_url = match.group("url")

        checkout_ref: str | None = None
        if match := WORKFLOW_CHECKOUT_RE.search(block):
            checkout_ref = match.group("ref")

        sparse_checkout_paths: tuple[str, ...] = ()
        if match := WORKFLOW_SPARSE_CHECKOUT_RE.search(block):
            sparse_checkout_paths = tuple(match.group("paths").split())

        expected_counts = {
            match.group("name"): int(match.group("count")) for match in WORKFLOW_EXPECTED_COUNT_RE.finditer(block)
        }
        jobs[job_id] = WorkflowJobMetadata(
            job_id=job_id,
            job_name=job_name,
            clone_url=clone_url,
            checkout_ref=checkout_ref,
            cache_enabled="uses: actions/cache" in block,
            sparse_checkout_paths=sparse_checkout_paths,
            expected_counts=expected_counts,
        )
        jobs.update(_workflow_suite_jobs(job_id, job_name, steps))
    return jobs


def _normalize_repo_url(url: str | None) -> str | None:
    """Normalize git clone URLs for Markdown links."""
    if url is None:
        return None
    return url.removesuffix(".git")


def _expected_count_label(name: str) -> str:
    """Return a stable human label for a workflow --expected-* argument."""
    label = name.replace("-", " ")
    match name:
        case "documents":
            label = "valid schemaDocument entries"
        case "unique-documents":
            label = "unique valid schemaDocument paths"
        case "schemas":
            label = "schemas"
    return label


def _format_workflow_expected_counts(expected_counts: dict[str, int]) -> str:
    """Format expected counts parsed from the workflow run command."""
    if not expected_counts:
        return "Not set in workflow command"
    return "; ".join(f"{count:,} {_expected_count_label(name)}" for name, count in expected_counts.items())


def _format_json_schema_expected_counts(counts: JsonSchemaSuiteCounts) -> str:
    """Format JSON-Schema-Test-Suite counts that pytest asserts for default drafts."""
    groups = sum(counts.group_counts_by_draft[draft] for draft in counts.default_drafts)
    tests = sum(counts.test_counts_by_draft[draft] for draft in counts.default_drafts)
    drafts = ", ".join(f"`{draft}`" for draft in counts.default_drafts)
    return f"{groups:,} groups / {tests:,} tests for default drafts {drafts} (asserted by pytest)"


def _source_corpus(spec: ConformanceSuiteSpec, job: WorkflowJobMetadata) -> str:
    """Format the upstream source corpus from workflow checkout metadata."""
    repo_url = _normalize_repo_url(job.clone_url)
    if repo_url is None:
        return f"{spec.source_label} (workflow checkout not detected)"

    checkout_ref = f" @ `{job.checkout_ref}`" if job.checkout_ref else ""
    sparse_paths = ""
    if job.sparse_checkout_paths:
        paths = ", ".join(f"`{path}`" for path in job.sparse_checkout_paths)
        sparse_paths = f"; sparse checkout: {paths}"
    return f"[{spec.source_label}]({repo_url}){checkout_ref}{sparse_paths}"


def _external_checkout(job: WorkflowJobMetadata) -> str:
    """Describe whether CI needs an external checkout or network access."""
    if job.clone_url is None:
        return "No external checkout detected"
    if job.cache_enabled:
        return "Yes; cached external checkout, network on cache miss"
    return "Yes; external checkout cloned during the CI job"


def _expected_count(spec: ConformanceSuiteSpec, job: WorkflowJobMetadata) -> str:
    """Format the suite's expected corpus count."""
    if spec.job_id == "jsonschema-suite-conformance":
        return _format_json_schema_expected_counts(load_json_schema_suite_counts())
    return _format_workflow_expected_counts(job.expected_counts)


def collect_conformance_rows() -> list[ConformanceSuiteRow]:
    """Collect generated conformance dashboard rows from local metadata."""
    tox_runner_scripts = load_tox_runner_scripts()
    workflow_jobs = load_workflow_jobs()
    rows: list[ConformanceSuiteRow] = []

    for spec in CONFORMANCE_SUITES:
        if not (runner_script := tox_runner_scripts.get(spec.tox_env)):
            msg = f"Missing tox env or runner script for {spec.tox_env}"
            raise ValueError(msg)
        if not (job := workflow_jobs.get(spec.job_id)):
            msg = f"Missing workflow job {spec.job_id}"
            raise ValueError(msg)
        runner_path = ROOT / runner_script
        if not runner_path.is_file():
            msg = f"Runner script does not exist: {runner_script}"
            raise ValueError(msg)

        rows.append(
            ConformanceSuiteRow(
                suite_name=spec.suite_name,
                input_scope=spec.input_scope,
                source_corpus=_source_corpus(spec, job),
                runner_script=f"`{runner_script}`",
                tox_env=f"`{spec.tox_env}`",
                ci_job=f"`{job.job_name}` (`{job.job_id}`)",
                expected_count=_expected_count(spec, job),
                external_checkout=_external_checkout(job),
                what_it_proves=spec.what_it_proves,
            ),
        )
    return rows


def _markdown_cell(value: str) -> str:
    """Escape Markdown table cell delimiters."""
    return value.replace("|", r"\|").replace("\n", "<br>")


def _markdown_table(rows: list[ConformanceSuiteRow]) -> str:
    """Render the suite summary table."""
    headers = [
        "Suite name",
        "Input format / scope",
        "Source corpus or upstream project",
        "Local runner script",
        "tox environment",
        "CI job name",
        "Expected corpus size/count",
        "External checkout/network in CI",
        "What the suite proves",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _header in headers) + " |",
    ]
    lines.extend(
        "| "
        + " | ".join(
            _markdown_cell(value)
            for value in (
                row.suite_name,
                row.input_scope,
                row.source_corpus,
                row.runner_script,
                row.tox_env,
                row.ci_job,
                row.expected_count,
                row.external_checkout,
                row.what_it_proves,
            )
        )
        + " |"
        for row in rows
    )
    return "\n".join(lines)


def render_conformance_markdown() -> str:
    """Render the complete conformance dashboard page."""
    rows = collect_conformance_rows()
    lines = [
        "# Conformance Dashboard",
        "",
        "<!-- Generated by scripts/build_conformance_docs.py. Do not edit manually. -->",
        "",
        (
            "This page summarizes the external conformance and end-to-end corpus checks that CI runs for "
            "datamodel-code-generator. The table is generated from `tox.ini`, `.github/workflows/test.yaml`, local "
            "runner scripts, and JSON-Schema-Test-Suite pytest metadata."
        ),
        "",
        (
            "These suites are compatibility and coverage signals. They show that CI exercises datamodel-code-generator "
            "against pinned upstream corpora; they do not claim complete specification compliance."
        ),
        "",
        _markdown_table(rows),
        "",
        "## Generated Sources",
        "",
        "- tox environments and runner script paths are derived from `tox.ini`.",
        (
            "- CI job names, upstream checkout pins, cache usage, and workflow `--expected-*` arguments are derived "
            "from `.github/workflows/test.yaml`."
        ),
        (
            "- JSON-Schema-Test-Suite default draft counts are derived from "
            "`tests/main/payload_validation/json_schema_suite.py` because the workflow delegates count assertions to "
            "pytest."
        ),
        "",
    ]
    return "\n".join(lines)


def build_docs(*, check: bool) -> int:
    """Generate or check the conformance dashboard documentation page."""
    content = render_conformance_markdown().rstrip() + "\n"

    if check:
        if not DOCS_PATH.exists() or DOCS_PATH.read_text(encoding="utf-8").rstrip() + "\n" != content:
            print("Conformance docs are out of date.", file=sys.stderr)
            print("Run 'python scripts/build_conformance_docs.py' to update.", file=sys.stderr)
            return 1
        return 0

    DOCS_PATH.write_text(content, encoding="utf-8")
    return 0


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Build conformance dashboard documentation")
    parser.add_argument("--check", action="store_true", help="Check whether docs/conformance.md is up to date")
    return parser.parse_args()


def main() -> int:
    """Script entrypoint."""
    args = parse_args()
    return build_docs(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
