"""Tests for the upgrade guide: the change list, the pages and release notes built from it, and the release stamp.

The registries are fixtures here, so each derived kind (warning, scheduled change, removal, experimental feature,
dated preset) shows up; the committed guide is checked against the registries of the source tree.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from datamodel_code_generator.deprecations import Deprecation
from datamodel_code_generator.experimental import ExperimentalFeature
from scripts.build_migration_docs import PresetDate, Registries, main, read_surface
from tests.capabilities import needs
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_migration_docs.py"
SCRIPT_JS = ROOT / "docs" / "assets" / "migration" / "migration.js"

REGISTRIES = Registries(
    deprecations=(
        Deprecation(
            id="cli.validation",
            kind="cli-option",
            target="--validation",
            message="--validation is deprecated.",
            warning_since="0.2.0",
            removal_version="0.4.0",
            replacement="Use --field-constraints.",
        ),
        Deprecation(
            id="python-api.python-version-has-type-alias",
            kind="python-api",
            target="PythonVersion.has_type_alias",
            message="has_type_alias is deprecated.",
            warning_since="0.3.0",
            removal_version=None,
        ),
        Deprecation(
            id="dependency.argcomplete-optional",
            kind="dependency",
            target="argcomplete installation",
            message="argcomplete will become optional.",
            warning_since="0.5.0",
            removal_version=None,
            status="scheduled",
            warning_category="FutureWarning",
        ),
        Deprecation(
            id="cli.allow-extra-fields",
            kind="cli-option",
            target="--allow-extra-fields",
            message="--allow-extra-fields is deprecated.",
            warning_since="0.0.9",
            removal_version=None,
        ),
    ),
    experimental=(
        ExperimentalFeature(
            id="input-format.avro",
            kind="input-format",
            target="--input-file-type avro",
            message="Avro input is experimental.",
            since_version="0.3.0",
        ),
        ExperimentalFeature(
            id="input-format.toml",
            kind="input-format",
            target="--input-file-type toml",
            message="TOML input is experimental.",
            since_version="0.5.0",
        ),
    ),
    preset_dates=(
        PresetDate("20260101", None, ()),
        PresetDate(
            "20260201", "20260101", ("- Python 3.15 targets", "- Strict types (`--strict-types str`): Be strict.")
        ),
        PresetDate("20260301", "20260201", ("- Snake case (`--snake-case-field`): Snake case field names.",)),
    ),
    surface={
        "options": {
            "--output-model-type": {"aliases": [], "choices": ["a", "b", "c"], "help": "Output model type."},
            "--preset": {"aliases": [], "choices": ["p1", "p2"], "help": "A dated preset."},
            "--new-flag": {"aliases": ["--no-new-flag"], "help": "Turn on new things.", "choices": None},
            "--quiet-flag": {"aliases": [], "help": "", "choices": None},
        },
        "python_api": ["generate"],
        "generate_config": ["output"],
        "templates": ["Kept.jinja2"],
        "presets": {
            "20260101": {"standard-py313-20260101": "a1", "practical-py313-20260101": "b1"},
            "20260201": {"standard-py313-20260201": "a2"},
            "20260301": {"practical-py314-20260301": "b3"},
        },
        "recommender_rules": {"digest": "r2", "revision": "2026-03-01"},
        "experimental": {"input-format.avro": "0.3.0", "input-format.toml": "0.5.0"},
    },
)
RELEASED_SURFACE = {
    "options": {
        "--old-flag": {"aliases": [], "choices": None},
        "--output-model-type": {"aliases": [], "choices": ["a", "b"]},
        "--preset": {"aliases": [], "choices": ["p1"]},
    },
    "python_api": ["generate"],
    "generate_config": ["output"],
    "templates": ["Kept.jinja2", "Old.jinja2"],
    "presets": {
        "20260101": {"standard-py313-20260101": "a1", "practical-py313-20260101": "b1"},
        "20260201": {"standard-py313-20260201": "a2"},
    },
    "recommender_rules": {"digest": "r1", "revision": "2026-02-01"},
    "experimental": {"input-format.avro": "0.3.0"},
}
"""The surface of 0.4.0: the rules changed since, with a new revision, and 20260301 is the one unreleased date."""
RELEASES = [("0.1.0", "2026-01-01"), ("0.2.0", "2026-02-01"), ("0.3.0", "2026-03-01"), ("0.4.0", "2026-04-01")]
DERIVED = '[presets]\n"20260101" = "0.2.0"\n"20260201" = "0.3.0"\n\n[options]\n"--released-flag" = "0.3.0"\n'
CELLS_TABLE = """
[[change.cells]]
input = ["tests/data/jsonschema/template.json"]
backends = "*"
options = "*"
match = "fields"
"""
"""A `[[change.cells]]` table of a decision record."""
CHANGES = {
    "0.2.0": """
[[change]]
id = "specialized-enum-default"
kind = "default-output"
action = "keep"
title = "String enums become `StrEnum`"
summary = "Enums of strings subclass `StrEnum` instead of `Enum`."
affects = { input_file_types = ["openapi", "jsonschema"] }
before = "class Status(Enum):"
after = "class Status(StrEnum):"
keep = { flags = ["--no-use-specialized-enum"] }
refs = ["#2512"]
source = "release-note"

[[change]]
id = "union-item-suffix-removed"
kind = "default-output"
action = "review"
title = "Models of `anyOf` members lose the `Item` suffix"
summary = "Models of `anyOf` and `oneOf` members no longer end in `Item`."
keep = { note = "No option restores the old names; `--model-name-map` can pin individual names." }
refs = ["#1682"]
source = "release-note"

[[change]]
id = "python-37-runtime-dropped"
kind = "runtime"
action = "required"
title = "datamodel-code-generator no longer runs on Python 3.7"
summary = "Install and run the generator on Python 3.8 or newer."
migrate = "Use Python 3.8 or newer."
refs = ["#2060"]
source = "release-note"
""",
    "0.3.0": """
