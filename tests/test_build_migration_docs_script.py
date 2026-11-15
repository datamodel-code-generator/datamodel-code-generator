"""Tests for the upgrade guide: the change list, the pages and release notes built from it, and the release stamp.

The registries are fixtures here, so each derived kind (warning, scheduled change, removal, experimental feature,
dated preset) shows up; the committed guide is checked against the registries of the source tree.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from datamodel_code_generator.deprecations import Deprecation
from datamodel_code_generator.experimental import ExperimentalFeature
from scripts.build_migration_docs import PresetDate, Registries, main
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
}
RELEASES = [("0.1.0", "2026-01-01"), ("0.2.0", "2026-02-01"), ("0.3.0", "2026-03-01"), ("0.4.0", "2026-04-01")]
DERIVED = '[presets]\n"20260101" = "0.2.0"\n"20260201" = "0.3.0"\n\n[options]\n"--released-flag" = "0.3.0"\n'
ALLOWLIST = """base = "0.4.0"

[[accepted]]
input = "*"
reason = "r"
decision = "#5001"
migration = "custom-template-root"
"""
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
kind = "template"
action = "required"
title = "Custom templates receive `fields` instead of `model_fields`"
summary = "Templates of `--custom-template-dir` read the fields as `fields`."
affects = { templates = ["pydantic_v2/BaseModel.jinja2", "Old.jinja2"] }
migrate = "Rename `model_fields` to `fields` in your templates."
refs = ["#5001"]
source = "release-note"
""",
    "old-flag-removed": """
[[change]]
id = "old-flag-removed"
kind = "removal"
action = "required"
title = "`--old-flag` is removed"
summary = "The option did nothing since 0.3.0."
affects = { options = ["--old-flag"] }
migrate = "Drop `--old-flag`."
refs = ["#5002"]
source = "release-note"
""",
}


def _write_data(
    root: Path,
    changes: dict[str, str],
    unreleased: dict[str, str],
    releases: str | None = None,
    derived: str = DERIVED,
    surface: dict[str, Any] | None = RELEASED_SURFACE,
    allowlist: str = ALLOWLIST,
) -> Path:
    data = root / "data"
    (data / "changes").mkdir(parents=True)
    (data / "unreleased").mkdir()
    rows = "".join(f'\n[[release]]\nversion = "{version}"\ndate = "{date}"\n' for version, date in RELEASES)
    (data / "releases.toml").write_text(rows if releases is None else releases, encoding="utf-8")
    (data / "derived.toml").write_text(derived, encoding="utf-8")
    (root / "allowlist.toml").write_text(allowlist, encoding="utf-8")
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
        "--allowlist",
        str(root / "allowlist.toml"),
    ]


def _files(directory: Path) -> str:
    return "".join(
        f"--- {path.relative_to(directory).as_posix()} ---\n{path.read_text(encoding='utf-8')}"
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    )


def test_build_migration_docs_check() -> None:
    """The committed hub, all-changes page and JSON match the change list and the registries of the source tree."""
    subprocess.run([sys.executable, str(SCRIPT), "--check"], check=True)


def test_build_migration_docs_outputs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The pages, the per-release Markdown and the release notes come from the hand entries and the registries."""
    _write_data(tmp_path, CHANGES, UNRELEASED)
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
    output = [json.dumps(codes, indent=1)]
    output += [f"=== release notes {release} (exit {code}) ===\n{text}" for release, (code, text) in notes.items()]
    output += [_files(tmp_path / "docs"), _files(tmp_path / "ranges"), _files(tmp_path / "site")]
    assert_output("\n".join(output))


UNCHANGED = {"surface": REGISTRIES.surface, "allowlist": ALLOWLIST.replace("0.4.0", "0.3.0")}
INVALID_SURFACES = {
    "surface and allowlist": {"surface": RELEASED_SURFACE, "allowlist": ALLOWLIST},
    "no surface": {"surface": None, "allowlist": ALLOWLIST},
}
INVALID_CASES = {
    "surface and allowlist": (
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
"""
        },
        None,
        DERIVED,
    ),
    "no surface": ({}, {}, None, DERIVED),
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
        '[[release]]\nversion = "0.2.0"\ndate = "2026"\n\n[[release]]\nversion = "0.1.0"\ndate = "2026-01-01"\n',
        DERIVED,
    ),
    "no releases": ({}, {}, "", DERIVED),
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
""",
        },
        {},
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
    "preset_names.py": 'PresetName = Literal[\n    "standard-py313-20260101",\n    "practical-py314-20260301",\n]\n',
    "model/template/Kept.jinja2": "{{ class_name }}\n",
}


def test_build_migration_docs_stamp(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A stamp moves the fragments the tag contains, adds the release and the preset dates the tag first ships."""
    data = _write_data(
        tmp_path,
        CHANGES,
        UNRELEASED
        | {"merged-after-tag": UNRELEASED["custom-template-root"].replace("custom-template-root", "merged-after-tag")},
    )
    tree = tmp_path / "tag"
    (tree / "docs" / "data" / "migration" / "unreleased").mkdir(parents=True)
    (tree / "docs" / "data" / "migration" / "unreleased" / "custom-template-root.toml").write_text("", encoding="utf-8")
    package = tree / "src" / "datamodel_code_generator"
    (package / "model" / "template").mkdir(parents=True)
    for name, text in TAG_PACKAGE.items():
        (package / name).write_text(text, encoding="utf-8")
    stamp = ["--stamp", "0.5.0", "--tag-tree", str(tree), "--date", "2026-05-01"]
    codes = [main([*_args(tmp_path), *stamp], REGISTRIES), main([*_args(tmp_path), *stamp], REGISTRIES)]
    printed = capsys.readouterr()
    with pytest.raises(SystemExit) as missing_tree:
        main([*_args(tmp_path), "--stamp", "0.6.0"], REGISTRIES)
    output = [
        f"exits {codes}, without --tag-tree {missing_tree.value.code}",
        printed.out,
        printed.err,
        capsys.readouterr().err.splitlines()[-1],
    ]
    assert_output("\n".join([*output, _files(data), _files(tmp_path / "docs")]))


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
