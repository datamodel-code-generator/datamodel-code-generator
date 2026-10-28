"""Tests for the decision-home checker."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import check_decision_homes
from tests.conftest import assert_output

ROOT = Path(__file__).parents[1]
FIXTURE_ROOT = Path(__file__).parent / "data" / "decision_homes"
FIXTURE_SOURCE = FIXTURE_ROOT / "src"
FIXTURE_CONFIG = (FIXTURE_ROOT / "homes.toml").read_text(encoding="utf-8")
FIXTURE_DOCS = FIXTURE_ROOT / "architecture.md"
ALLOWED = """
[allow."other.py"."Other.method"]
"sample.read-flag" = { home = "sample", count = 2, audit = "X-2" }
"sample.write-size" = { home = "sample", count = 2, audit = "X-3" }
"sample.compute-call" = { home = "sample", count = 2, audit = "X-4" }
"sample.null-test" = { home = "sample", count = 2, audit = "X-6" }

[allow."other.py"."_merge_values"]
"sample.merge-function" = { home = "sample", count = 1, audit = "X-5" }

[allow."other.py"."_merge_values._merge_inner"]
"sample.merge-function" = { home = "sample", count = 1, audit = "X-5" }

[allow."other.py"."<module>"]
"sample.limit" = { home = "sample", count = 3, audit = "X-7" }
"sample.keyword-table" = { home = "sample", count = 2, audit = "X-8" }

[allow."other.py"."other_helper"]
"sample.keyword-table" = { home = "sample", count = 1, audit = "X-8" }

[allow."other.py"."read_keys"]
"sample.read-key" = { home = "sample", count = 4, audit = "X-9" }

[allow."adapter.py"."Adapter.read_again"]
"sample.read-flag" = { home = "sample", count = 1, audit = "X-1" }
"""
MALFORMED = """
[allow."other.py"."Other.method"]
"missing.signature" = { home = "sample", count = 1, audit = "X-1" }
"""
FIXTURE_ARGS = ["--source-root", str(FIXTURE_SOURCE), "--docs", str(FIXTURE_DOCS)]


def _scan_report(config_text: str) -> str:
    config = check_decision_homes.parse_config(config_text)
    scan = check_decision_homes.scan_tree(config, FIXTURE_SOURCE)
    problems = [
        *check_decision_homes.table_problems(config, FIXTURE_SOURCE),
        *check_decision_homes.apply_allowlist(config, scan, FIXTURE_SOURCE),
    ]
    return check_decision_homes.format_report(problems)


def test_source_tree_keeps_decisions_in_their_homes() -> None:
    """Every decision is decided in its home, or at a site the allowlist names, and every legacy row is planned."""
    problems, _ = check_decision_homes.check_all()

    assert_output(check_decision_homes.format_report(problems))


def test_decision_home_detector_reports_every_signature_kind() -> None:
    """Report each kind of signature outside the core and the adapters, and none inside them."""
    assert_output(_scan_report(FIXTURE_CONFIG))


def test_decision_home_allowlist_accepts_known_sites_only() -> None:
    """Accept the sites an allowlist counts and report the ones beyond it."""
    lowered = ALLOWED.replace("count = 3", "count = 2").replace(
        '"sample.null-test" = { home = "sample", count = 2', '"sample.null-test" = { home = "sample", count = 1'
    )

    assert_output(_scan_report(FIXTURE_CONFIG + ALLOWED), name="complete")
    assert_output(_scan_report(FIXTURE_CONFIG + lowered), name="beyond")


def test_decision_home_function_scoped_paths_check_that_function() -> None:
    """A signature whose `paths` names a function reports the hits in that function and its nested functions."""
    scoped = FIXTURE_CONFIG.replace('paths = ["other.py", "core.py"]', 'paths = ["other.py:_merge_values"]')
    assert_output(_scan_report(scoped))


def test_decision_home_planned_places_cover_hits_until_they_exist() -> None:
    """A planned core covers its hits and need not exist; once it exists it must move to the core."""
    later = """
