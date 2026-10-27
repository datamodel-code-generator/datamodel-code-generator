"""Structured behaviour-signature diff and mechanical cell classification, for scripts/behaviour_diff.py.

`diff(a, b)` lists the changes between two signatures of `_behaviour_signature.py`; `classify_cell` puts one cell
(input x backend x option set) into one class:

- `same`: equal output bytes
- `no-behaviour`: the output differs, the signatures and payload verdicts do not
- `crash-fix`: the release failed to generate or import, or every change is on a name the release could not use
  (no validator, failed introspection, dcg's internal placeholder as a value) or on a payload it crashed on
- `accepted`: every behaviour change matches an owner-approved allowlist entry
- `violation`: any other behaviour change, or the candidate fails where the release worked
- `both-fail`: both sides fail

Pure functions only: no file system access apart from `Allowlist.load`.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
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
CLASSES = ("same", "no-behaviour", "crash-fix", "accepted", "violation", "both-fail", "opt-in")


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
    """Return the names importable from each module: its own public names and its re-exports."""
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
    changes = []
    for module in sorted(old_paths):
        for name in sorted(old_paths[module] - new_paths.get(module, set())):
            reported_as_removed = (
                module in new_modules
                and name in old_modules.get(module, {}).get("items", {})
                and name not in new_modules[module].get("items", {})
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


def unusable_items(signature: dict[str, Any]) -> set[tuple[str, str]]:
    """Return the (module, name) pairs no validator can be built for, or whose introspection failed."""
    unusable = set()
    for module, sig in _modules(signature).items():
        for key in ("decoders", "adapters"):
            unusable |= {(module, name) for name, status in (sig.get(key) or {}).items() if status != "ok"}
        unusable |= {(module, name) for name, item in sig.get("items", {}).items() if item.get("kind") == "error"}
    return unusable


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

    @classmethod
    def load(cls, path: Path) -> Allowlist:
        """Read an allowlist file."""
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return cls(data.get("base"), list(data.get("accepted", [])))

    def covers(self, index: int, input_path: str, backend: str) -> bool:
        """Return whether entry `index` applies to a cell of this input and backend."""
        entry = self.entries[index]
        return fnmatch.fnmatch(input_path, entry.get("input", "")) and backend in entry.get("backends", [backend])

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
        if self.entries and self.base != version:
            found.append(f"allowlist base {self.base} is not {version}: replace the table when the base moves")
        return found


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
        return Verdict("violation", (f"generation now fails: {candidate.message[:160]}",))
    if same_bytes:
        return Verdict("same")
    old, new = base.signature or {}, candidate.signature or {}
    old_errors, new_errors = import_errors(old), import_errors(new)
    if new_only := sorted(set(new_errors) - set(old_errors)):
        return Verdict("violation", (f"import now fails: {new_errors[new_only[0]][:160]}",))
    if fixed := sorted(set(old_errors) - set(new_errors)):
        return Verdict("crash-fix", (f"import fixed: {old_errors[fixed[0]][:120]}",))
    if old_errors:
        return Verdict("both-fail")
    payloads = payload_changes(old, new)
    changes = [change for change in diff(old, new) if change.behaviour] + payloads
    if not changes:
        return Verdict("no-behaviour")
    unusable = unusable_items(old)
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
