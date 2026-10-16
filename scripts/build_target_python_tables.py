r"""Build the tables of each target Python version from the CPython interpreter of that version.

Names the generator writes follow the Unicode version of `--target-python-version`, never the running interpreter's:
identifier characters, `\w`, numeric characters, case mappings and normalization. The builtin formatter groups the
standard library modules of the target. This script runs the interpreter of every target, records what it answers for
every code point and its `sys.stdlib_module_names`, and writes `src/datamodel_code_generator/_target_unicode_data.py`
and `src/datamodel_code_generator/_target_stdlib_data.py`.

    python scripts/build_target_python_tables.py [--python 3.15=/path/to/python3.15 ...]
    python scripts/build_target_python_tables.py --check [--python ...]
    python scripts/build_target_python_tables.py --check-interpreter

Interpreters not given with `--python` are found with `uv python find --managed-python`. Give the newest patch release
of each version, which CI runs: a patch release can add standard library modules. `--check` reports a module that
differs from what the interpreters answer; `--check-interpreter` checks the tables of the running interpreter's
version against the running interpreter alone, including the rules that rebuild case mappings and normalization from
the tables.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src/datamodel_code_generator/_target_unicode_data.py"
STDLIB_OUTPUT = ROOT / "src/datamodel_code_generator/_target_stdlib_data.py"
SET_NAMES = ("identifier_start", "identifier_continue", "word", "numeric", "assigned")
SET_DOCS = {
    "identifier_start": "Characters `str.isidentifier()` accepts first: XID_Start and the underscore.",
    "identifier_continue": "Characters `str.isidentifier()` accepts after the first: XID_Continue.",
    "word": "Characters the `re` pattern `\\\\w` matches.",
    "numeric": "Characters `str.isnumeric()` accepts.",
    "assigned": "Characters whose general category is not Cn.",
}
HANGUL_SYLLABLES = range(0xAC00, 0xD7A4)
SIGMA = "Σ"
FINAL_SIGMA = "ς"
LINE_WIDTH = 116


def _ranges(code_points: list[int]) -> list[list[int]]:
    ranges: list[list[int]] = []
    for code_point in code_points:
        if ranges and ranges[-1][1] == code_point - 1:
            ranges[-1][1] = code_point
        else:
            ranges.append([code_point, code_point])
    return ranges


def _case_flags(character: str) -> int:
    """Return the Lowercase, Uppercase, Lt and Case_Ignorable bits CPython stores for a character."""
    title = character.istitle() and not character.isupper()
    cased = character.islower() or character.istitle()
    sigma = ("A" + SIGMA + character).lower()[1] if cased else ("A" + character + SIGMA).lower()[-1]
    return character.islower() | character.isupper() << 1 | title << 2 | (sigma == FINAL_SIGMA) << 3


def probe() -> dict[str, Any]:
    """Return what the running interpreter answers for every code point."""
    word = re.compile(r"\w")
    members: dict[str, list[int]] = {name: [] for name in SET_NAMES}
    case: dict[int, list[Any]] = {}
    normalization: dict[int, list[Any]] = {}
    compositions: list[list[int]] = []
    for code_point in range(0x110000):
        character = chr(code_point)
        if unicodedata.category(character) == "Cn":
            continue
        members["assigned"].append(code_point)
        if character.isidentifier():
            members["identifier_start"].append(code_point)
        if ("a" + character).isidentifier():
            members["identifier_continue"].append(code_point)
        if word.match(character):
            members["word"].append(code_point)
        if character.isnumeric():
            members["numeric"].append(code_point)
        mappings = (character.upper(), character.lower(), character.title(), character.casefold())
        if (flags := _case_flags(character)) or any(mapping != character for mapping in mappings):
            case[code_point] = [*mappings, flags]
        if code_point in HANGUL_SYLLABLES:
            continue
        decompositions = (unicodedata.normalize("NFD", character), unicodedata.normalize("NFKD", character))
        if (combining := unicodedata.combining(character)) or any(item != character for item in decompositions):
            normalization[code_point] = [*decompositions, combining]
        match unicodedata.decomposition(character).split():
            case [first, second] if not first.startswith("<"):
                if unicodedata.normalize("NFC", chr(int(first, 16)) + chr(int(second, 16))) == character:
                    compositions.append([int(first, 16), int(second, 16), code_point])
            case _:
                pass
    return {
        "python": "{}.{}".format(*sys.version_info[:2]),
        "release": sys.version_info.releaselevel,
        "unicode": unicodedata.unidata_version,
        "sets": {name: _ranges(code_points) for name, code_points in members.items()},
        "case": case,
        "normalization": normalization,
        "compositions": compositions,
        "stdlib": sorted(sys.stdlib_module_names),
    }


def _targets() -> tuple[str, ...]:
    sys.path.insert(0, str(ROOT / "src"))
    from datamodel_code_generator import PythonVersion  # noqa: PLC0415

    return tuple(version.value for version in PythonVersion)


def _interpreter(version: str, given: dict[str, str]) -> str:
    if (path := given.get(version)) is not None:
        return path
    if (uv := shutil.which("uv")) is None:
        msg = f"uv is required to find Python {version}; pass --python {version}=PATH"
        raise SystemExit(msg)
    found = subprocess.run(
        [uv, "python", "find", "--managed-python", "--no-project", version],
        capture_output=True,
        check=True,
        text=True,
    )
    return found.stdout.strip()


def _run_probe(version: str, interpreter: str) -> dict[str, Any]:
    output = subprocess.run(
        [interpreter, "-I", str(Path(__file__).resolve()), "--probe"], capture_output=True, check=True, text=True
    ).stdout
    result = json.loads(output)
    if result["python"] != version or result["release"] != "final":
        msg = f"{interpreter} is Python {result['python']} ({result['release']}), not a final Python {version}"
        raise SystemExit(msg)
    return result


def _members(ranges: list[list[int]]) -> set[int]:
    return {code_point for start, end in ranges for code_point in range(start, end + 1)}


def _changes(values: list[Any], default: Any) -> list[tuple[int, Any]]:
    changes: list[tuple[int, Any]] = []
    previous = default
    for index, value in enumerate(values):
        if value != previous:
            changes.append((index, value))
            previous = value
    return changes


def merge(probes: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge the answers of every target into tables a target reads by its index."""
    sets: dict[str, list[list[list[int]]]] = {}
    for name in SET_NAMES:
        additions: list[list[list[int]]] = []
        previous: set[int] = set()
        for result in probes:
            current = _members(result["sets"][name])
            if lost := previous - current:
                msg = f"Python {result['python']} drops {len(lost)} {name} characters; the tables only add"
                raise SystemExit(msg)
            additions.append(_ranges(sorted(current - previous)))
            previous = current
        sets[name] = additions
    case: dict[int, list[tuple[int, Any]]] = {}
    for code_point in sorted({int(key) for result in probes for key in result["case"]}):
        default = [chr(code_point)] * 4 + [0]
        values = [result["case"].get(str(code_point), default) for result in probes]
        if any(value != values[0] for value in values):
            case[code_point] = _changes(values, default)
    normalization: dict[int, list[tuple[int, Any]]] = {}
    for code_point in sorted({int(key) for result in probes for key in result["normalization"]}):
        default = [chr(code_point)] * 2 + [0]
        values = [result["normalization"].get(str(code_point), default) for result in probes]
        if any(value != values[0] for value in values):
            normalization[code_point] = _changes(values, default)
    compositions: dict[tuple[int, int, int], int] = {}
    previous_compositions: set[tuple[int, int, int]] = set()
    for index, result in enumerate(probes):
        current_compositions = {(first, second, composite) for first, second, composite in result["compositions"]}
        if previous_compositions - current_compositions:
            msg = f"Python {result['python']} drops canonical compositions; the tables only add"
            raise SystemExit(msg)
        for item in current_compositions - previous_compositions:
            compositions[item] = index
        previous_compositions = current_compositions
    first_compositions = {item for item, index in compositions.items() if index == 0}
    stdlib: dict[str, tuple[int, int]] = {}
    for name in sorted({name for result in probes for name in result["stdlib"]}):
        present = [index for index, result in enumerate(probes) if name in result["stdlib"]]
        if present != list(range(present[0], present[-1] + 1)):
            msg = f"The standard library module {name!r} leaves and returns; the tables hold one span"
            raise SystemExit(msg)
        stdlib[name] = (present[0], present[-1] + 1)
    return {
        "targets": [result["python"] for result in probes],
        "unicode": [result["unicode"] for result in probes],
        "sets": sets,
        "case": case,
        "normalization": normalization,
        "compositions": sorted(
            (index, *item) for item, index in compositions.items() if item not in first_compositions
        ),
        "stdlib": stdlib,
    }