[[change]]
id = "aware-datetime"
kind = "default-output"
action = "keep"
title = "`format: date-time` becomes `AwareDatetime`"
summary = "A timestamp without a UTC offset is rejected."
affects = { output_model_types = ["pydantic_v2.BaseModel"] }
keep = { flags = ["--output-datetime-class", "datetime"] }
refs = ["#1735"]
source = "release-note"

[[change]]
id = "annotated-constraints"
kind = "default-output"
action = "keep"
title = "Constraints move into `Annotated`"
summary = "With `--use-annotated`, constraints are written in `Annotated`."
affects = { options = ["--use-annotated"] }
keep = { flags = ["--field-constraints"] }
refs = ["#3001"]
source = "release-note"

[[change]]
id = "dynamic-models-unformatted"
kind = "python-api"
action = "keep"
title = "`generate_dynamic_models()` no longer formats the code it builds"
summary = "It skips Black and isort unless formatters are selected."
affects = { python_api = ["generate_dynamic_models"] }
keep = { note = "Pass `formatters=[Formatter.BLACK, Formatter.ISORT]` in the config." }
refs = ["#6750"]
source = "release-note"

[[change]]
id = "class-docstrings"
kind = "output-text"
action = "none"
title = "Class docstrings are wrapped"
summary = "Long class docstrings are wrapped at the line length."
refs = ["#3002"]
source = "release-note"

[[change]]
id = "python-38-runtime-dropped"
kind = "runtime"
action = "required"
title = "datamodel-code-generator no longer runs on Python 3.8"
summary = "Install and run the generator on Python 3.9 or newer."
migrate = "Use Python 3.9 or newer."
supersedes = ["python-37-runtime-dropped"]
refs = ["#3003"]
source = "release-note"
""",
    "0.4.0": """
[[change]]
id = "removal:cli.validation"
title = "`--validation` is removed"

[[change]]
id = "docstrings-unwrapped"
kind = "output-text"
action = "none"
title = "Class docstrings are no longer wrapped"
summary = "The wrapping of 0.3.0 is undone."
reverts = "class-docstrings"
refs = ["#4001"]
source = "release-note"

[[change]]
id = "refuse-empty-enum"
kind = "error"
action = "required"
title = "An empty `enum` is refused"
summary = "A schema with `enum: []` fails with an error instead of generating an empty enum."
migrate = "Drop the empty `enum` keyword."
refs = ["#4002"]
source = "release-note"
""",
}
UNRELEASED = {
    "custom-template-root": """
[[change]]
id = "custom-template-root"
category = "decided-change"
kind = "template"
action = "required"
title = "Custom templates receive `fields` instead of `model_fields`"
summary = "Templates of `--custom-template-dir` read the fields as `fields`."
affects = { templates = ["pydantic_v2/BaseModel.jinja2", "Old.jinja2"] }
keep = { note = "No option keeps `model_fields`." }
migrate = "Rename `model_fields` to `fields` in your templates."
refs = ["#5001"]
decision = ["#5000"]
source = "release-note"
""",
    "old-flag-removed": """
[[change]]
id = "old-flag-removed"
category = "api"
kind = "removal"
action = "required"
title = "`--old-flag` is removed"
summary = "The option did nothing since 0.3.0."
affects = { options = ["--old-flag"] }
migrate = "Drop `--old-flag`."
refs = ["#5002"]
decision = ["#5000"]
source = "release-note"
""",
}
RECORDS = {
    "new-flag": """
[[change]]
id = "new-flag"
category = "new-surface"
title = "Add --new-flag and the 20260301 presets"
surface = ["--new-flag", "--output-model-type c", "preset:20260301"]
covers = [
    "main/help/new_flag.txt",
]
""",
    "help-wording": """
[[change]]
id = "help-wording"
category = "docs-text"
title = "Reword the help of --quiet-flag"
""",
    "more-new-flag-outputs": """
[[change]]
id = "more-new-flag-outputs"
extends = "new-flag"
refs = ["#5003"]
""",
    "test-input-moved": """
[[change]]
id = "test-input-moved"
category = "harness"
title = "Move a test input"
summary = "The test reads its schema from a shared file; the package writes the same output for it."
""",
}
"""Records of categories without a guide entry and one that extends another: the pages and notes leave them out."""


def _write_data(
    root: Path,
    changes: dict[str, str],
    unreleased: dict[str, str],
    releases: str | None = None,
    derived: str = DERIVED,
    surface: dict[str, Any] | None = RELEASED_SURFACE,
) -> Path:
    data = root / "data"
    (data / "changes").mkdir(parents=True)
    (data / "unreleased").mkdir()
    rows = "".join(f'\n[[release]]\nversion = "{version}"\ndate = "{date}"\n' for version, date in RELEASES)
    (data / "releases.toml").write_text(rows if releases is None else releases, encoding="utf-8")
    (data / "derived.toml").write_text(derived, encoding="utf-8")
    if surface is not None:
        (data / "surface.json").write_text(json.dumps(surface), encoding="utf-8")
    for version, text in changes.items():
        (data / "changes" / f"{version}.toml").write_text(text, encoding="utf-8")
    for entry_id, text in unreleased.items():
        (data / "unreleased" / f"{entry_id}.toml").write_text(text, encoding="utf-8")
    return data


def _args(root: Path) -> list[str]:
    return [
        "--data-dir",
        str(root / "data"),
        "--docs-dir",
        str(root / "docs" / "migration"),
        "--json",
        str(root / "docs" / "data" / "migration.json"),
    ]


def _files(directory: Path) -> str:
    return "".join(
        f"--- {path.relative_to(directory).as_posix()} ---\n{path.read_text(encoding='utf-8')}"
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    )


def test_build_migration_docs_check() -> None:
    """The change list is valid and the committed hub matches it and the registries of the source tree."""
    subprocess.run([sys.executable, str(SCRIPT), "--check"], check=True)


def test_build_migration_docs_checks_only_the_committed_hub(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The build writes the hub and the files the docs deploy builds; `--check` compares the hub alone.

    The all-changes page and the JSON are not committed, so a check passes without them and names only a stale hub.
    """
    _write_data(tmp_path, CHANGES, UNRELEASED)
    docs = tmp_path / "docs"
    codes = {"build": main(_args(tmp_path), REGISTRIES)}
    written = sorted(path.relative_to(docs).as_posix() for path in docs.rglob("*") if path.is_file())
    (docs / "migration" / "changes.md").unlink()
    (docs / "data" / "migration.json").unlink()
    codes["check without the deploy files"] = main([*_args(tmp_path), "--check"], REGISTRIES)
    (docs / "migration" / "index.md").write_text("stale\n", encoding="utf-8")
    codes["check of a stale hub"] = main([*_args(tmp_path), "--check"], REGISTRIES)
    assert_output(f"{json.dumps(codes, indent=1)}\nwritten {written}\n{capsys.readouterr().err}")