[homes.later]
decision = "a decision whose core a later change creates"
planned = ["future.py", "core.py:decide"]

[homes.later.signatures.merge-function]
kind = "function"
pattern = "^_merge"
paths = ["future.py", "other.py", "missing.py"]
what = "defines a merge function"
audit = "X-5"
"""
    assert_output(_scan_report(FIXTURE_CONFIG + later))


def test_decision_home_key_signature_skips_assigned_and_deleted_keys() -> None:
    """A key signature counts the keys a function reads, not the subscripts it assigns or deletes."""
    keys_only = """
[homes.keys]
decision = "the keys of a raw document"
core = ["core.py"]

[homes.keys.signatures.read-key]
kind = "key"
keys = ["type", "items"]
paths = ["other.py:read_keys"]
what = "reads a key of a raw document"
audit = "X-9"
"""
    assert_output(_scan_report(keys_only))


def test_decision_home_signature_paths_outside_planned_places_must_exist() -> None:
    """A signature path is exempt from the existence check only where it names a planned place or its file."""
    misspelled = """
[homes.later]
decision = "a decision whose core a later change creates"
planned = ["future.py", "other.py:later_core"]

[homes.later.signatures.merge-function]
kind = "function"
pattern = "^_merge_nothing"
paths = ["future.py:anything", "future.py", "other.py:later_core.inner", "other.py:later_cor", "core.py:decid"]
what = "defines a merge function"
audit = "X-5"
"""
    assert_output(_scan_report(misspelled))


def test_decision_home_allowlist_reports_stale_entries() -> None:
    """Require an entry to go when its site was routed to the home, lowered or renamed."""
    stale = (
        ALLOWED.replace('count = 2, audit = "X-2"', 'count = 3, audit = "X-2"')
        + """
[allow."gone.py"."Gone.method"]
"sample.read-flag" = { home = "sample", count = 1, audit = "X-1" }

[allow."other.py"."Other.method_renamed"]
"sample.write-size" = { home = "other", count = 1, audit = "X-3" }

