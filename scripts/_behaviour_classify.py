"""Structured behaviour-signature diff and mechanical cell classification, for scripts/behaviour_diff.py.

`diff(a, b)` lists the changes between two signatures of `_behaviour_signature.py`; `classify_cell` puts one cell
(input x backend x option set) into one class:

- `same`: equal output bytes
- `no-behaviour`: the output differs, the signatures and payload verdicts do not
- `crash-fix`: the release failed to generate or import, or every change is on a name the release could not use
  (no validator, a validator that raised on its own schema's example value where the candidate's does not, failed
  introspection, dcg's internal placeholder as a value) or on a payload it crashed on; a candidate that refuses to
  generate an output the release wrote but could not use (no module that defines a name imports, a syntax error)
  fixes it too
- `accepted`: every behaviour change matches an owner-approved allowlist entry; a candidate that refuses an output
  the release's output imported is accepted by an entry with the aspect `generation` (its change line is
  `[generation] : now fails: <error>`)
- `violation`: any other behaviour change, or the candidate fails where the release's output imported
- `both-fail`: both sides fail
- `base-skipped`: the release is known to hang on the input (`[[base_skip]]`), so it did not run; the candidate
  generated and imported (a candidate that fails there is a `violation`)

The change records judge a cell whose facts differ (`kinds`): `covers` finds the records' `[[change.cells]]` selectors
that name it, and `judge` decides whether the records that cover it admit every kind of its difference with the
evidence of their categories (`CellEvidence`, its change lines from `change_lines`, `rule_b` for `output-text`).
`scripts/behaviour_diff.py` judges every differing cell of a gate run this way; `classify_cell` gives the class its
evidence reads and the class each cell's row reports.

Pure functions only: no file system access apart from `Allowlist.load`.
"""

from __future__ import annotations

import ast
import fnmatch
import itertools
import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from keyword import iskeyword
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import tomllib

from scripts._behaviour_signature import MSGSPEC_CONFIG_KEYS, PYDANTIC_CONFIG_KEYS
from scripts._change_records import ASPECTS, RULES, Category, CellKind, split_options
from scripts._source_model import SourceModelError, SourceModule

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Sequence

    from scripts._change_records import CellSelector, Record
    from scripts._source_model import Scope
    from scripts.compare_release import Facts

NUMBERED = re.compile(r"^.*?[A-Za-z_]\d+$")
INFO_ASPECTS = frozenset({"name-added", "module-added"})
SENTINEL = "-datamodel-code-generator-#"
DECISION = re.compile(r"#\d+(?:, #\d+)*")
BASE_SPEC = re.compile(r"(<=)?(\d+(?:\.\d+)*)")
ITEM_KEYS = (
    ("bases", "bases"),
    ("root", "type"),
    ("type", "type"),
    ("enum_base", "enum-base"),
    ("config", "config"),
    ("readonly", "readonly"),
    ("default", "default"),
    ("signature", "signature"),
    ("methods", "signature"),
)
FIELD_KEYS = ("required", "alias", "validation_alias", "serialization_alias", "default")
CLASSES = ("same", "no-behaviour", "crash-fix", "accepted", "violation", "both-fail", "opt-in", "base-skipped")
SIGNATURE_CONFIG_KEYS = frozenset((*PYDANTIC_CONFIG_KEYS, *MSGSPEC_CONFIG_KEYS))
"""The configuration keys a signature lists; rule (b) compares the others' values."""
SIGNATURE_FAILED = "signature_failed"
"""The key of a signature that could not be taken: a timeout or unreadable output, not a module's import error."""


@dataclass(frozen=True)
class Change:
    """One behaviour aspect that differs between the release and the candidate."""

    module: str
    item: str
    aspect: str
    detail: str
    field: str | None = None

    def __post_init__(self) -> None:
        """Refuse an aspect the change records cannot name (`_change_records.ASPECTS`)."""
        if self.aspect not in ASPECTS:
            msg = f"the aspect {self.aspect!r} is not in scripts/_change_records.py's ASPECTS"
            raise ValueError(msg)

    @property
    def behaviour(self) -> bool:
        """Whether the change can change behaviour (a new name or module cannot)."""
        return self.aspect not in INFO_ASPECTS

    def line(self) -> str:
        """Return the change as one report line."""
        location = ":".join(
            x for x in (self.module if self.module != "__init__" else "", self.item, self.field or "") if x
        )
        return f"[{self.aspect}] {location}: {self.detail}"


