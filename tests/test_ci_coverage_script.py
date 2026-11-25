"""Exercise CI coverage recording and validation through the CLI without mocks."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import threading
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO, StringIO
from pathlib import Path

import pytest
from coverage import CoverageData

from scripts import ci_coverage, coverage_exclusions
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests/data/ci_coverage"
CASES = json.loads((DATA / "cases.json").read_text(encoding="utf-8"))
MODULE = """import sys


def sign(value):
    if value < 0:
        return -1
    return 1


print(sign(int(sys.argv[1])), sign(int(sys.argv[2])))
"""
EXCLUDED_MODULE = MODULE.replace("if value < 0:", "if value < 0:  # pragma: no cover").replace(
    "print(", "def never():  # pragma: no cover\n    return 0\n\n\nprint("
)
NESTED_MODULE = """import sys


def never():  # pragma: no cover
    return 0  # pragma: no cover


def clamp(value):  # pragma: no cover
    if value > 5:  # pragma: no cover
        value = 5
    return value


print(clamp(int(sys.argv[1])), clamp(int(sys.argv[2])))
"""
DECORATED_MODULE = """import functools
import sys


@functools.cache  # pragma: no cover
def never(value):
    return value


@functools.cache  # pragma: no cover
def called(value):
    return value


class Shape:
    @property  # pragma: no cover
    def area(self):
        return 0


print(called(int(sys.argv[1])), Shape)
"""
MULTI_LINE_MODULE = MODULE.replace(
    "    if value < 0:", "    (  # pragma: no cover\n        abs(value)\n    )\n    if value < 0:"
)
CLAUSES_MODULE = """import sys


def describe(value):
    match value:  # pragma: no cover
        case 0:
            result = 0
        case int():  # pragma: no cover
            result = value
    try:  # pragma: no cover
        result = 1 // value
    except ZeroDivisionError:  # pragma: no cover
        result = 0
    else:  # pragma: no cover
        result += 1
    finally:  # pragma: no cover
        result *= 2
    with open(__file__, encoding="utf-8"):  # pragma: no cover
        pass
    for _ in ():  # pragma: no cover
        result = 0
    else:  # pragma: no cover
        result += 0
    if value > 5:  # pragma: no cover
        result = 5
    elif value > 3:  # pragma: no cover
        result = 3
    else:  # pragma: no cover
        result -= 0
    while result > 100:  # pragma: no cover
        result = 0
    return result