[allow."other.py"."Other.zero"]
"missing.signature" = { home = "sample", count = 0, audit = "X-9" }
"""
    )

    assert_output(_scan_report(FIXTURE_CONFIG + stale))


def test_decision_home_legacy_rows_are_named_planned_and_documented() -> None:
    """Fail for a row on one side of src, the toml and the page only, and for an unplanned row."""
    config = check_decision_homes.parse_config(FIXTURE_CONFIG)
    problems = check_decision_homes.legacy_row_problems(
        config, FIXTURE_DOCS.read_text(encoding="utf-8"), FIXTURE_SOURCE
    )

    assert_output(check_decision_homes.format_report(problems))


HOME = '[homes.a]\ndecision = "x"\ncore = ["a.py"]\n[homes.a.signatures.s]\n'


@pytest.mark.parametrize(
    "text",
    [
        '[homes.a]\ndecision = "x"\ncore = []\n',
        HOME + 'kind = "regex"\naudit = "X"\n',
        HOME + 'kind = "name"\nnames = ["x"]\n',
        HOME + 'kind = "name"\nnames = ["x"]\naudit = "X"\nctx = "no"\n',
        HOME + 'kind = "call"\naudit = "X"\npaths = "a"\n',
        HOME + 'kind = "number"\naudit = "X"\npowers = ["63"]\n',
        HOME + 'kind = "key"\naudit = "X"\n',
        '[allow."a.py"."f"]\n"a.s" = 3\n',
    ],
    ids=[
        "no-core",
        "unknown-kind",
        "no-audit",
        "unknown-context",
        "paths-not-a-list",
        "powers-not-integers",
        "key-without-keys",
        "entry-not-a-table",
    ],
)
def test_decision_home_table_rejects_malformed_entries(text: str) -> None:
    """Name the table and the key that is wrong."""
    with pytest.raises(check_decision_homes.ConfigError) as error:
        check_decision_homes.parse_config(text)

    assert_output(f"{error.value}\n")


def test_decision_home_allowlist_is_written_from_the_scan_and_only_lowered(tmp_path: Path) -> None:
    """Seed the allowlist once, lower it after a site was routed, and refuse to add an entry."""
    config_path = tmp_path / "decision_homes.toml"
    config_path.write_text(FIXTURE_CONFIG, encoding="utf-8")
    empty = check_decision_homes.parse_config(FIXTURE_CONFIG)
    scan = check_decision_homes.scan_tree(empty, FIXTURE_SOURCE)
    check_decision_homes.write_allowlist(empty, scan, seed=True, path=config_path, source_root=FIXTURE_SOURCE)
    seeded = config_path.read_text(encoding="utf-8")

    routed = check_decision_homes.Scan(
        hits=[hit for hit in scan.hits if hit.signature != "sample.null-test"][:-1], scanned=scan.scanned
    )
    check_decision_homes.write_allowlist(
        check_decision_homes.parse_config(seeded), routed, seed=False, path=config_path, source_root=FIXTURE_SOURCE
    )
    lowered = config_path.read_text(encoding="utf-8")

    grown = check_decision_homes.write_allowlist(
        check_decision_homes.parse_config(lowered), scan, seed=False, path=config_path, source_root=FIXTURE_SOURCE
    )
    unchanged = config_path.read_text(encoding="utf-8") == lowered

    assert_output(seeded.split(check_decision_homes.ALLOWLIST_MARKER)[1], name="seeded")
    assert_output(lowered.split(check_decision_homes.ALLOWLIST_MARKER)[1], name="lowered")
    assert_output("".join(f"{key}\n" for key in grown) + f"unchanged: {unchanged}\n", name="refused")
    sizes = check_decision_homes.allowlist_sizes(check_decision_homes.parse_config(seeded))
    assert_output(
        "".join(f"{home}: {entries} entries, {hits} hits\n" for home, (entries, hits) in sizes.items()), name="sizes"
    )


def test_decision_home_cli_passes_on_the_source_tree(capsys: pytest.CaptureFixture[str]) -> None:
    """Pass on the source tree and print the allowlist size per home."""
    clean_code = check_decision_homes.main([])
    clean_output = capsys.readouterr()
    sizes_code = check_decision_homes.main(["--sizes"])
    sizes_output = capsys.readouterr()

    assert_output(
        f"clean exit: {clean_code}\nclean stdout: {clean_output.out!r}\nclean stderr: {clean_output.err!r}\n"
        f"sizes exit: {sizes_code}, printed: {bool(sizes_output.out)}\n"
    )


def test_decision_home_cli_reports_and_writes_the_allowlist(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Report a fixture tree, refuse to grow an allowlist, seed an empty one once and lower it."""
    config_path = tmp_path / "decision_homes.toml"
    fixture_args = ["--config", str(config_path), *FIXTURE_ARGS]
    outputs = []
    for allowlist, flags in (
        ("", []),
        ("", ["--write-allowlist"]),
        ("", ["--seed"]),
        (ALLOWED, ["--seed"]),
        (ALLOWED, ["--write-allowlist"]),
        (MALFORMED, ["--write-allowlist"]),
    ):
        config_path.write_text(FIXTURE_CONFIG + allowlist, encoding="utf-8")
        code = check_decision_homes.main([*fixture_args, *flags])
        outputs.append(f"{' '.join(flags)} exit: {code}\n{capsys.readouterr().err}")

    assert_output("".join(outputs))


def test_decision_home_cli_checks_the_full_source_tree() -> None:
    """Run the checker as a real process, as a pre-commit hook would."""
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_decision_homes.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert_output(f"exit: {completed.returncode}\nstdout: {completed.stdout!r}\nstderr:\n{completed.stderr}")