def _field_changes(module: str, item: str, old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    changes = [Change(module, item, "field-removed", "removed", name) for name in old if name not in new]
    changes += [
        Change(module, item, "field-added", "required" if new[name]["required"] else "optional", name)
        for name in new
        if name not in old
    ]
    for name, before in old.items():
        if name not in new or before == (after := new[name]):
            continue
        if before["type"] != after["type"]:
            changes.append(Change(module, item, "type", f"{before['type']} -> {after['type']}", name))
        elif before["ctype"] != after["ctype"]:
            changes.append(Change(module, item, "constraints", f"{before['ctype']} -> {after['ctype']}", name))
        changes += [
            Change(module, item, key, f"{before.get(key)!r} -> {after.get(key)!r}", name)
            for key in FIELD_KEYS
            if before.get(key) != after.get(key)
        ]
    return changes


def _member_changes(module: str, item: str, old: dict[str, str], new: dict[str, str]) -> list[Change]:
    changes = []
    for name, value in old.items():
        if name not in new:
            changes.append(Change(module, item, "enum-member-removed", name))
        elif value != new[name]:
            changes.append(Change(module, item, "enum-value", f"{name}={value} -> {new[name]}"))
    return changes + [Change(module, item, "enum-member-added", name) for name in new if name not in old]


def _order_changed(old: list[str] | None, new: list[str] | None) -> bool:
    if not old or new is None or old == new:
        return False
    common = [name for name in old if name in new]
    return common != [name for name in new if name in old] or len(old) != len(new)


def item_changes(module: str, name: str, old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    """Return the changes of one public name present on both sides."""
    if old == new:
        return []
    if old.get("kind") != new.get("kind"):
        return [Change(module, name, "kind", f"{old.get('kind')} -> {new.get('kind')}")]
    changes = [
        Change(module, name, aspect, f"{old.get(key)} -> {new.get(key)}")
        for key, aspect in ITEM_KEYS
        if old.get(key) != new.get(key)
    ]
    if old.get("root") == new.get("root") and old.get("croot") != new.get("croot"):
        changes.append(Change(module, name, "constraints", f"{old.get('croot')} -> {new.get('croot')}"))
    changes += _member_changes(module, name, old.get("members") or {}, new.get("members") or {})
    changes += _field_changes(module, name, old.get("fields") or {}, new.get("fields") or {})
    if _order_changed(old.get("positional"), new.get("positional")):
        changes.append(Change(module, name, "constructor-order", f"{old['positional']} -> {new['positional']}"))
    return changes


def module_changes(module: str, old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    """Return the changes between two signatures of one module."""
    old_items, new_items = old.get("items", {}), new.get("items", {})
    changes = [
        Change(module, name, "numbered-name-removed" if NUMBERED.match(name) else "name-removed", "removed")
        for name in sorted(set(old_items) - set(new_items))
    ]
    changes += [Change(module, name, "name-added", "added") for name in sorted(set(new_items) - set(old_items))]
    for name in sorted(set(old_items) & set(new_items)):
        changes += item_changes(module, name, old_items[name], new_items[name])
    return changes


def _modules(signature: dict[str, Any]) -> dict[str, Any]:
    return signature.get("package", {"__init__": signature})


def import_paths(signature: dict[str, Any]) -> dict[str, set[str]]:
    """Return the names importable from each module: its own public names and its re-exports.

    A submodule bound in a module (`from .app import list`) is importable by that name, so its binding counts;
    `diff` leaves it to `module-removed` when the module itself is gone, and drops it when the release used the module
    as a type (the binding was the bug).
    """
    return {
        module.removesuffix(".__init__"): set(sig.get("items", {})) | set(sig.get("exports", {}))
        for module, sig in _modules(signature).items()
    }


def diff(old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    """Return every change between two signatures, modules and re-exports included."""
    if "package" not in old and "package" not in new:
        return module_changes("__init__", old, new)
    old_modules, new_modules = _modules(old), _modules(new)
    old_paths, new_paths = import_paths(old), import_paths(new)
    new_names = {module.removesuffix(".__init__") for module in new_modules}
    changes = []
    for module in sorted(old_paths):
        old_sig = old_modules.get(module) or old_modules.get(f"{module}.__init__") or {}
        old_exports, old_text = old_sig.get("exports", {}), json.dumps(old_sig.get("items", {}))
        for name in sorted(old_paths[module] - new_paths.get(module, set())):
            source = old_exports.get(name, "")
            reported_as_removed = (
                module in new_modules
                and name in old_modules.get(module, {}).get("items", {})
                and name not in new_modules[module].get("items", {})
            ) or (
                source.startswith("<module ")
                and (target := source[len("<module ") : -1])
                and (target not in new_names or f".{target}>" in old_text)
            )
            if not reported_as_removed:
                changes.append(Change(module, name, "import-path-removed", f"from {module} import {name}"))
    for module in sorted(set(old_modules) | set(new_modules)):
        if module not in new_modules:
            changes.append(Change(module, "", "module-removed", "removed"))
        elif module not in old_modules:
            changes.append(Change(module, "", "module-added", "added"))
        else:
            changes += module_changes(module, old_modules[module], new_modules[module])
    return changes


def import_errors(signature: dict[str, Any]) -> dict[str, str]:
    """Return the import error of each module that failed to import."""
    return {module: sig["import_error"] for module, sig in _modules(signature).items() if "import_error" in sig}


def _importable_by_name(module: str) -> bool:
    """Return whether an import statement can name the module of the package at the dotted path ``module``."""
    return all(part.isidentifier() and not iskeyword(part) for part in module.split("."))


def unusable_output_error(signature: dict[str, Any]) -> str | None:
    """Return an import error of an output that has no working part, or None when a part works or none is known.

    A part works where its module imports, an import statement can name it (not below ``class/``), and it defines a
    name; an output whose signature could not be taken (``SIGNATURE_FAILED``: a timeout or unreadable output) is not
    known to fail.
    """
    if signature.get(SIGNATURE_FAILED) or not (errors := import_errors(signature)):
        return None
    working = (
        module
        for module, sig in _modules(signature).items()
        if "import_error" not in sig and sig.get("items") and _importable_by_name(module)
    )
    return None if next(working, None) is not None else errors[min(errors)]


def unusable_items(signature: dict[str, Any]) -> set[tuple[str, str]]:
    """Return the (module, name) pairs no validator can be built for, or whose introspection failed."""
    unusable = set()
    for module, sig in _modules(signature).items():
        for key in ("decoders", "adapters"):
            unusable |= {(module, name) for name, status in (sig.get(key) or {}).items() if status != "ok"}
        unusable |= {(module, name) for name, item in sig.get("items", {}).items() if item.get("kind") == "error"}
    return unusable


def probe_fixed(old: dict[str, Any], new: dict[str, Any]) -> set[tuple[str, str]]:
    """Return the names whose validator raised on the probe value in the release and no longer does (or is gone).

    A validator that raises on both sides, e.g. pydantic on a TypedDict whose `extra_items` it cannot resolve, gives no
    evidence: the change is judged like any other.
    """
    new_modules = _modules(new)
    fixed = set()
    for module, sig in _modules(old).items():
        new_probes = (new_modules.get(module) or {}).get("probes") or {}
        fixed |= {
            (module, name)
            for name, probe in (sig.get("probes") or {}).items()
            if probe.startswith("error:")
            and (
                name not in (new_modules.get(module) or {}).get("items", {})
                or (name in new_probes and not new_probes[name].startswith("error:"))
            )
        }
    return fixed


def probe_changes(old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    """Return the names whose validator now raises on, or rejects, the probe value the release accepted (`probe`)."""
    old_modules, new_modules = _modules(old), _modules(new)
    changes = []
    for module in sorted(set(old_modules) & set(new_modules)):
        old_probes, new_probes = old_modules[module].get("probes") or {}, new_modules[module].get("probes") or {}
        changes += [
            Change(module, name, "probe", f"{old_probes[name]} -> {probe}")
            for name, probe in sorted(new_probes.items())
            if name in old_probes
            and (
                (probe.startswith("error:") and not old_probes[name].startswith("error:"))
                or (probe == "reject" and old_probes[name] == "accept")
            )
        ]
    return changes


def payload_changes(old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    """Return the payloads whose verdict (`payload-verdict`) or parsed value (`parsed-value`) differs."""
    old_payloads, new_payloads = old.get("payloads") or {}, new.get("payloads") or {}
    old_verdicts, new_verdicts = old_payloads.get("verdicts") or {}, new_payloads.get("verdicts") or {}
    old_values, new_values = old_payloads.get("values") or {}, new_payloads.get("values") or {}
    changes = []
    for key, verdict in old_verdicts.items():
        if key not in new_verdicts:
            continue
        if verdict != new_verdicts[key]:
            changes.append(Change("__init__", f"payload {key}", "payload-verdict", f"{verdict} -> {new_verdicts[key]}"))
        elif old_values.get(key) != new_values.get(key):
            changes.append(Change("__init__", f"payload {key}", "parsed-value", "the parsed value differs"))
    return changes


def rejects_every_valid_payload(signature: dict[str, Any], valid_ids: frozenset[str]) -> bool:
    """Return whether a side accepts none of the schema-valid payloads (it could not be depended on)."""
    verdicts = (signature.get("payloads") or {}).get("verdicts") or {}
    checked = [verdicts[key] for key in valid_ids if key in verdicts]
    return bool(checked) and all(verdict != "accept" for verdict in checked)


@dataclass
class Allowlist:
    """Owner-accepted behaviour changes against one base release."""

    base: str | None = None
    entries: list[dict[str, Any]] = field(default_factory=list)
    base_skips: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Allowlist:
        """Read an allowlist file."""
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return cls(data.get("base"), list(data.get("accepted", [])), list(data.get("base_skip", [])))

    def skip_for(self, input_path: str, version: str) -> int | None:
        """Return the index of the first `[[base_skip]]` entry that skips the release `version` on this input."""
        for index, entry in enumerate(self.base_skips):
            if _names_input(entry, input_path) and any(_base_matches(spec, version) for spec in _skip_bases(entry)):
                return index
        return None

    def skip_applies(self, index: int, version: str) -> bool:
        """Return whether `[[base_skip]]` entry `index` names the release `version`."""
        return any(_base_matches(spec, version) for spec in _skip_bases(self.base_skips[index]))

    def covers(self, index: int, input_path: str, backend: str) -> bool:
        """Return whether entry `index` applies to a cell of this input and backend."""
        entry = self.entries[index]
        return _names_input(entry, input_path) and backend in entry.get("backends", [backend])

    def match(self, input_path: str, backend: str, change: Change) -> int | None:
        """Return the index of the first entry that accepts the change, or None."""
        for index, entry in enumerate(self.entries):
            if not self.covers(index, input_path, backend):
                continue
            if change.aspect not in entry.get("aspects", [change.aspect]):
                continue
            if (pattern := entry.get("match")) and not re.search(pattern, change.line()):
                continue
            return index
        return None

    def problems(self, version: str) -> list[str]:
        """Return what makes the file invalid for a gate against `version`, apart from stale entries."""
        found = [
            f"allowlist entry {index + 1} ({entry.get('input')}): missing {key}"
            for index, entry in enumerate(self.entries)
            for key in ("input", "reason", "decision")
            if not entry.get(key)
        ]
        found += [
            f"allowlist entry {index + 1} ({entry.get('input')}): decision {decision!r} is not a list of public "
            "PR or issue references (#1234, #1235)"
            for index, entry in enumerate(self.entries)
            if (decision := entry.get("decision")) and not DECISION.fullmatch(decision)
        ]
        if self.entries and self.base != version and (self.base is None or not _released_before(self.base, version)):
            found.append(f"allowlist base {self.base} is not {version}: replace the table when the base moves")
        return found + self._skip_problems()

    def _skip_problems(self) -> list[str]:
        found = []
        for index, entry in enumerate(self.base_skips):
            where = f"base_skip entry {index + 1} ({entry.get('input')})"
            found += [f"{where}: missing {key}" for key in ("input", "bases", "reason", "issue") if not entry.get(key)]
            if (issue := entry.get("issue")) and not DECISION.fullmatch(issue):
                found.append(f"{where}: issue {issue!r} is not a list of public issue references (#1234, #1235)")
            bases = entry.get("bases")
            if bases and not (
                isinstance(bases, list) and all(isinstance(spec, str) and BASE_SPEC.fullmatch(spec) for spec in bases)
            ):
                found.append(f"{where}: bases {bases!r} is not a list of versions (0.88.0) or maximums (<=0.88.0)")
        return found


def _names_input(entry: dict[str, Any], input_path: str) -> bool:
    """Return whether an entry names a cell's input: its `input` glob, or one of the globs of a list of them."""
    globs = entry.get("input", "")
    return any(fnmatch.fnmatch(input_path, glob) for glob in ([globs] if isinstance(globs, str) else globs))


def _skip_bases(entry: dict[str, Any]) -> list[Any]:
    """Return the `bases` of a `[[base_skip]]` entry, none where they are no list (`problems` reports that)."""
    return bases if isinstance(bases := entry.get("bases"), list) else []


def _base_matches(spec: str, version: str) -> bool:
    """Return whether a `bases` item, a version (`0.88.0`) or a maximum (`<=0.88.0`), names the release `version`."""
    if not isinstance(spec, str) or not (found := BASE_SPEC.fullmatch(spec)):
        return False
    try:
        wanted, actual = tuple(map(int, found[2].split("."))), tuple(map(int, version.split(".")))
    except ValueError:
        return False
    return actual <= wanted if found[1] else actual == wanted


def _released_before(base: str, version: str) -> bool:
    """Return whether `base` is a release before `version`, whose changes `version` already contains."""
    try:
        return tuple(map(int, base.split("."))) < tuple(map(int, version.split(".")))
    except ValueError:
        return False


@dataclass(frozen=True)
class Side:
    """What one side of a cell produced."""

    generated: bool
    message: str = ""
    signature: dict[str, Any] | None = None


@dataclass(frozen=True)
class Verdict:
    """The class of one cell, its report lines and the allowlist entries it used."""

    cls: str
    notes: tuple[str, ...] = ()
    accepted_by: frozenset[int] = frozenset()


def classify_cell(  # ruff: ignore[too-many-return-statements]
    base: Side,
    candidate: Side,
    *,
    input_path: str,
    backend: str,
    allowlist: Allowlist,
    same_bytes: bool = False,
    valid_payloads: frozenset[str] = frozenset(),
) -> Verdict:
    """Classify one cell from both sides' generation status and signatures."""
    if not base.generated and not candidate.generated:
        return Verdict("both-fail")
    if not base.generated:
        return Verdict("crash-fix", (f"generation fixed: {base.message[:120]}",))
    if not candidate.generated:
        if (broken := unusable_output_error(base.signature or {})) is not None:
            return Verdict("crash-fix", (f"generation refused an output that failed to import: {broken[:120]}",))
        refusal = Change("", "", "generation", f"now fails: {candidate.message}")
        if (index := allowlist.match(input_path, backend, refusal)) is not None:
            reason = allowlist.entries[index].get("reason", "")[:60]
            return Verdict("accepted", (f"accepted ({reason}) {refusal.line()[:200]}",), frozenset({index}))
        return Verdict("violation", (f"generation now fails: {candidate.message[:160]}",))
    if same_bytes:
        return Verdict("same")
    old, new = base.signature or {}, candidate.signature or {}
    old_errors, new_errors = import_errors(old), import_errors(new)
    if new_only := sorted(set(new_errors) - set(old_errors)):
        return Verdict("violation", (f"import now fails: {new_errors[new_only[0]][:160]}",))
    if fixed := sorted(set(old_errors) - set(new_errors)):
        return Verdict("crash-fix", (f"import fixed: {old_errors[fixed[0]][:120]}",))
    payloads = payload_changes(old, new)
    changes = [change for change in diff(old, new) if change.behaviour] + probe_changes(old, new) + payloads
    if not changes:
        return Verdict("both-fail" if old_errors else "no-behaviour")
    unusable = unusable_items(old) | probe_fixed(old, new)
    rejected_valid = (
        {f"payload {key}" for key in valid_payloads}
        if payloads and rejects_every_valid_payload(old, valid_payloads)
        else set()
    )
    rest: list[Change] = []
    notes: list[str] = []
    accepted: set[int] = set()
    for change in changes:
        if (
            (change.module, change.item) in unusable
            or SENTINEL in change.detail.split(" -> ")[0]
            or (
                change.aspect == "payload-verdict"
                and (change.detail.startswith("error:") or change.item in rejected_valid)
            )
        ):
            notes.append(f"crash-fix {change.line()}")
        elif (index := allowlist.match(input_path, backend, change)) is not None:
            accepted.add(index)
            notes.append(f"accepted ({allowlist.entries[index].get('reason', '')[:60]}) {change.line()}")
        else:
            rest.append(change)
    if rest:
        return Verdict("violation", (*(change.line() for change in rest), *notes), frozenset(accepted))
    if len(accepted_notes := [note for note in notes if note.startswith("accepted")]) < len(notes) or (
        unusable - unusable_items(new)
    ):
        return Verdict("crash-fix", tuple(notes), frozenset(accepted))
    return Verdict("accepted", tuple(accepted_notes), frozenset(accepted))


def classify_base_skipped(candidate: Side, *, note: str) -> Verdict:
    """Classify a cell whose release did not run (`[[base_skip]]`): only the candidate can fail it."""
    if not candidate.generated:
        return Verdict("violation", (f"generation fails: {candidate.message[:160]}", note))
    if errors := import_errors(candidate.signature or {}):
        return Verdict("violation", (f"import fails: {errors[min(errors)][:160]}", note))
    return Verdict("base-skipped", (note,))


class RunCell(Protocol):
    """What `covers` and `judge` read of a comparator cell (`behaviour_diff.Cell`)."""

    @property
    def label(self) -> str:
        """The input's path below the head tree."""

    @property
    def file_type(self) -> str:
        """The input file type the cell runs with."""

    @property
    def backend(self) -> str:
        """The output model type."""

    @property
    def options(self) -> tuple[str, ...]:
        """The option set."""


FACTS_LENGTH = 4
"""The elements of a side's facts: exit status, file hashes, warnings' hash, other facts' hash."""


def kinds(base: Facts, head: Facts) -> frozenset[CellKind]:
    """Return the kinds of the difference between two sides' facts of a cell (`compare_release.cell_facts`).

    The files' hashes (and the file set, which holds the outputs written outside the run's directory under the
    prefix `compare_release.OUTSIDE_PREFIX`) are its output, the exit status its exit, the warnings' hash its warnings
    and the other facts' hash its messages. Facts of another length are refused: an element no kind reads would never
    show.
    """
    if len(base) != FACTS_LENGTH or len(head) != FACTS_LENGTH:
        message = f"a cell's facts have {FACTS_LENGTH} elements, not {len(base)} and {len(head)}"
        raise ValueError(message)
    pairs = (
        (CellKind.EXIT, base[0], head[0]),
        (CellKind.OUTPUT, base[1], head[1]),
        (CellKind.WARNINGS, base[2], head[2]),
        (CellKind.MESSAGES, base[3], head[3]),
    )
    return frozenset(kind for kind, old, new in pairs if old != new)


def _warning_text(warning: Sequence[str]) -> str:
    category, message = (*warning, "", "")[:2]
    return f"{category}: {message}"


def change_lines(
    changes: Iterable[Change],
    *,
    exits: tuple[int, int] = (0, 0),
    warnings: tuple[Sequence[Sequence[str]], Sequence[Sequence[str]]] = ((), ()),
    messages: tuple[str, str] = ("", ""),
) -> tuple[str, ...]:
    """Return the change lines of a differing cell, which a decision's or a notice's selector must match.

    They are the behaviour changes of its signatures, then `[exit] <base> -> <head>`, a `[warning]` line for each
    warning (category, message, module) one side shows more often than the other and one where only their order
    differs, and the same for the lines of their messages: a `[message]` line for each line one side prints more
    often than the other, so a selector's `match` is checked on every differing line, and one where only their order
    differs. A no-behaviour output difference has no lines.
    """
    lines = [change.line() for change in changes if change.behaviour]
    if exits[0] != exits[1]:
        lines.append(f"[exit] {exits[0]} -> {exits[1]}")
    old, new = Counter(map(tuple, warnings[0])), Counter(map(tuple, warnings[1]))
    lines += [f"[warning] removed {_warning_text(item)}" for item in (old - new).elements()]
    lines += [f"[warning] added {_warning_text(item)}" for item in (new - old).elements()]
    if old == new and list(map(tuple, warnings[0])) != list(map(tuple, warnings[1])):
        lines.append("[warning] the same warnings in another order")
    old_lines, new_lines = Counter(messages[0].splitlines()), Counter(messages[1].splitlines())
    lines += [f"[message] removed {line}" for line in (old_lines - new_lines).elements()]
    lines += [f"[message] added {line}" for line in (new_lines - old_lines).elements()]
    if old_lines == new_lines and messages[0] != messages[1]:
        lines.append("[message] the same lines in another order")
    return tuple(lines)


def _glob_matches(pattern: Sequence[str], parts: Sequence[str]) -> bool:
    """Return whether path parts match glob parts as a glob over a tree does: `**` is any number of parts."""
    if not pattern:
        return not parts
    if pattern[0] == "**":
        return any(_glob_matches(pattern[1:], parts[index:]) for index in range(len(parts) + 1))
    return bool(parts) and fnmatch.fnmatchcase(parts[0], pattern[0]) and _glob_matches(pattern[1:], parts[1:])


GLOB_WILDCARD = re.compile(r"[*?[]")


def selector_paths(glob: str, root: Path) -> list[str]:
    """Return the paths below `root` that a selector's input glob names, by the matching of `covers`, sorted.

    The walk starts at the glob's parts before its first wildcard. A `**` names files and directories alike on every
    Python (`Path.glob` names only directories with a final `**` before 3.13), and the walk follows no directory
    symlink, as `**` does not.
    """
    pattern = glob.split("/")
    fixed = list(itertools.takewhile(lambda part: not GLOB_WILDCARD.search(part), pattern))
    start = root.joinpath(*fixed)
    found = [start] if fixed and start.exists() else []
    if len(fixed) < len(pattern) and start.is_dir():
        for directory, names, files in os.walk(start):
            found += [Path(directory, name) for name in (*names, *files)]
    labels = {path.relative_to(root).as_posix() for path in found}
    return sorted(label for label in labels if _glob_matches(pattern, label.split("/")))


def covers(selector: CellSelector, cell: RunCell) -> bool:
    """Return whether a `[[change.cells]]` selector names a run cell.

    Its input path matches one of the selector's globs as the glob expands over the head tree (`*` within a path
    part, `**` across parts: `selector_paths`), its backend and its option set (in either spelling, `split_options`)
    are listed (or every one is, `"*"`), and its input file type is the selector's; a selector that names none covers
    the cells whose type is their folder's below `tests/data/` (a cell that runs its input with another type, such as
    a cli_doc example, needs a selector that names it).
    """
    parts = cell.label.split("/")
    below = parts[2:] if parts[:2] == ["tests", "data"] else []
    folder = below[0] if below else None
    return (
        any(_glob_matches(glob.split("/"), parts) for glob in selector.input)
        and (selector.backends is None or cell.backend in selector.backends)
        and (selector.options is None or split_options(cell.options) in selector.options)
        and cell.file_type == (folder if selector.input_file_type is None else selector.input_file_type)
    )


@dataclass(frozen=True)
class CellEvidence:
    """What the gate proved on one differing cell, which the evidence of each category reads (`judge`).

    `cls`: the cell's class from `classify_cell` without an allowlist; `lines`: its change lines (`change_lines`);
    `exits`: the base's and the head's exit status; `model_files`: every differing file is a model module;
    `rule_b`: rule (b) (`rule_b`) holds on every differing file; `payloads`: the payload cells of the cell give equal
    verdicts and parsed values (true where it has none); `signature_failed`: either side's signature could not be
    taken; `companion_equal`: the cell with the options of the experimental features removed is equal;
    `removed`: the options of the cell the head no longer has.
    """

    cls: str
    lines: tuple[str, ...] = ()
    exits: tuple[int, int] = (0, 0)
    model_files: bool = False
    rule_b: bool = False
    payloads: bool = False
    signature_failed: bool = False
    companion_equal: bool = False
    removed: frozenset[str] = frozenset()


DECISIVE = frozenset({Category.DEFAULT_FIX, Category.DECIDED_CHANGE, Category.EXPERIMENTAL, Category.CRASH_FIX})
"""The categories whose evidence, where it holds on a cell, covers the kinds they admit without `output-text`'s."""
DECISIONS = frozenset({Category.DEFAULT_FIX, Category.DECIDED_CHANGE})
"""The categories whose evidence is that a covering selector selects each change line of a cell."""
LINE_ASPECT = re.compile(r"^\[([a-z_-]+)\]")
KIND_ASPECTS = {CellKind.EXIT: "exit", CellKind.WARNINGS: "warning", CellKind.MESSAGES: "message"}
"""The aspect of the change lines that show a difference of each kind that has lines."""


def _line_matches(line: str, selector: CellSelector) -> bool:
    aspect = found[1] if (found := LINE_ASPECT.match(line)) else ""
    return (selector.aspects is None or aspect in selector.aspects) and (
        selector.match is None or re.search(selector.match, line) is not None
    )


def _unshown(found: frozenset[CellKind], lines: Sequence[str]) -> str | None:
    """Return the kinds of a difference no change line shows, or None: line-based evidence checks nothing for them."""
    shown = {match[1] for line in lines if (match := LINE_ASPECT.match(line))}
    unshown = [kind for kind, aspect in KIND_ASPECTS.items() if kind in found and aspect not in shown]
    return f"the {', '.join(unshown)} difference shows in no change line" if unshown else None


def _feature_applies(feature: str, cell: RunCell) -> bool:
    """Return whether an experimental feature is in a cell: its option is set, or the cell's input is of its type."""
    kind, _, name = feature.partition(".")
    if kind == "input-format":
        return cell.file_type == name
    return kind == "cli-option" and any(option.partition("=")[0] == f"--{name}" for option in cell.options)


def evidence_problem(
    record: Record,
    selectors: Collection[CellSelector],
    cell: RunCell,
    found: frozenset[CellKind],
    evidence: CellEvidence,
) -> str | None:
    """Return why a record's category's evidence does not hold on a differing cell its `selectors` cover, or None.

    `output-text`: a no-behaviour model output; `crash-fix`: the classifier's crash-fix (an exit difference from a
    failure to 0); a decision: every change line inside a covering selector's aspects and match; `experimental`: a
    feature of the record in the cell and an equal companion cell; `notice`: equal output and exit, every warning and
    message line matching; `api`: a removed option the record names, which the head refuses. Other categories cover no
    cell. A decision and a notice prove nothing where a signature could not be taken, or where an exit, warning or
    message difference shows in no change line (the exit, warning and message lines, `change_lines`): the cell is
    unproved for them. That the kinds a category does not admit are equal is `judge`'s check over every covering
    record, not a record's evidence.
    """
    problems: tuple[tuple[bool, str], ...] = ((True, f"category {record.category} covers no cell"),)
    unshown = _unshown(found, evidence.lines)
    match record.category:
        case Category.OUTPUT_TEXT:
            problems = (
                (not evidence.model_files, "a differing file is no model module"),
                (evidence.signature_failed, "a signature could not be taken"),
                (evidence.cls != "no-behaviour", f"the cell's class is {evidence.cls}, not no-behaviour"),
                (not evidence.rule_b, "rule (b) does not hold on a differing file"),
                (not evidence.payloads, "the payload cells' verdicts or parsed values differ"),
            )
        case Category.CRASH_FIX:
            problems = (
                (evidence.cls != "crash-fix", f"the cell's class is {evidence.cls}, not crash-fix"),
                (
                    CellKind.EXIT in found and not (evidence.exits[0] != 0 and evidence.exits[1] == 0),
                    f"the exit status went {evidence.exits[0]} -> {evidence.exits[1]}, not from a failure to 0",
                ),
            )
        case Category.DEFAULT_FIX | Category.DECIDED_CHANGE:
            outside = [line for line in evidence.lines if not any(_line_matches(line, item) for item in selectors)]
            problems = (
                (evidence.signature_failed, "a signature could not be taken, so the change lines are unknown"),
                (bool(outside), f"a change line is outside its aspects and match: {outside[:1]}"),
                (unshown is not None, unshown or ""),
            )
        case Category.EXPERIMENTAL:
            problems = (
                (
                    not any(_feature_applies(feature, cell) for feature in record.features),
                    "no feature of it is in the cell",
                ),
                (not evidence.companion_equal, "the cell without the feature's options differs too: it needs a record"),
            )
        case Category.NOTICE:
            notices = [line for line in evidence.lines if line.startswith(("[warning]", "[message]"))]
            outside = [line for line in notices if not any(_line_matches(line, item) for item in selectors)]
            problems = (
                (evidence.signature_failed, "a signature could not be taken, so the change lines are unknown"),
                (bool(outside), f"a warning or message line is outside its match: {outside[:1]}"),
                (unshown is not None, unshown or ""),
            )
        case Category.API:
            named = set(dict(record.affects).get("options", ()))
            problems = (
                (not evidence.removed & named, "the cell sets no removed option the record names"),
                (evidence.exits[1] == 0, "the head still generates the cell"),
            )
    return next((problem for failed, problem in problems if failed), None)


def _admits(record: Record) -> frozenset[CellKind]:
    """Return the kinds of a cell's difference a record's category admits; a record without a category admits none."""
    return frozenset() if record.category is None else RULES[record.category].cells.kinds


def judge(cell: RunCell, found: frozenset[CellKind], records: Sequence[Record], evidence: CellEvidence) -> Verdict:
    """Return whether the records cover a differing cell: `accepted`, by the indices of the records that cover it.

    `records` are those whose selectors may cover the cell (an extending record passed as its target with its own id and
    cells); a record covers it where one of its selectors does (`covers`). The covering records must admit every kind of
    the difference (`found`), the union of their categories' kinds, so a kind one record's category does not admit needs
    to be equal only where no other covering record admits it (`output-text` beside `notice` covers a no-behaviour text
    change with a new warning), and the covering decision records (`DECISIONS`) select the change lines together: each
    line must be inside the aspects and match of a covering selector of one of them (two changes of one release each
    select their own lines). Where a covering record of a decisive category (`DECISIVE`) holds its evidence, the kinds
    it admits need no other evidence, `output-text`'s included; every other covering record that admits a kind left over
    must hold its evidence. Else the cell is a `violation` with the reasons.
    """
    if not found:
        return Verdict("violation", ("the cell has no differing kind to judge",))
    covering = {
        index: selectors
        for index, record in enumerate(records)
        if (selectors := [selector for selector in record.cells if covers(selector, cell)])
    }
    if not covering:
        return Verdict("violation", ("no record's [[change.cells]] covers the cell",))
    admitted = {index: _admits(records[index]) & found for index in covering}
    if missing := found - set().union(*admitted.values()):
        return Verdict(
            "violation", tuple(f"no covering record admits the cell's {kind} difference" for kind in sorted(missing))
        )
    decisions = [selector for index in covering if records[index].category in DECISIONS for selector in covering[index]]
    problems = {
        index: evidence_problem(
            records[index],
            decisions if records[index].category in DECISIONS else covering[index],
            cell,
            found,
            evidence,
        )
        for index in admitted
        if admitted[index]
    }
    settled = set().union(
        *(
            admitted[index]
            for index, problem in problems.items()
            if problem is None and records[index].category in DECISIVE
        )
    )
    failing = [index for index, problem in problems.items() if problem is not None and not admitted[index] <= settled]
    if failing:
        return Verdict(
            "violation",
            tuple(f"{records[index].id} ({records[index].category}): {problems[index]}" for index in failing),
        )
    holding = frozenset(index for index, problem in problems.items() if problem is None)
    return Verdict("accepted", tuple(f"covered by {records[index].id}" for index in sorted(holding)), holding)


def _read(text: str) -> SourceModule:
    """Return the model of a module's text with the docstring of the module, each class and each function dropped."""
    model = SourceModule(text, "<rule (b)>")
    for scope in model.scopes:
        body = getattr(scope.node, "body", None) if scope.kind in {"module", "class", "function"} else None
        if (
            isinstance(body, list)
            and body
            and isinstance(first := body[0], ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            body[:] = body[1:] or [ast.Pass()]
    return model


def _text_free(model: SourceModule) -> list[tuple[str, str | tuple[tuple[str, ...], ...]]]:
    """Return a module's top-level statements in order as dumps; each run of consecutive imports is sorted.

    An import never moves past another statement: the statement may bind a name the import rebinds, or the reverse.
    """
    top = {id(node) for node in model.tree.body}
    items: dict[int, list[tuple[str, ...]]] = {}
    for item in model.imports:
        if id(item.node) in top:
            items.setdefault(id(item.node), []).append((
                item.kind,
                str(item.level),
                str(getattr(item.node, "module", "")),
                ast.dump(item.alias or item.node),
            ))
    found: list[tuple[str, str | tuple[tuple[str, ...], ...]]] = []
    run: list[tuple[str, ...]] = []
    for node in model.tree.body:
        if id(node) in items:
            run += items[id(node)]
            continue
        if run:
            found.append(("imports", tuple(sorted(run))))
            run = []
        found.append(("statement", ast.dump(node)))
    if run:
        found.append(("imports", tuple(sorted(run))))
    return found


def _union_members(node: ast.AST) -> list[ast.expr] | None:
    """Return the flattened members of a union (`Union[...]`, `Optional[...]`, a `|` chain) in order, else None."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return [*_flat(node.left), *_flat(node.right)]
    if isinstance(node, ast.Subscript):
        name = node.value.attr if isinstance(node.value, ast.Attribute) else getattr(node.value, "id", "")
        if name == "Union":
            arguments = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            return [member for argument in arguments for member in _flat(argument)]
        if name == "Optional":
            return [*_flat(node.slice), ast.Constant(value=None)]
    return None


def _flat(node: ast.expr) -> list[ast.expr]:
    return _union_members(node) or [node]


def _keyword_values(keywords: Iterable[ast.keyword]) -> list[tuple[str, ast.AST]]:
    return [(keyword.arg or "**", keyword.value) for keyword in keywords]


def _config_values(scope: Scope) -> list[tuple[str, ast.AST]]:
    """Return a class's configuration as (key, value): class keywords, `model_config` entries, `class Config` names."""
    found = _keyword_values(getattr(scope.node, "keywords", []))
    for child in scope.children:
        if child.kind == "class" and child.code_name == "Config":
            found += [
                (name, binding.value)
                for name, bindings in child.bindings.items()
                for binding in bindings
                if binding.value is not None
            ]
    for binding in scope.bindings.get("model_config", []):
        if isinstance(value := binding.value, ast.Call):
            found += _keyword_values(value.keywords)
        elif isinstance(value, ast.Dict):
            found += [
                (str(key.value) if isinstance(key, ast.Constant) else "**", item)
                for key, item in zip(value.keys, value.values, strict=True)
            ]
    return found


def _unions(node: ast.AST) -> Iterable[tuple[ast.AST, list[ast.expr]]]:
    """Yield each union below a node with its members (a union nested in a union is part of the outer one)."""
    if (members := _union_members(node)) is not None:
        yield node, members
        for member in members:
            yield from _unions(member)
    else:
        for child in ast.iter_child_nodes(node):
            yield from _unions(child)


def _where(scope: Scope) -> str:
    """Return where a scope is: its qualified name and its ordinal, as the source model pairs scopes across texts."""
    return f"{scope.qualname}#{scope.ordinal}"


def _union_parts(model: SourceModule) -> Iterable[str]:
    """Yield the members of each union in their order, each with where it is.

    A union is where the statement that holds it is: its scope and the names the statement binds there (a field's
    name, a function's), and its index among the unions of statements that bind the same names in that scope.
    """
    bound: dict[tuple[int, int], list[str]] = {}
    for binding in model.bindings():
        bound.setdefault((id(binding.scope), id(model.statement_at(binding.line))), []).append(binding.name)
    seen: Counter[str] = Counter()
    for node, members in _unions(model.tree):
        scope = model.scope_of(node)
        statement = model.statement_at(getattr(node, "lineno", 0))
        at = f"{_where(scope)} {','.join(sorted(bound.get((id(scope), id(statement)), [])))}"
        yield f"union {at} {seen[at]} {' | '.join(map(ast.unparse, members))}"
        seen[at] += 1


def _unlisted_parts(model: SourceModule) -> Counter[str]:
    """Return what a signature does not see, as a multiset of dumps, each with where it is.

    They are function bodies, lambdas, decorators, configuration keys and the members of each union in their order
    (a signature sorts them, and validation tries them in order). Each is where its scope is (`_where`: a field's
    lambda by its index among the class's lambdas, a method by its index among the methods of its name), or a union
    where its statement is (`_union_parts`), so two fields or methods never trade them unseen, and moving a class
    keeps them.

    A configuration key counts unless the signature lists it (`_behaviour_signature`'s pydantic and msgspec keys).
    """
    parts: Counter[str] = Counter(_union_parts(model))
    for scope in model.scopes:
        node, at = scope.node, _where(scope)
        if scope.kind == "function":
            parts[f"body {at} {ast.dump(ast.Module(body=getattr(node, 'body', []), type_ignores=[]))}"] += 1
        if scope.kind == "lambda":
            parts[f"lambda {at} {ast.dump(node)}"] += 1
        parts.update(f"decorator {at} {ast.dump(item)}" for item in getattr(node, "decorator_list", []))
        if scope.kind == "class":
            parts.update(
                f"config {at} {key} {ast.dump(value)}"
                for key, value in _config_values(scope)
                if key not in SIGNATURE_CONFIG_KEYS
            )
    return parts


def rule_b(old: str, new: str, *, signature_equal: bool) -> bool:
    """Return whether two texts of a model module behave the same by rule (b) (owner, 2026-11-17).

    Their syntax trees are equal apart from docstrings, comments and the order of the top-level imports within a run of
    consecutive imports; or else the signatures are equal (`signature_equal`) and no function body, lambda, decorator
    or configuration key the signature does not list differs, nor the order of the members of a union, nor where one of
    them is (the statement that holds it), compared as multisets of their dumps. A text that does not parse holds
    nothing.
    """
    try:
        old_model, new_model = _read(old), _read(new)
    except (SyntaxError, SourceModelError):
        return False
    if _text_free(old_model) == _text_free(new_model):
        return True
    return signature_equal and _unlisted_parts(old_model) == _unlisted_parts(new_model)