def test_build_migration_docs_writes_only_the_data(tmp_path: Path) -> None:
    """`--write-data` writes the guide's JSON to the given file and no page."""
    _write_data(tmp_path, CHANGES, UNRELEASED)
    data = tmp_path / "out" / "migration.json"
    code = main([*_args(tmp_path), "--write-data", str(data)], REGISTRIES)
    written = sorted(path.relative_to(tmp_path).as_posix() for path in (tmp_path / "docs").rglob("*") if path.is_file())
    assert_output(f"exit {code}\nrepository files {written}\n{data.read_text(encoding='utf-8')}")


COMPLETE_RELEASES = 'complete_from = "0.2.0"\n' + "".join(
    f'\n[[release]]\nversion = "{version}"\ndate = "{date}"\n' for version, date in RELEASES
)
"""The releases with `complete_from`: the steps from 0.1.0 lack changes nobody wrote down."""
OPTION_VALUE_ENTRY = """
[[change]]
id = "option:--output-model-type=b"
title = "New value `b` of `--output-model-type`"
summary = "Writes models of kind b."
migrate = "Pass `--output-model-type b` for kind b models."
"""
NEW_WARNING_ENTRIES = """
[[change]]
id = "unmatched-override-keys"
kind = "warning"
action = "warn"
title = "`--type-overrides` keys that match nothing emit a `UserWarning`"
summary = "A key that names no generated model or field is reported; the output is unchanged."
affects = { options = ["--type-overrides"] }
migrate = "Fix or drop the keys the warning names."
refs = ["#3004"]
source = "release-note"

[[change]]
id = "quiet-warning-text"
kind = "warning"
action = "none"
title = "The warning of an unknown format names the field"
summary = "The text of an existing warning changes; nothing else does."
refs = ["#3005"]
source = "release-note"
"""
"""Hand entries of new warnings that deprecate nothing, beside the deprecation the registry gives in 0.3.0."""


EXPERIMENTAL_RECORD = """
[[change]]
id = "avro-logical-types"
category = "experimental"
kind = "experimental"
action = "review"
title = "Avro logical types become dates"
summary = "An Avro `date` logical type is typed `date` instead of `int`."
affects = { experimental = ["input-format.avro"] }
refs = ["#5004"]
decision = ["#5000"]
source = "release-note"
"""
"""A change of a released experimental feature: its release-note item names the feature."""
RECORD_PRS = {
    "custom-template-root": 5101,
    "new-flag": 5103,
    "more-new-flag-outputs": 5104,
    "help-wording": 5105,
    "avro-logical-types": 5106,
}
"""The pull requests that added the fragments: an item names those of its own and `new-surface` records only."""


def test_build_migration_docs_outputs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The pages, the per-release Markdown and the release notes come from the hand entries and the registries.

    The steps from a release older than `complete_from` are marked incomplete with a pointer to the changelog, and a
    hand entry extends the derived entry of an option value by its id. A hand-written new warning is listed apart
    from the deprecations, under a heading and a release-note section that plan no removal. Records without a guide
    entry, and one that extends another record, are left out. With the pull requests of the fragments, each item of
    the next release first names the one that added its record, a `new-surface` record's on the options and the
    preset it names, and an item of an experimental feature names the feature; the notes refuse a tag that
    releases.toml does not list yet.
    """
    _write_data(
        tmp_path,
        CHANGES | {"0.3.0": CHANGES["0.3.0"] + OPTION_VALUE_ENTRY + NEW_WARNING_ENTRIES},
        UNRELEASED | RECORDS | {"avro-logical-types": EXPERIMENTAL_RECORD},
        releases=COMPLETE_RELEASES,
        derived=DERIVED + '"--output-model-type=b" = "0.3.0"\n',
    )
    prs = tmp_path / "record-prs.json"
    prs.write_text(json.dumps(RECORD_PRS), encoding="utf-8")
    drafts = {
        "draft": [*_args(tmp_path), "--release-notes", "unreleased", "--record-prs", str(prs), "--latest-tag", "0.4.0"],
        "stamp pending": [*_args(tmp_path), "--release-notes", "unreleased", "--latest-tag", "0.5.0"],
    }
    (tmp_path / "docs" / "output-changes").mkdir(parents=True)
    (tmp_path / "docs" / "output-changes" / "0.3.0.md").write_text("# Output changes in 0.3.0\n", encoding="utf-8")
    codes = {
        "stale check": main([*_args(tmp_path), "--check"], REGISTRIES),
        "build": main(_args(tmp_path), REGISTRIES),
        "check": main([*_args(tmp_path), "--check"], REGISTRIES),
        "range pages": main([*_args(tmp_path), "--range-pages", str(tmp_path / "ranges")], REGISTRIES),
        "raw site": main([*_args(tmp_path), "--raw-site", str(tmp_path / "site")], REGISTRIES),
        "has breaking 0.4.0": main([*_args(tmp_path), "--has-breaking", "0.4.0"], REGISTRIES),
        "has breaking 0.1.0": main([*_args(tmp_path), "--has-breaking", "0.1.0"], REGISTRIES),
    }
    notes = {
        release: (main([*_args(tmp_path), "--release-notes", release], REGISTRIES), capsys.readouterr().out)
        for release in ("0.2.0", "0.3.0", "0.4.0", "unreleased")
    }
    for name, arguments in drafts.items():
        notes[name] = main(arguments, REGISTRIES), "".join(capsys.readouterr())
    output = [json.dumps(codes, indent=1)]
    output += [f"=== release notes {release} (exit {code}) ===\n{text}" for release, (code, text) in notes.items()]
    output += [_files(tmp_path / "docs"), _files(tmp_path / "ranges"), _files(tmp_path / "site")]
    assert_output("\n".join(output))


UNCHANGED = {"surface": REGISTRIES.surface}
INVALID_SURFACES = {
    "surface of the latest release": {"surface": RELEASED_SURFACE},
    "no surface": {"surface": None},
    "surface without released facts": {"surface": {key: RELEASED_SURFACE[key] for key in ("options", "templates")}},
    "released facts changed": {
        "surface": RELEASED_SURFACE
        | {
            "presets": {"20260101": {"standard-py313-20260101": "a0"}},
            "recommender_rules": {"digest": "r1", "revision": "2026-03-01"},
            "experimental": {"input-format.avro": "0.2.0"},
        }
    },
}
INVALID_CASES = {
    "surface of the latest release": (
        {},
        {
            "other-flag": """