def _hex_ranges(ranges: list[list[int]]) -> str:
    """Spell ranges with at least four hexadecimal digits, so that no code point reads as a word."""
    return " ".join(f"{start:04x}" if start == end else f"{start:04x}-{end:04x}" for start, end in ranges)


def _string(value: str) -> str:
    """Return a double-quoted literal that spells every non-ASCII character as an escape."""
    escaped = value.encode("unicode_escape").decode("ascii").replace('"', '\\"')
    return f'"{escaped}"'


def _wrapped(text: str, indent: str) -> list[str]:
    """Split space-separated text into string literal lines no wider than the line width."""
    lines: list[str] = []
    current = ""
    for token in text.split(" "):
        candidate = f"{current} {token}" if current else token
        if current and len(indent) + len(candidate) + 3 > LINE_WIDTH:
            lines.append(f'{indent}"{current} "')
            current = token
        else:
            current = candidate
    lines.append(f'{indent}"{current}"')
    return lines


def _tuple(items: list[str]) -> str:
    return f"({items[0]},)" if len(items) == 1 else f"({', '.join(items)})"


def _mapping(value: str, code_point: int) -> str:
    return '""' if value == chr(code_point) else _string(value)


def render(tables: dict[str, Any]) -> str:
    """Return the source of the data module."""
    targets = ", ".join(_string(target) for target in tables["targets"])
    unicode_versions = ", ".join(_string(version) for version in tables["unicode"])
    lines = [
        '"""The Unicode data of each target Python version, from the CPython interpreter of that version.',
        "",
        "Generated by scripts/build_target_python_tables.py; do not edit. Each character set holds, at the index of",
        "a target in `TARGETS`, the characters that target adds; a target has the characters at its index and below.",
        "Ranges are inclusive hexadecimal code points of at least four digits, separated by spaces.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Final",
        "",
        "__all__ = (",
        '    "ASSIGNED",',
        '    "CASE_CHANGES",',
        '    "COMPOSITION_CHANGES",',
        '    "IDENTIFIER_CONTINUE",',
        '    "IDENTIFIER_START",',
        '    "NORMALIZATION_CHANGES",',
        '    "NUMERIC",',
        '    "TARGETS",',
        '    "UNICODE_VERSIONS",',
        '    "WORD",',
        ")",
        "",
        f"TARGETS: Final = ({targets})",
        '"""The target Python versions, oldest first."""',
        f"UNICODE_VERSIONS: Final = ({unicode_versions})",
        '"""The Unicode version of each target."""',
    ]
    for name in sorted(SET_NAMES):
        lines.extend(("", f"{name.upper()}: Final[tuple[str, ...]] = ("))
        for ranges in tables["sets"][name]:
            if len(wrapped := _wrapped(_hex_ranges(ranges), "        ")) > 1:
                lines.extend(("    (", *wrapped, "    ),"))
            else:
                lines.append(f"    {wrapped[0].strip()},")
        lines.extend((")", f'"""{SET_DOCS[name]}"""'))
    lines.extend(("", "CASE_CHANGES: Final[dict[int, tuple[tuple[int, str, str, str, str, int], ...]]] = {"))
    for code_point, changes in tables["case"].items():
        items = [
            f"({index}, {', '.join(_mapping(value, code_point) for value in values[:4])}, {values[4]})"
            for index, values in changes
        ]
        lines.append(f"    0x{code_point:X}: {_tuple(items)},")
    lines.extend((
        "}",
        '"""Characters whose case data differs between targets: from each listed target on, the full uppercase,',
        "lowercase, titlecase and casefold mappings (empty for the character itself) and the Lowercase, Uppercase,",
        'Lt and Case_Ignorable bits. Before the first listed target a character has no case data."""',
        "",
        "NORMALIZATION_CHANGES: Final[dict[int, tuple[tuple[int, str, str, int], ...]]] = {",
    ))
    for code_point, changes in tables["normalization"].items():
        items = [
            f"({index}, {_mapping(values[0], code_point)}, {_mapping(values[1], code_point)}, {values[2]})"
            for index, values in changes
        ]
        lines.append(f"    0x{code_point:X}: {_tuple(items)},")
    lines.extend((
        "}",
        '"""Characters whose normalization data differs between targets: from each listed target on, the full',
        "canonical and compatibility decompositions (empty for the character itself) and the canonical combining",
        'class. Before the first listed target a character decomposes to itself with combining class 0."""',
        "",
        "COMPOSITION_CHANGES: Final[tuple[tuple[int, int, int, int], ...]] = (",
    ))
    lines.extend(
        f"    ({index}, 0x{first:X}, 0x{second:X}, 0x{composite:X}),"
        for index, first, second, composite in tables["compositions"]
    )
    lines.extend((
        ")",
        '"""Canonical compositions the first target lacks: the target that adds each, its two characters and the',
        'character they compose to. Hangul syllables compose by rule and are not listed."""',
        "",
    ))
    return "\n".join(lines)


