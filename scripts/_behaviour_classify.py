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

Pure functions only: no file system access apart from `Allowlist.load`.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from keyword import iskeyword
from typing import TYPE_CHECKING, Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib


if TYPE_CHECKING:
    from pathlib import Path

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
)
FIELD_KEYS = ("required", "alias", "validation_alias", "serialization_alias", "default")
CLASSES = ("same", "no-behaviour", "crash-fix", "accepted", "violation", "both-fail", "opt-in", "base-skipped")
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
        if self.entries and self.base != version and not _released_before(self.base, version):
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


def classify_cell(  # ruff: ignore[too-many-arguments, too-many-return-statements]
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