[[change]]
id = "other-flag"
kind = "cli"
action = "none"
title = "t"
summary = "s"
affects = { options = ["--other-flag"] }
refs = ["#1"]
source = "release-note"
""",
            "cells-unreleased-flag": """
[[change]]
id = "cells-unreleased-flag"
category = "output-text"
kind = "output-text"
action = "none"
title = "t"
summary = "s"
refs = ["#1"]
source = "release-note"

[[change.cells]]
input = ["tests/data/jsonschema/a.json"]
backends = ["msgspec.Struct"]
options = [["--new-flag"], ["--output-model-type", "c"], ["--output-model-type", "b"]]
""",
        },
        None,
        DERIVED,
    ),
    "no surface": ({}, {}, None, DERIVED),
    "surface without released facts": ({}, UNRELEASED, None, DERIVED),
    "released facts changed": ({}, UNRELEASED, None, DERIVED),
    "entries": (
        {
            "0.2.0": """
[[change]]
id = "Not A Slug"

[[change]]
id = "unknown-keys"
kind = "surprise"
action = "maybe"
title = 1
summary = "s"
source = "guess"
color = "red"
affects = { planets = ["mars"], options = "--x" }
keep = { flags = ["--no-use-specialized-enum"], why = "x" }
verify = { schema = "{}", now = true }
refs = ["6000", 7]
supersedes = "older"

[[change]]
id = "keep-without-flags"
kind = "default-output"
action = "keep"
title = "t"
summary = "s"
refs = ["#1"]
source = "run"
verify = "{}"

[[change]]
id = "required-without-migrate"
kind = "cli"
action = "required"
title = "t"
summary = "s"
source = "release-note"
keep = { flags = ["--no-use-specialized-enum"] }

[[change]]
id = "removal:cli.unknown"
title = "t"

[[change]]
id = "deprecation:cli.validation"
title = "t"

[[change]]
id = "links"
kind = "cli"
action = "review"
title = "t"
summary = "s"
refs = ["#2"]
source = "release-note"
supersedes = ["missing", "later"]
reverts = "later"

[[change]]
id = "links"
title = "t"
""",
            "0.3.0": """
[[change]]
id = "removal:cli.validation"
title = "t"

[[change]]
id = "later"
kind = "cli"
action = "none"
title = "t"
summary = "s"
refs = ["#3"]
source = "release-note"
""",
            "0.9.0": "",
        },
        {"wrong-name": '[[change]]\nid = "other"\n', "broken": "[[change]\n"},
        None,
        '[presets]\n"20260101" = "0.2.5"\n"20251231" = "0.1.0"\n',
    ),
    "releases": (
        {},
        {},
        (
            'complete_from = "0.1.5"\n\n[[release]]\nversion = "0.2.0"\ndate = "2026"\n\n'
            '[[release]]\nversion = "0.1.0"\ndate = "2026-01-01"\n'
        ),
        DERIVED,
    ),
    "no releases": ({}, {}, "", DERIVED),
    "records": (
        {
            "0.3.0": """
[[change]]
id = "released-notice"
category = "notice"
kind = "default-output"
action = "review"
title = "t"
summary = "s"
keep = { note = "n" }
source = "release-note"

[[change]]
id = "option:--new-flag"
category = "new-surface"
title = "t"
"""
        },
        {
            "no-category": '[[change]]\nid = "no-category"\ntitle = "t"\n',
            "odd-category": '[[change]]\nid = "odd-category"\ncategory = "surprise"\ndecision = "#1"\n',
            "features": """
[[change]]
id = "features"
category = "experimental"
kind = "experimental"
action = "warn"
title = "t"
summary = "s"
affects = { experimental = ["input-format.avro", "input-format.unknown", "input-format.toml"] }
surface = ["--new-flag"]
source = "release-note"
""",
            "no-features": """
[[change]]
id = "no-features"
category = "experimental"
kind = "experimental"
action = "review"
title = "t"
summary = "s"
source = "release-note"
""",
            "harness-guide": """
[[change]]
id = "harness-guide"
category = "harness"
kind = "cli"
action = "none"
title = "t"
""",
            "surfaces": """
[[change]]
id = "surfaces"
category = "new-surface"
title = "t"
surface = ["--output-model-type", "--nothing", "preset:20260101", "preset:20260301"]
""",
            "extends-missing": '[[change]]\nid = "extends-missing"\nextends = "nothing"\ntitle = "t"\n',
            "extends-extends": '[[change]]\nid = "extends-extends"\nextends = "extends-missing"\n',
            "inline-covers": (
                '[[change]]\nid = "inline-covers"\ncategory = "internal"\ntitle = "t"\ncovers = ["a.txt"]\n'
            ),
            "api-scheduled": """
[[change]]
id = "api-scheduled"
category = "api"
kind = "removal"
action = "required"
title = "t"
summary = "s"
affects = { options = ["--validation"] }
migrate = "m"
refs = ["#1"]
source = "release-note"
""",
            "api-unscheduled": """