def _rule_problems(version: str, sets: dict[str, list[list[int]]]) -> list[str]:
    """Compare the generator's target rules for the running interpreter's version with the running interpreter."""
    from datamodel_code_generator import PythonVersion  # noqa: PLC0415
    from datamodel_code_generator import _target_unicode as rules  # noqa: PLC0415, PLC2701
    from datamodel_code_generator import _target_unicode_data as data  # noqa: PLC0415, PLC2701

    target = PythonVersion(version)
    problems: list[str] = []
    assigned = sorted(_members(sets["assigned"]))
    text = "".join(chr(code_point) for code_point in assigned)
    if rules.replace_non_word(text, target) != re.sub(r"[\u00b9\u00b2\u00b3\u2074-\u2079]|\W", "_", text):
        problems.append("replace_non_word differs")
    for code_point in assigned:
        character = chr(code_point)
        if (
            rules.is_identifier(character, target) != character.isidentifier()
            or rules.is_identifier(f"a{character}", target) != f"a{character}".isidentifier()
            or rules.is_numeric(character, target) != character.isnumeric()
        ):
            problems.append(f"identifier or numeric rule differs for U+{code_point:04X}")
    words = rules.target_pattern(r"(?:(?!_)\w)+", target).findall(text)
    if words != re.findall(r"[^\W_]+", text):
        problems.append("target_pattern differs for \\w")
    identifier = rules.target_pattern("(?#identifier)", target)
    problems.extend(
        f"target_pattern differs for the identifier {value!a}"
        for value in (*map(chr, assigned[::31]), *(f"a{chr(code_point)}" for code_point in assigned[::31]))
        if (identifier.fullmatch(value) is not None) != value.isidentifier()
    )
    marks = ("\u0301", "\u0323", "\u0345", "\u05b0")
    unassigned = "\u0378\U0002fffe"
    case_characters = [chr(code_point) for code_point in (*data.CASE_CHANGES, *assigned[::97])] + list(unassigned)
    for character in case_characters:
        for value in (
            character,
            character * 2,
            f"A{character}{SIGMA}",
            f"A{SIGMA}{character}",
            f"{character}{SIGMA}",
            f"{SIGMA}{character}a",
            f"a{character}B",
            f"{character}a{SIGMA}",
        ):
            problems.extend(
                f"{method} differs for {value!a}"
                for method in ("upper", "lower", "title", "capitalize", "casefold")
                if getattr(rules, method)(value, target) != getattr(value, method)()
            )
            if rules.is_upper(value, target) != value.isupper():
                problems.append(f"isupper differs for {value!a}")
    special = sorted({
        chr(code_point)
        for code_point in (
            *data.NORMALIZATION_CHANGES,
            *(item for _, first, second, _ in data.COMPOSITION_CHANGES for item in (first, second)),
        )
    })
    combining = [character for character in special if unicodedata.combining(character)]
    values = [
        *(f"{character}{suffix}" for character in special for suffix in ("", *marks, "".join(marks), unassigned)),
        *(
            f"{hangul}{character}"
            for character in special
            for hangul in ("\u1100\u1161\u11a8", "\uac00", "\uac00\u11a8")
        ),
        *(f"a{mark}{character}" for character in special for mark in marks),
        *(f"{character}{other}" for character in special for other in combining),
        *(f"{chr(first)}{chr(second)}" for _, first, second, _ in data.COMPOSITION_CHANGES),
        *(f"{chr(first)}\u0323{chr(second)}" for _, first, second, _ in data.COMPOSITION_CHANGES),
        *(f"{chr(composite)}{mark}" for *_, composite in data.COMPOSITION_CHANGES for mark in marks),
    ]
    for value in values:
        for form, method in (("NFC", rules.nfc), ("NFKC", rules.nfkc)):
            if method(value, target) != unicodedata.normalize(form, value):
                problems.append(f"{form} differs for {value!a}")
        if rules.is_nfkc(value, target) != unicodedata.is_normalized("NFKC", value):
            problems.append(f"NFKC check differs for {value!a}")
    return problems