print(describe(int(sys.argv[1])), describe(int(sys.argv[2])))
"""
MODULES = {
    "report-executed-exclusion": EXCLUDED_MODULE,
    "report-stale-data": EXCLUDED_MODULE,
    "absolute-path": EXCLUDED_MODULE,
    "nested-regions": NESTED_MODULE,
    "decorated-uncalled": DECORATED_MODULE,
    "multi-line-statement": MULTI_LINE_MODULE,
    "clause-kinds": CLAUSES_MODULE,
}
"""The module each case records, `MODULE` for the others."""


def _report_payload(case: str, tmp_path: Path) -> bytes:
    """Record real coverage data of a module staged in a git project whose `base` branch lacks it."""
    git = ["git", "-c", "user.name=CI", "-c", "user.email=ci@example.com", "-c", "commit.gpgsign=false"]
    relative = "false" if case == "absolute-path" else "true"
    (tmp_path / ".coveragerc").write_text(
        f"[run]\nbranch = true\nrelative_files = {relative}\nsource = .\n", encoding="utf-8"
    )
    for command in (["init", "-q", "-b", "base"], ["add", ".coveragerc"], ["commit", "-q", "-m", "base"]):
        subprocess.run([*git, *command], cwd=tmp_path, check=True)
    module = "src/datamodel_code_generator/parser/sign.py" if case == "absolute-path" else "sign.py"
    (tmp_path / module).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / module).write_text(MODULES.get(case, MODULE), encoding="utf-8")
    subprocess.run([*git, "add", module], cwd=tmp_path, check=True)
    data = tmp_path / "seed.coverage"
    values = ("1", "1") if case == "report-missing-lines" else ("1", "-1")
    subprocess.run(
        [sys.executable, "-m", "coverage", "run", f"--data-file={data}", module, *values],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    payload = data.read_bytes()
    data.unlink()
    return payload


def _prepare_case(case: str, tmp_path: Path) -> list[str]:  # ruff: ignore[too-many-branches]
    """Materialize each artifact fault using actual files."""
    root = tmp_path / "inputs"
    root.mkdir()
    report = case.startswith("report-")
    payload = _report_payload(case, tmp_path) if report else (DATA / "coverage.txt").read_bytes()
    for name in sorted(ci_coverage.EXPECTED_NAMES):
        directory = root / name
        directory.mkdir()
        path = directory / name
        path.write_bytes(payload)
        ci_coverage.main(["record", str(path), "--run-id", "123", "--sha", "abc"])
    name = min(ci_coverage.EXPECTED_NAMES)
    directory = root / name
    path = directory / name
    metadata = directory / f"{name}.json"
    action = "prepare"
    target = root
    match case:
        case "missing":
            path.unlink()
            metadata.unlink()
            directory.rmdir()
        case "unexpected":
            (root / "extra").mkdir()
        case "duplicate":
            (directory / "duplicate").write_bytes(payload)
        case "empty":
            path.write_bytes(b"")
        case "wrong-run" | "wrong-sha" | "wrong-name":
            data = json.loads(metadata.read_text(encoding="utf-8"))
            data[{"wrong-run": "run_id", "wrong-sha": "sha", "wrong-name": "name"}[case]] = "foreign"
            metadata.write_text(json.dumps(data), encoding="utf-8")
        case "invalid-json":
            metadata.write_text("{", encoding="utf-8")
        case "artifact-file":
            path.unlink()
            metadata.unlink()
            directory.rmdir()
            directory.touch()
        case "data-directory":
            path.unlink()
            path.mkdir()
        case "stale-destination":
            (tmp_path / ".coverage.stale").touch()
        case "missing-root":
            target = root / "missing"
        case "record-empty" | "record-missing" | "record-unknown":
            action = "record"
            target = path
            match case:
                case "record-empty":
                    path.write_bytes(b"")
                case "record-missing":
                    path.unlink()
                case _:
                    target = directory / "unknown"
                    target.write_bytes(payload)
    if report:
        ci_coverage.main(["prepare", str(root), "--run-id", "123", "--sha", "abc"])
        match case:
            case "report-no-source":
                (tmp_path / "sign.py").unlink()
            case "report-missing-input":
                (tmp_path / name).unlink()
            case "report-stale-data":
                (tmp_path / name).unlink()
                (tmp_path / ".coverage").write_bytes(payload)
        return ["report", ".", "--compare-branch", "base"]
    return [action, str(target), "--run-id", "123", "--sha", "abc"]


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("subprocess_cli", [False, True], ids=["entrypoint", "subprocess"])
def test_coverage_artifact_cli(
    case: str, subprocess_cli: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject incomplete or foreign artifacts before exposing any combine inputs, then combine and report them."""
    args = _prepare_case(case, tmp_path)
    monkeypatch.chdir(tmp_path)
    if subprocess_cli:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/ci_coverage.py"), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        code, stdout, stderr = result.returncode, result.stdout, result.stderr
    else:
        out, err = StringIO(), StringIO()
        code = 0
        with redirect_stdout(out), redirect_stderr(err):
            try:
                ci_coverage.main(args)
            except SystemExit as error:
                code = error.code
        stdout, stderr = out.getvalue(), err.getvalue()
    # OSError wording differs by platform; retain the failure type and verify no files moved.
    if case == "missing-root":
        stderr = "missing input directory\n" if "missing" in stderr else stderr
    output = (
        json.dumps(
            {
                "code": code,
                "stdout": stdout,
                "stderr": stderr,
                "prepared": sorted(p.name for p in tmp_path.glob(".coverage.*")),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    assert_output(output)


@pytest.mark.parametrize(
    "case",
    [
        "report-executed-exclusion",
        "report-no-source",
        "nested-regions",
        "decorated-uncalled",
        "multi-line-statement",
        "absolute-path",
        "clause-kinds",
    ],
)
def test_executed_exclusions(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """List each excluded region whose body the recorded run entered, skipping a source file that is gone.

    A region keeps the nested ones inside its suite, a header that runs without entering the suite (an imported
    decorated definition, a clause line) does not count, a statement counts from any of its lines, and the file and
    area are named relative to the project whether the data stores the path absolute or relative.
    """
    (tmp_path / ".coverage").write_bytes(_report_payload(case, tmp_path))
    if case == "report-no-source":
        (tmp_path / "sign.py").unlink()
    monkeypatch.chdir(tmp_path)
    coverage_exclusions.main([".coverage"])
    assert_output(capsys.readouterr().out)


LATE_FILES = {
    "combined": {"late-tracker": ("-1", "1"), "late-forkserver": ("1", "1")},
    "no-leftovers": {},
    "unreadable-leftover": {"late-tracker": ("-1", "1"), "truncated": None},
    "only-unreadable": {"truncated": None},
    "unknown-name": {"late-tracker": ("-1", "1")},
}
"""The files each case leaves beside the run's data file: a run of `MODULE` with its arguments, or a broken file."""


@pytest.mark.parametrize("case", LATE_FILES)
def test_collect_combines_the_files_written_after_the_tests(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The data files a run's late processes leave beside its data file join it before it takes the artifact's name.

    A file coverage cannot read stays, and the command fails rather than leave a stray file in the artifact.
    """
    (tmp_path / "sign.py").write_text(MODULE, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    data = ".coverage.py314-parallel"
    for name, values in {data: ("1", "1"), **{f"{data}.{late}": run for late, run in LATE_FILES[case].items()}}.items():
        if values is None:
            Path(name).write_bytes(b"not a data file")
            continue
        command = [sys.executable, "-m", "coverage", "run", "--branch", f"--data-file={name}", "sign.py", *values]
        subprocess.run(command, capture_output=True, check=True)
    target = ".coverage.unknown" if case == "unknown-name" else ".coverage.py314-parallel-shard9-ubuntu-24.04"
    out, err = StringIO(), StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            ci_coverage.main(["collect", data, target])
        except SystemExit as error:
            code = error.code
    lines = {}
    if Path(target).is_file():
        stored = CoverageData(target)
        stored.read()
        lines = {
            Path(name).name: sorted(stored.lines(name) or [])
            for name in stored.measured_files()
            if Path(name).name == "sign.py"
        }
    payload = {
        "code": code,
        # Coverage's verdict on each file, without the paths of its warnings, which differ by machine.
        "stdout": [
            line.partition(": ")[0]
            for line in out.getvalue().splitlines()
            if line.startswith(("Combined", "Couldn't combine", "No usable"))
        ],
        "stderr": err.getvalue(),
        "files": sorted(path.name for path in tmp_path.glob(".coverage*")),
        "lines": lines,
    }
    assert_output(json.dumps(payload, sort_keys=True, indent=2) + "\n")


class _ArtifactServer(ThreadingHTTPServer):
    """GitHub's artifact API for one run: paged listing, a zip URL that redirects, blob storage refusing a token."""

    def __init__(self, artifacts: list[dict[str, object]], blobs: dict[int, bytes], failures: dict[int, int]) -> None:
        super().__init__(("127.0.0.1", 0), _ArtifactHandler)
        self.artifacts, self.blobs, self.failures = artifacts, blobs, failures


class _ArtifactHandler(BaseHTTPRequestHandler):
    server: _ArtifactServer

    def log_message(self, *_: object) -> None:
        """Keep the test output free of the request log."""

    def _send(self, status: int, body: bytes = b"", location: str = "") -> None:
        self.send_response(status)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        authorized = self.headers.get("Authorization") == "Bearer token"
        if path == "/repos/owner/repo/actions/runs/7/artifacts" and authorized:
            page = int(query.rpartition("page=")[2])
            self._send(200, json.dumps({"artifacts": self.server.artifacts[(page - 1) * 100 : page * 100]}).encode())
        elif (match := re.fullmatch(r"/repos/owner/repo/actions/artifacts/(\d+)/zip", path)) and authorized:
            self._send(302, location=f"/blob/{match[1]}")
        elif (match := re.fullmatch(r"/blob/(\d+)", path)) and "Authorization" not in self.headers:
            artifact = int(match[1])
            if self.server.failures.get(artifact, 0):
                self.server.failures[artifact] -= 1
                self._send(500)
            else:
                self._send(200, self.server.blobs[artifact])
        else:
            self._send(401 if path.startswith("/repos/") else 403)


DOWNLOAD_CASES = ("complete", "flaky", "unavailable", "digest-mismatch", "no-token")
"""`flaky` fails an artifact's first blob request, `unavailable` all three attempts."""


def _artifact_server(case: str, staged: Path) -> _ArtifactServer:
    """Serve every coverage artifact a push run uploads, with the case's fault, beside other listed artifacts.

    The listing spans pages and also holds artifacts of another kind, an expired one and an older attempt of a name.
    """
    artifacts: list[dict[str, object]] = []
    blobs: dict[int, bytes] = {}
    for number, name in enumerate(sorted(ci_coverage.EXPECTED_NAMES), 10):
        path = staged / name
        path.write_bytes((DATA / "coverage.txt").read_bytes())
        ci_coverage.main(["record", str(path), "--run-id", "7", "--sha", "abc"])
        archive = BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.write(path, name)
            zipped.write(staged / f"{name}.json", f"{name}.json")
        blobs[number] = archive.getvalue()
        digest = hashlib.sha256(blobs[number]).hexdigest()
        artifacts.append({"id": number, "name": name, "expired": False, "digest": f"sha256:{digest}"})
    stale = {**artifacts[0], "id": 1, "digest": "sha256:older"}
    expired = {"id": 500, "name": ".coverage.expired", "expired": True, "digest": "sha256:none"}
    # A run also uploads an artifact of another kind per job, so the listing spans pages whatever the coverage count.
    others = [
        {
            "id": 501 + shard,
            "name": f"junit.py314-parallel-shard{shard}-ubuntu-24.04",
            "expired": False,
            "digest": "sha256:none",
        }
        for shard in range(1, ci_coverage.PAGE_SIZE + 1)
    ]
    if case == "digest-mismatch":
        artifacts[5]["digest"] = "sha256:foreign"
    failures = {13: {"flaky": 1, "unavailable": 3}.get(case, 0)}
    return _ArtifactServer([*artifacts[:60], stale, expired, *others, *artifacts[60:]], blobs, failures)


@pytest.mark.parametrize("case", DOWNLOAD_CASES)
def test_download_unpacks_the_artifacts_prepare_reads(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Download the newest unexpired coverage artifact of each name of a run, checked by its digest, then prepare them.

    The zip URL redirects to blob storage, which refuses the token.
    """
    (staged := tmp_path / "staged").mkdir()
    server = _artifact_server(case, staged)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    if case != "no-token":
        monkeypatch.setenv(ci_coverage.TOKEN_ENV, "token")
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.chdir(tmp_path)
    out, err = StringIO(), StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            api = f"http://127.0.0.1:{server.server_port}"
            ci_coverage.main(["download", "inputs", "--repository", "owner/repo", "--run-id", "7", "--api-url", api])
            ci_coverage.main(["prepare", "inputs", "--run-id", "7", "--sha", "abc"])
        except SystemExit as error:
            code = error.code
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)
    payload = {
        "code": code,
        "stdout": out.getvalue(),
        "stderr": err.getvalue(),
        "prepared": len(list(tmp_path.glob(".coverage.*"))),
    }
    assert_output(json.dumps(payload, sort_keys=True, indent=2) + "\n")