[[change]]
id = "api-unscheduled"
category = "api"
kind = "cli"
action = "review"
title = "t"
summary = "s"
affects = { options = ["--old-flag"] }
refs = ["#1"]
source = "release-note"
""",
            "derived-id-fragment": """
[[change]]
id = "option:--no-such-entry"
category = "api"
kind = "cli"
action = "review"
title = "t"
summary = "s"
affects = { options = ["--old-flag"] }
refs = ["#1"]
source = "release-note"
""",
            "decided-without-keep": """
[[change]]
id = "decided-without-keep"
category = "decided-change"
kind = "default-output"
action = "review"
title = "t"
summary = "s"
refs = ["#1"]
decision = ["#5"]
source = "release-note"
""",
            "bad-tables": """
[[change]]
id = "bad-tables"
category = "default-fix"
affects = "x"
keep = { flags = "x", note = 1 }
verify = { schema = 1, args = "x" }
""",
            "fix-without-decision": """
[[change]]
id = "fix-without-decision"
category = "default-fix"
kind = "default-output"
action = "keep"
title = "t"
summary = "s"
refs = ["#1"]
source = "release-note"
""",
            "run-without-fixture": """
[[change]]
id = "run-without-fixture"
category = "output-text"
kind = "output-text"
action = "none"
title = "t"
summary = "s"
refs = ["#1"]
source = "run"
""",
            "bad-decision": '[[change]]\nid = "bad-decision"\ncategory = "internal"\ntitle = "t"\ndecision = ["5"]\n',
            "cells-of-harness": (
                '[[change]]\nid = "cells-of-harness"\ncategory = "harness"\ntitle = "t"\nsummary = "s"\n' + CELLS_TABLE
            ),
            "cells-unknown-key": '[[change]]\nid = "cells-unknown-key"\ncategory = "internal"\ntitle = "t"\n'
            + CELLS_TABLE.replace("match", "colour"),
            "cells-inline": (
                '[[change]]\nid = "cells-inline"\ncategory = "docs-text"\ntitle = "t"\n'
                'cells = [{ input = ["a.json"], backends = "*", options = "*" }]\n'
            ),
        },
        None,
        DERIVED,
    ),
    "ranges": (
        {
            "0.2.0": """
[[change]]
id = "datetime-class"
kind = "default-output"
action = "keep"
title = "t"
summary = "s"
keep = { flags = ["--output-datetime-class", "datetime"] }
refs = ["#1"]
source = "release-note"

[[change]]
id = "unknown-flag"
kind = "default-output"
action = "keep"
title = "t"
summary = "s"
keep = { flags = ["--no-such-option"] }
refs = ["#2"]
source = "release-note"
""",
            "0.3.0": """
[[change]]
id = "datetime-class-again"
kind = "default-output"
action = "keep"
title = "t"
summary = "s"
keep = { flags = ["--output-datetime-class", "AwareDatetime"] }
refs = ["#3"]
source = "release-note"

[[change]]
id = "conditional-unknown-flag"
kind = "default-output"
action = "keep"
title = "t"
summary = "s"
affects = { options = ["--use-annotated"] }
keep = { flags = ["--no-such-conditional-option"] }
refs = ["#4"]
source = "release-note"
""",
        },
        {
            "unreleased-unknown-flag": """
[[change]]
id = "unreleased-unknown-flag"
category = "default-fix"
decision = ["#5"]
kind = "default-output"
action = "keep"
title = "t"
summary = "s"
keep = { flags = ["--no-such-unreleased-option"] }
refs = ["#5"]
source = "release-note"
"""
        },
        None,
        DERIVED,
    ),
}


@pytest.mark.parametrize("case", sorted(INVALID_CASES))
def test_build_migration_docs_invalid_data(case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Every problem of the change list is reported, and nothing is written."""
    _write_data(tmp_path, *INVALID_CASES[case], **INVALID_SURFACES.get(case, UNCHANGED))
    code = main(_args(tmp_path), REGISTRIES)
    assert_output(f"exit {code}\n{capsys.readouterr().err}written: {sorted(tmp_path.rglob('*.md'))}\n")


TAG_PACKAGE = {
    "__init__.py": '__all__ = ["generate"]\n',
    "arguments.py": """import argparse

arg_parser = argparse.ArgumentParser(add_help=False)
arg_parser.add_argument("--output-model-type", choices=["a", "b", "c"], help="Output model type.")
arg_parser.add_argument("--preset", choices=["p1", "p2"], help="A dated preset.")
arg_parser.add_argument("--new-flag", action=argparse.BooleanOptionalAction, help="Turn on new things.")
arg_parser.add_argument("--quiet-flag", action="store_true")
""",
    "config.py": 'class GenerateConfig:\n    model_fields = {"output": None}\n',
    "experimental.py": """from types import SimpleNamespace


def iter_experimental_features():
    return [
        SimpleNamespace(id="input-format.avro", since_version="0.3.0"),
        SimpleNamespace(id="input-format.toml", since_version="0.5.0"),
    ]
""",
    "preset.py": """from types import SimpleNamespace

GROUP = SimpleNamespace(
    config=SimpleNamespace(items=lambda: [SimpleNamespace(field_name="snake_case_field", pyproject_value=True)]),
    input_file_types=(),
    output_model_types=(),
)


def get_preset_infos():
    dates = {"20260101": ["standard-py313", "practical-py313"], "20260201": ["standard-py313"]}
    names = [f"{name}-{date}" for date, names in (dates | {"20260301": ["practical-py314"]}).items() for name in names]
    return [SimpleNamespace(name=SimpleNamespace(value=name), option_groups=[GROUP]) for name in names]
""",
    "resources/recommender_rules.json": '{"rules_revision": "2026-03-01", "rules": [], "options": {}}\n',
    "model/template/Kept.jinja2": "{{ class_name }}\n",
}
"""A tag tree's package with the surface of `REGISTRIES`; its dated presets have option sets of their own."""