def render_stdlib(tables: dict[str, Any]) -> str:
    """Return the source of the standard library module name module."""
    targets = ", ".join(_string(target) for target in tables["targets"])
    lines = [
        '"""The top-level standard library module names of each target Python version, from its CPython interpreter.',
        "",
        "Generated by scripts/build_target_python_tables.py; do not edit.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Final",
        "",
        '__all__ = ("STDLIB_MODULE_NAMES", "TARGETS")',
        "",
        f"TARGETS: Final = ({targets})",
        '"""The target Python versions, oldest first."""',
        "",
        "STDLIB_MODULE_NAMES: Final[dict[str, tuple[int, int]]] = {",
        *(f"    {_string(name)}: ({since}, {until})," for name, (since, until) in tables["stdlib"].items()),
        "}",
        '"""Each name of `sys.stdlib_module_names` with the index in `TARGETS` of the first target that has it and of',
        'the first target after it that no longer does."""',
        "",
    ]
    return "\n".join(lines)


def _check_interpreter() -> list[str]:
    """Compare the tables and rules of the running interpreter's version with the running interpreter."""
    sys.path.insert(0, str(ROOT / "src"))
    from datamodel_code_generator import _target_stdlib_data as stdlib  # noqa: PLC0415, PLC2701
    from datamodel_code_generator import _target_unicode_data as data  # noqa: PLC0415, PLC2701

    problems: list[str] = []
    if _targets() != data.TARGETS:
        problems.append(f"tables for {', '.join(data.TARGETS)}, targets {', '.join(_targets())}")
    version = "{}.{}".format(*sys.version_info[:2])
    if version not in data.TARGETS:
        return [*problems, f"no tables for Python {version}"]
    index = data.TARGETS.index(version)
    if data.UNICODE_VERSIONS[index] != unicodedata.unidata_version:
        problems.append(f"Unicode {data.UNICODE_VERSIONS[index]} recorded, {unicodedata.unidata_version} running")
    result = probe()
    if {name for name, (since, until) in stdlib.STDLIB_MODULE_NAMES.items() if since <= index < until} != set(
        sys.stdlib_module_names
    ):
        problems.append("stdlib_module_names differs")
    for name in SET_NAMES:
        recorded = set().union(*(_members(_parse_ranges(text)) for text in getattr(data, name.upper())[: index + 1]))
        if recorded != _members(result["sets"][name]):
            problems.append(f"{name} differs")
    return [*problems, *_rule_problems(version, result["sets"])]


