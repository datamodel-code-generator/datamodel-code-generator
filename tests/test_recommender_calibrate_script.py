"""Exercise the recommender calibration script end to end on a local corpus, without network access.

The specs are served from tests/data over file:// URLs, the recommender is the installed `--recommend-options`, and
`--check-upstream` asks local bare git repositories for their HEAD.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from scripts import recommender_calibrate
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/data/recommender_calibrate"
SHA256_PLACEHOLDER = re.compile(r"@SHA256:([^@]+)@")
GIT_IDENTITY = {
    f"GIT_{role}_{key}": value
    for role in ("AUTHOR", "COMMITTER")
    for key, value in (("NAME", "calibration"), ("EMAIL", "calibration@example.com"), ("DATE", "2026-01-01T00:00Z"))
}


def _manifest(tmp_path: Path, replacements: tuple[tuple[str, str], ...] = ()) -> Path:
    text = (FIXTURES / "corpus.toml").read_text(encoding="utf-8")
    for old, new in replacements:
        text = text.replace(old, new)
    text = text.replace("@FIXTURES@", FIXTURES.as_uri())
    text = SHA256_PLACEHOLDER.sub(lambda match: hashlib.sha256((FIXTURES / match[1]).read_bytes()).hexdigest(), text)
    (manifest := tmp_path / "corpus.toml").write_text(text, encoding="utf-8")
    return manifest


def _run(args: list[str], tmp_path: Path, out: Path | None = None, placeholders: dict[str, str] | None = None) -> str:
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = recommender_calibrate.main([*args, "--cache-dir", str(tmp_path / "cache")])
    sections = [f"exit={code}", "--- stdout", stdout.getvalue(), "--- stderr", stderr.getvalue()]
    for file in sorted(out.iterdir()) if out and out.exists() else ():
        sections += [f"--- {file.name}", file.read_text(encoding="utf-8")]
    text = "\n".join(sections)
    for path in sorted(FIXTURES.glob("v*/*"), key=lambda path: path.as_posix()):
        text = text.replace(
            hashlib.sha256(path.read_bytes()).hexdigest(), f"<sha256 of {path.parent.name}/{path.name}>"
        )
    for old, new in (placeholders or {}).items():
        text = text.replace(old, new)
    return text


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        input="",
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **GIT_IDENTITY},
    )
    return completed.stdout.strip()


def _upstream(path: Path, *, commit: bool) -> str:
    """Create a bare repository standing in for a schema's source; return its HEAD commit, if it has one."""
    _git("init", "--bare", "--quiet", str(path))
    if not commit:
        return ""
    head = _git("--git-dir", str(path), "commit-tree", _git("--git-dir", str(path), "mktree"), "-m", "upstream")
    _git("--git-dir", str(path), "update-ref", "HEAD", head)
    return head


def test_corpus_manifest() -> None:
    """The shipped corpus manifest validates offline; its summary pins every entry."""
    stdout = StringIO()
    with redirect_stdout(stdout):
        code = recommender_calibrate.main(["--check-manifest"])
    assert_output(f"exit={code}\n{stdout.getvalue()}")