def _tag_registries(data: Path, package: Path) -> Registries:
    """Write the tag tree's package and return registries with its surface, the release before it with its presets."""
    for name, text in TAG_PACKAGE.items():
        (package / name).parent.mkdir(parents=True, exist_ok=True)
        (package / name).write_text(text, encoding="utf-8")
    surface = read_surface(package.parent)
    released = {date: surface["presets"][date] for date in RELEASED_SURFACE["presets"]}
    (data / "surface.json").write_text(json.dumps(RELEASED_SURFACE | {"presets": released}), encoding="utf-8")
    return replace(REGISTRIES, surface=surface)


def test_build_migration_docs_stamp(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A stamp moves the fragments the tag contains, adds the release and the preset dates the tag first ships.

    A moved entry loses its covers and cells; records without a guide entry and records that extend another are
    deleted. A tag tree whose fragment differs from the branch's, whose surface cannot be read, or that lacks an
    experimental feature whose `since_version` is the stamped release is refused before anything is written, so the
    stamp runs again once the tree is right.
    """
    shipped = {
        "covered-template-root": UNRELEASED["custom-template-root"]
        .replace("custom-template-root", "covered-template-root")
        .replace('source = "release-note"\n', 'source = "release-note"\ncovers = [\n    "main/template.py",\n]\n')
        + CELLS_TABLE,
        "new-flag": RECORDS["new-flag"],
        "more-new-flag-outputs": RECORDS["more-new-flag-outputs"],
    }
    data = _write_data(
        tmp_path,
        CHANGES,
        UNRELEASED
        | shipped
        | {"merged-after-tag": UNRELEASED["custom-template-root"].replace("custom-template-root", "merged-after-tag")},
    )
    tree = tmp_path / "tag"
    tagged = tree / "docs" / "data" / "migration" / "unreleased" / "custom-template-root.toml"
    tagged.parent.mkdir(parents=True)
    for entry_id, text in shipped.items():
        tagged.with_name(f"{entry_id}.toml").write_text(text, encoding="utf-8")
    package = tree / "src" / "datamodel_code_generator"
    registries = _tag_registries(data, package)
    without_toml = TAG_PACKAGE["experimental.py"].replace(
        '        SimpleNamespace(id="input-format.toml", since_version="0.5.0"),\n', ""
    )
    stamp = ["--stamp", "0.5.0", "--tag-tree", str(tree), "--date", "2026-05-01"]
    codes = []
    for fragment, package_files in (
        ("edited after the tag", TAG_PACKAGE),
        (UNRELEASED["custom-template-root"], {**TAG_PACKAGE, "arguments.py": "raise SystemExit('no surface')"}),
        (UNRELEASED["custom-template-root"], {**TAG_PACKAGE, "experimental.py": without_toml}),
        (UNRELEASED["custom-template-root"], TAG_PACKAGE),
        (UNRELEASED["custom-template-root"], TAG_PACKAGE),
    ):
        tagged.write_text(fragment, encoding="utf-8")
        for name, text in package_files.items():
            (package / name).write_text(text, encoding="utf-8")
        codes.append(main([*_args(tmp_path), *stamp], registries))
    printed = capsys.readouterr()
    with pytest.raises(SystemExit) as missing_tree:
        main([*_args(tmp_path), "--stamp", "0.6.0"], registries)
    output = [
        f"exits {codes}, without --tag-tree {missing_tree.value.code}",
        printed.out,
        printed.err,
        capsys.readouterr().err.splitlines()[-1],
    ]
    assert_output("\n".join([*output, _files(data), _files(tmp_path / "docs")]))


def test_build_migration_docs_stamp_after_a_merge_onto_the_tagged_date(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tree that moved an option to a new date after the tag is stamped, as the tag's own facts hold.

    Between the tag and its stamp surface.json is still the release before, so the tagged date and the new one are both
    unreleased there; `--check` says to stamp first, and the stamp compares the tree with the tag's surface alone.
    """
    data = _write_data(tmp_path, CHANGES, UNRELEASED)
    tree = tmp_path / "tag"
    fragments = tree / "docs" / "data" / "migration" / "unreleased"
    fragments.mkdir(parents=True)
    for entry_id, text in UNRELEASED.items():
        (fragments / f"{entry_id}.toml").write_text(text, encoding="utf-8")
    tagged = _tag_registries(data, tree / "src" / "datamodel_code_generator")
    surface = tagged.surface
    registries = replace(
        tagged,
        preset_dates=(*tagged.preset_dates, PresetDate("20260401", "20260301", ())),
        surface=surface | {"presets": surface["presets"] | {"20260401": {"practical-py314-20260401": "b4"}}},
    )
    checked = main([*_args(tmp_path), "--check"], registries)
    problems = capsys.readouterr().err
    stamped = main([*_args(tmp_path), "--stamp", "0.5.0", "--tag-tree", str(tree), "--date", "2026-05-01"], registries)
    stamp_problems = capsys.readouterr().err
    after = main([*_args(tmp_path), "--check"], registries)
    assert_output(f"exits {checked}, {stamped}, {after}\n{problems}{stamp_problems}{capsys.readouterr().err}")


@pytest.mark.parametrize("case", ["date removed", "date swapped", "name removed", "date added"])
def test_build_migration_docs_check_refuses_a_released_preset_the_tree_lost(
    case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A preset date or name the release ships must stay; a tree that only adds a date passes."""
    _write_data(tmp_path, CHANGES, UNRELEASED, **UNCHANGED)
    presets = dict(REGISTRIES.surface["presets"])
    dates = REGISTRIES.preset_dates
    new_date = PresetDate("20260401", "20260301", ())
    match case:
        case "date removed":
            del presets["20260201"]
        case "date swapped":
            presets = {date: names for date, names in presets.items() if date != "20260301"}
            presets["20260401"] = {"practical-py314-20260401": "b4"}
            dates = (*dates[:-1], new_date)
        case "name removed":
            presets["20260101"] = {"standard-py313-20260101": "a1"}
        case _:
            presets["20260401"] = {"practical-py314-20260401": "b4"}
            dates = (*dates, new_date)
    registries = replace(REGISTRIES, preset_dates=dates, surface=REGISTRIES.surface | {"presets": presets})
    written = main(_args(tmp_path), registries) if case == "date added" else None
    capsys.readouterr()
    code = main([*_args(tmp_path), "--check"], registries)
    assert_output(f"{case}: written {written}, exit {code}\n{capsys.readouterr().err}")


def test_build_migration_docs_stamp_retained_extension(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A fragment merged after the tag that extends one the tag contains is refused before anything is written.

    Once the extension is gone the stamp goes through, also for a fragment whose header carries a comment.
    """
    commented = UNRELEASED["custom-template-root"].replace("custom-template-root", "commented-header")
    commented = commented.replace("[[change]]\n", "[[change]] # kept as written\n")
    shipped = UNRELEASED | {"new-flag": RECORDS["new-flag"], "commented-header": commented}
    data = _write_data(tmp_path, CHANGES, shipped | {"more-new-flag-outputs": RECORDS["more-new-flag-outputs"]})
    tree = tmp_path / "tag"
    tagged = tree / "docs" / "data" / "migration" / "unreleased"
    tagged.mkdir(parents=True)
    for entry_id, text in shipped.items():
        (tagged / f"{entry_id}.toml").write_text(text, encoding="utf-8")
    registries = _tag_registries(data, tree / "src" / "datamodel_code_generator")
    stamp = ["--stamp", "0.5.0", "--tag-tree", str(tree), "--date", "2026-05-01"]
    refused = main([*_args(tmp_path), *stamp], registries)
    before = _files(data)
    (data / "unreleased" / "more-new-flag-outputs.toml").unlink()
    stamped = main([*_args(tmp_path), *stamp], registries)
    printed = capsys.readouterr()
    assert_output(f"exits {refused}, {stamped}\n{printed.err}{before}{_files(data / 'changes')}")


def test_build_migration_docs_stamp_refuses_an_empty_preset_date(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A release whose new preset date adds no option group and no Python target to the date before is not stamped."""
    data = _write_data(tmp_path, CHANGES, UNRELEASED)
    tree = tmp_path / "tag"
    (tree / "docs" / "data" / "migration" / "unreleased").mkdir(parents=True)
    tagged = _tag_registries(data, tree / "src" / "datamodel_code_generator")
    *kept, shipped = tagged.preset_dates
    registries = replace(tagged, preset_dates=(*kept, replace(shipped, additions=())))
    before = _files(data)
    code = main([*_args(tmp_path), "--stamp", "0.5.0", "--tag-tree", str(tree), "--date", "2026-05-01"], registries)
    assert_output(f"exit {code}, files kept: {_files(data) == before}\n{capsys.readouterr().err}")


def test_build_migration_docs_preset_line_names_the_records_of_its_options(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The release-note item of a preset date names the pull request of each record of an option the date adds.

    A `new-surface` record that names an option of the date, and not the date, adds its pull request to the date's
    item without its refs, which stay on the option's item.
    """
    record = RECORDS["new-flag"].replace(', "preset:20260301"', "").replace("covers", 'refs = ["#5201"]\ncovers')
    _write_data(tmp_path, CHANGES, UNRELEASED | {"new-flag": record})
    prs = tmp_path / "record-prs.json"
    prs.write_text(json.dumps(RECORD_PRS), encoding="utf-8")
    *kept, joined = REGISTRIES.preset_dates
    registries = replace(REGISTRIES, preset_dates=(*kept, replace(joined, options=("--new-flag",))))
    code = main([*_args(tmp_path), "--release-notes", "unreleased", "--record-prs", str(prs)], registries)
    assert_output(f"exit {code}\n{capsys.readouterr().out}")


def test_build_migration_docs_preset_line_names_the_record_of_a_value_option(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bare-option record also reaches the date whose group lists the option with a value.

    The group lists `--new-flag chosen`; the record names `--new-flag`. A record that names the value itself keeps
    exact matching.
    """
    record = RECORDS["new-flag"].replace(', "preset:20260301"', "").replace("covers", 'refs = ["#5201"]\ncovers')
    _write_data(tmp_path, CHANGES, UNRELEASED | {"new-flag": record})
    prs = tmp_path / "record-prs.json"
    prs.write_text(json.dumps(RECORD_PRS), encoding="utf-8")
    *kept, joined = REGISTRIES.preset_dates
    registries = replace(REGISTRIES, preset_dates=(*kept, replace(joined, options=("--new-flag chosen",))))
    code = main([*_args(tmp_path), "--release-notes", "unreleased", "--record-prs", str(prs)], registries)
    assert_output(f"exit {code}\n{capsys.readouterr().out}")


def test_migration_selector_script() -> None:
    """The hub's script shows the hidden selector, also after the page content changes, and opens the chosen range."""
    driver = """
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[1], "utf8");
vm.runInNewContext(source, {});
class HTMLSelectElement {
  constructor(value, inside) { this.value = value; this.inside = inside; }
  closest() { return this.inside ? {} : null; }
}
const elements = [{ hidden: true }, { hidden: false }];
const assigned = [];
let onChange = null;
let onMutation = null;
const document = {
  documentElement: {},
  querySelectorAll: (selector) => elements.filter((element) => element.hidden && selector.endsWith("[hidden]")),
  addEventListener: (type, listener) => { onChange = type === "change" ? listener : onChange; },
};
class MutationObserver {
  constructor(callback) { onMutation = callback; }
  observe() {}
}
const window = { location: { href: "https://example.org/migration/", assign: (url) => assigned.push(url) } };
vm.runInNewContext(source, { document, window, MutationObserver, HTMLSelectElement, URL });
const shown = elements.map((element) => element.hidden);
elements.push({ hidden: true });
onMutation();
for (const target of [
  new HTMLSelectElement("from-0.31.0/", true),
  new HTMLSelectElement("", true),
  new HTMLSelectElement("from-0.20.0/", false),
  {},
]) {
  onChange({ target });
}
console.log(JSON.stringify({ hiddenAtStart: shown, hiddenAfterNewContent: elements.map((e) => e.hidden), assigned }));
"""
    result = subprocess.run(
        ["node", "-e", driver, str(SCRIPT_JS)], capture_output=True, text=True, encoding="utf-8", check=True
    )
    assert_output(result.stdout)


ENUM_SCHEMA = (
    '{"title": "Order", "type": "object", "properties": {"status": {"type": "string", "enum": ["open", "closed"]}}}'
)
ENUM_ARGS = json.dumps([
    "--input-file-type",
    "jsonschema",
    "--output-model-type",
    "pydantic_v2.BaseModel",
    "--target-python-version",
    "3.11",
])
RUN_CHANGES = {
    "0.3.0": f"""
[[change]]
id = "enum-keep"
kind = "default-output"
action = "keep"
title = "String enums become `StrEnum`"
summary = "Enums of strings subclass `StrEnum` instead of `Enum`."
before = "class Status(Enum):"
after = "class Status(StrEnum):"
keep = {{ flags = ["--no-use-specialized-enum"] }}
refs = ["#2512"]
source = "run"
[change.verify]
schema = '{ENUM_SCHEMA}'
args = {ENUM_ARGS}

[[change]]
id = "enum-wrong-keep"
kind = "default-output"
action = "keep"
title = "A keep claim that does not hold"
summary = "Constraints move into `Annotated`."
keep = {{ flags = ["--use-annotated"] }}
refs = ["#2513"]
source = "run"
[change.verify]
schema = '{ENUM_SCHEMA}'
args = {ENUM_ARGS}
""",
}
RUN_CHANGES_IDS = ("enum-keep", "enum-wrong-keep")
RUN_BROKEN = {
    "enum-import-fails": "raise ImportError('the previous release wrote broken models')\n",
    "enum-signature-crashes": "import os\n\nos._exit(3)\n",
}
"""Recorded outputs of entries whose keep flags cannot match them: one fails to import, one kills its signature."""
RUN_BROKEN_CHANGES = "".join(
    f"""
[[change]]
id = "{entry_id}"
kind = "default-output"
action = "keep"
title = "A keep claim against a recorded output that {entry_id.removeprefix("enum-").replace("-", " ")}"
summary = "s"
keep = {{ flags = ["--no-use-specialized-enum"] }}
refs = ["#2514"]
source = "run"
[change.verify]
schema = '{ENUM_SCHEMA}'
args = {ENUM_ARGS}
"""
    for entry_id in RUN_BROKEN
)
RUN_UNRELEASED = {
    "enum-keep-next": f"""
[[change]]
id = "enum-keep-next"
category = "default-fix"
decision = ["#2515"]
kind = "default-output"
action = "keep"
title = "An unreleased change kept only for Python 3.11 targets"
summary = "Enums of strings subclass `StrEnum` instead of `Enum`."
affects = {{ options = ["--target-python-version 3.11"] }}
keep = {{ flags = ["--no-use-specialized-enum"] }}
refs = ["#2515"]
source = "run"
[change.verify]
schema = '{ENUM_SCHEMA}'
args = {ENUM_ARGS}
"""
}
"""An unreleased entry: its own keep flags, which `--target-python-version 3.11` in its args selects, are its union."""
PREVIOUS_ENUM_OUTPUT = """from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class Status(Enum):
    open = 'open'
    closed = 'closed'


class Order(BaseModel):
    status: Status | None = None
"""


@needs("posix")
def test_build_migration_docs_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Keep claims are checked against the recorded output of the previous release; a stale record is refused.

    A recorded output that fails to import, or whose signature cannot be taken, matches no output that imports; an
    unreleased entry's union holds its own keep flags. A record of another previous release than the entry's is
    stale.
    """
    data = _write_data(tmp_path, {"0.3.0": RUN_CHANGES["0.3.0"] + RUN_BROKEN_CHANGES}, RUN_UNRELEASED, **UNCHANGED)
    unrecorded = main(_args(tmp_path), REGISTRIES)
    missing = capsys.readouterr().err
    fixture = hashlib.sha256(json.dumps([ENUM_SCHEMA, json.loads(ENUM_ARGS)]).encode()).hexdigest()[:16]
    tree = {"model.py": PREVIOUS_ENUM_OUTPUT}
    records = {entry_id: {"previous": "0.2.0", "fixture": fixture, "tree": tree} for entry_id in RUN_CHANGES_IDS}
    records |= {
        entry_id: {"previous": "0.2.0", "fixture": fixture, "tree": {"model.py": text}}
        for entry_id, text in RUN_BROKEN.items()
    }
    records["enum-keep-next"] = {"previous": "0.4.0", "fixture": fixture, "tree": tree}
    (data / "recorded.json").write_text(json.dumps(records), encoding="utf-8")
    codes = {
        "verify": main([*_args(tmp_path), "--verify", "--text", "-j", "2"], REGISTRIES),
        "verify one": main([*_args(tmp_path), "--verify", "enum-keep"], REGISTRIES),
    }
    printed = capsys.readouterr().out
    (data / "recorded.json").write_text(
        json.dumps(records | {"enum-keep": records["enum-keep"] | {"previous": "0.1.0"}}), encoding="utf-8"
    )
    codes["record of another previous release"] = main([*_args(tmp_path), "--check"], REGISTRIES)
    (data / "recorded.json").write_text(json.dumps(records), encoding="utf-8")
    codes["unknown id"] = main([*_args(tmp_path), "--verify", "nothing"], REGISTRIES)
    codes["bad python"] = main([*_args(tmp_path), "--record", "enum-keep", "--release-python", "0.2.0"], REGISTRIES)
    errors = capsys.readouterr().err
    assert_output("\n".join([f"exit {unrecorded}", missing, json.dumps(codes, indent=1), printed, errors]))


@needs("posix")
def test_build_migration_docs_record_without_change(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A fixture both releases generate alike is not recorded: the entry claims a change that is not there."""
    data = _write_data(tmp_path, RUN_CHANGES, {}, **UNCHANGED)
    python = f"={sys.executable}"
    code = main(
        [
            *_args(tmp_path),
            "--record",
            "enum-keep",
            "--release-python",
            f"0.2.0{python}",
            "--release-python",
            f"0.3.0{python}",
        ],
        REGISTRIES,
    )
    assert_output(f"exit {code}\n{capsys.readouterr().out}recorded: {(data / 'recorded.json').read_text()}")