def _parse_ranges(text: str) -> list[list[int]]:
    ranges: list[list[int]] = []
    for token in text.split():
        start, _, end = token.partition("-")
        ranges.append([int(start, 16), int(end or start, 16)])
    return ranges


def main(argv: list[str] | None = None) -> int:
    """Write the data modules, or report what differs from the interpreters' answers."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail if a data module is stale")
    parser.add_argument("--check-interpreter", action="store_true", help="check the running interpreter's tables")
    parser.add_argument("--python", action="append", default=[], metavar="VERSION=PATH", help="interpreter to use")
    parser.add_argument("--probe", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.probe:
        json.dump(probe(), sys.stdout)
        return 0
    if args.check_interpreter:
        problems = _check_interpreter()
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    given = dict(item.split("=", 1) for item in args.python)
    targets = _targets()
    with ThreadPoolExecutor(max_workers=3) as executor:
        probes = list(executor.map(lambda version: _run_probe(version, _interpreter(version, given)), targets))
    tables = merge(probes)
    sources = {OUTPUT: render(tables), STDLIB_OUTPUT: render_stdlib(tables)}
    if not args.check:
        for path, source in sources.items():
            path.write_text(source, encoding="utf-8", newline="\n")
        return 0
    stale = [
        path for path, source in sources.items() if not path.is_file() or path.read_text(encoding="utf-8") != source
    ]
    for path in stale:
        print(path.relative_to(ROOT).as_posix(), file=sys.stderr)
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