def test_calibration_run(tmp_path: Path) -> None:
    """A full run writes the report, scores, proposed calibration and rules diff; a second fetch hits the cache.

    `broken`'s recommendation fails (exit 1). --check-upstream proposes rotating `pets` and `config`, whose source
    HEAD moved past the newest pin, keeps `shop`, pinned at its source's HEAD, and reports that `broken`'s source has
    no HEAD. The target is the oldest Python version CI runs, since --import-check imports with this interpreter.
    """
    moved = tmp_path / "moved.git"
    head = _upstream(moved, commit=True)
    _upstream(empty := tmp_path / "empty.git", commit=False)
    pinned = tmp_path / "pinned"
    (pinned / head).mkdir(parents=True)
    shutil.copyfile(FIXTURES / "v1/shop.yaml", pinned / head / "shop.yaml")
    manifest = _manifest(
        tmp_path,
        (
            ('"https://example.com/pets"', f'"{moved.as_uri()}"'),
            ('"https://example.com/config"', f'"{moved.as_uri()}"'),
            ('"https://example.com/shop"', f'"{moved.as_uri()}"'),
            ('"https://example.com/broken"', f'"{empty.as_uri()}"'),
            ('"@FIXTURES@/{ref}/shop.yaml"', f'"{pinned.as_uri()}/{{ref}}/shop.yaml"'),
            ('{ ref = "v1", date = "2026-01-15"', f'{{ ref = "{head}", date = "2026-01-15"'),
        ),
    )
    out = tmp_path / "out"
    common = ["--manifest", str(manifest), "--only", "pets", "shop", "config", "broken"]
    placeholders = {head: "<upstream head>", head[:12]: "<upstream head:12>"}
    first = _run(
        [
            *common,
            "--out",
            str(out),
            "--import-check",
            "--check-upstream",
            "--target-python-version",
            "3.10",
            "--calibration",
            str(FIXTURES / "calibration.json"),
        ],
        tmp_path,
        out,
        placeholders,
    )
    second = _run([*common, "--fetch-only"], tmp_path, placeholders=placeholders)
    assert_output(f"{first}\n=== second run\n{second}")


@pytest.mark.parametrize(
    ("replacements", "args", "writes"),
    [
        pytest.param(
            (("@SHA256:v2/pets.json@", "0" * 64),), ["--fetch-only", "--only", "pets"], False, id="sha256_mismatch"
        ),
        pytest.param(
            (("stub_external_refs = true", "stub_external_refs = false"),), ["--fetch-only"], False, id="external_ref"
        ),
        pytest.param(
            (
                ('kind = "jsonschema"', 'kind = "graphql"'),
                ('"https://example.com/shop"', '"http://example.com/shop"'),
                ('date = "2026-02-01"', 'date = "2025-12-31"'),
                ('{ ref = "v1", date = "2026-01-15"', '{ ref = "v1", tag = "x", date = "2026-01-15"'),
                ("format_version = 1", "format_version = 1\nextra = true"),
            ),
            ["--fetch-only"],
            False,
            id="invalid_manifest",
        ),
        pytest.param((), ["--fetch-only", "--only", "pets", "nope"], False, id="unknown_id"),
        pytest.param((), ["--check-manifest", "--check-upstream"], False, id="upstream_without_out"),
        pytest.param(
            (("title-source = [", "title-source = [{ use_subclass_enum = false }, "),),
            [
                *("--only", "shop", "config", "--import-check", "--target-python-version", "3.10"),
                *("--calibration", str(FIXTURES / "none.json")),
            ],
            True,
            id="inexpressible",
        ),
        pytest.param(
            (
                (
                    'naming-strategy = [{}, { naming_strategy = "full-path" }]',
                    'naming-strategy = [{}, { naming_strategy = "numbered" }]',
                ),
            ),
            ["--only", "enums", "--calibration", str(FIXTURES / "none.json")],
            True,
            id="ratio_threshold",
        ),
        pytest.param(
            (),
            ["--only", "pets", "config", "--jobs", "2", "--calibration", str(FIXTURES / "calibration.json")],
            True,
            id="partial_parallel",
        ),
    ],
)
def test_calibration_cases(
    replacements: tuple[tuple[str, str], ...], args: list[str], writes: bool, tmp_path: Path
) -> None:
    """Pinned digests, the $ref guard and manifest validation fail closed; other modes and grid values.

    --check-upstream needs --out. A grid value no option expresses is rejected, and a preset proposal that generates
    and imports on every schema is proposed. A ratio threshold of 3/7 is proposed rounded down so that the schema
    still fires. Recommender processes give the same scores, a proposal waits for --import-check, and a run of some
    schemas keeps the other schemas' entries of the last calibration.
    """
    out = tmp_path / "out"
    output = _run(
        ["--manifest", str(_manifest(tmp_path, replacements)), *args, *(["--out", str(out)] if writes else [])],
        tmp_path,
        out,
    )
    assert_output(output)
