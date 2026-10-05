"""Central registry for deprecated and scheduled-breaking behavior."""

from __future__ import annotations

import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Literal

from datamodel_code_generator._registry_render import _render_registry_json, _render_registry_table

if TYPE_CHECKING:
    from collections.abc import Generator

DeprecationKind = Literal["cli-option", "python-api", "config", "behavior", "schema", "dependency"]
DeprecationStatus = Literal["active", "scheduled"]
DeprecationFormat = Literal["table", "json", "markdown"]
DeprecationId = Literal[
    "behavior.pydantic-v2-use-annotated-default",
    "behavior.remote-ref-default",
    "cli.allow-extra-fields",
    "cli.parent-scoped-naming",
    "cli.set-default-enum-member",
    "cli.validation",
    "config.yaml-non-lowercase-bool",
    "config.json-config-strict-validation",
    "dependency.external-formatters-optional",
    "dependency.black-minimum",
    "dependency.isort-minimum",
    "dependency.pydantic-runtime-minimum",
    "format.default-formatters",
    "python-api.python-version-has-type-alias",
    "schema.jsonschema-items-array",
    "schema.openapi-nullable",
]


@dataclass(frozen=True)
class Deprecation:
    """Structured metadata for a deprecation entry."""

    id: DeprecationId
    kind: DeprecationKind
    target: str
    message: str
    warning_since: str
    removal_version: str | None
    replacement: str | None = None
    status: DeprecationStatus = "active"
    warning_category: str = "DeprecationWarning"
    note: str | None = None


DEPRECATIONS: dict[DeprecationId, Deprecation] = {
    "cli.allow-extra-fields": Deprecation(
        id="cli.allow-extra-fields",
        kind="cli-option",
        target="--allow-extra-fields",
        message="--allow-extra-fields is deprecated. Use --extra-fields=allow instead.",
        warning_since="0.31.0",
        removal_version=None,
        replacement="--extra-fields=allow",
        note="The replacement supports allow, forbid, and ignore modes.",
    ),
    "cli.parent-scoped-naming": Deprecation(
        id="cli.parent-scoped-naming",
        kind="cli-option",
        target="--parent-scoped-naming",
        message="--parent-scoped-naming is deprecated. Use --naming-strategy parent-prefixed instead.",
        warning_since="0.48.0",
        removal_version=None,
        replacement="--naming-strategy parent-prefixed",
    ),
    "cli.set-default-enum-member": Deprecation(
        id="cli.set-default-enum-member",
        kind="cli-option",
        target="--set-default-enum-member",
        message=("--set-default-enum-member is deprecated. Use --deserialize-default-values enum instead."),
        warning_since="0.76.0",
        removal_version=None,
        replacement="--deserialize-default-values enum",
        status="scheduled",
    ),
    "cli.validation": Deprecation(
        id="cli.validation",
        kind="cli-option",
        target="--validation",
        message=(
            "The `--validation` option is deprecated and will be removed in a future release. "
            "Use --field-constraints instead."
        ),
        warning_since="0.24.0",
        removal_version=None,
        replacement="--field-constraints",
    ),
    "behavior.pydantic-v2-use-annotated-default": Deprecation(
        id="behavior.pydantic-v2-use-annotated-default",
        kind="behavior",
        target="Pydantic v2 default for --use-annotated",
        message=(
            "Pydantic v2 with --use-annotated is recommended for correct type annotations. "
            "In a future version, --use-annotated will be enabled by default for Pydantic v2."
        ),
        warning_since="0.52.1",
        removal_version=None,
        replacement="Explicitly pass --use-annotated or --no-use-annotated.",
    ),
    "behavior.remote-ref-default": Deprecation(
        id="behavior.remote-ref-default",
        kind="behavior",
        target="Remote $ref fetching without --allow-remote-refs",
        message="Remote $ref fetching without --allow-remote-refs is deprecated.",
        warning_since="0.56.0",
        removal_version=None,
        replacement=(
            "Pass --allow-remote-refs for trusted remote schemas, or --no-allow-remote-refs to block HTTP(S) "
            "$ref fetching."
        ),
        warning_category="FutureWarning",
        note=(
            "The current default allows remote fetching for compatibility; the scheduled default is disabled. "
            "Private, loopback, link-local, and otherwise non-public network targets require --allow-private-network."
        ),
    ),
    "format.default-formatters": Deprecation(
        id="format.default-formatters",
        kind="behavior",
        target="Default formatters",
        message="Default formatters will change to builtin. Set --formatters or --preset explicitly; see --help.",
        warning_since="0.52.0",
        removal_version=None,
        replacement="Set formatters explicitly, for example black and isort or builtin.",
        warning_category="FutureWarning",
    ),
    "dependency.external-formatters-optional": Deprecation(
        id="dependency.external-formatters-optional",
        kind="dependency",
        target="Black/isort installation",
        message="Black/isort will become optional. Declare the corresponding extras; see --help.",
        warning_since="0.78.0",
        removal_version=None,
        replacement="Declare the black/isort extras for the external formatters you select.",
        warning_category="FutureWarning",
        note="This is advance notice only. Current dependency ranges and compatibility remain unchanged.",
    ),
    "dependency.black-minimum": Deprecation(
        id="dependency.black-minimum",
        kind="dependency",
        target="Black <24.3.0",
        message="Support for Black <24.3.0 will end in a future release.",
        warning_since="0.78.0",
        removal_version=None,
        replacement="Upgrade Black to >=24.3.0 before the later support change.",
        warning_category="FutureWarning",
        note="This is advance notice only. Current dependency ranges and compatibility remain unchanged.",
    ),
    "dependency.isort-minimum": Deprecation(
        id="dependency.isort-minimum",
        kind="dependency",
        target="isort <6",
        message="Support for isort <6 will end in a future release.",
        warning_since="0.78.0",
        removal_version=None,
        replacement="Upgrade isort to >=6,<9 before the later support change.",
        warning_category="FutureWarning",
        note="This is advance notice only. Current dependency ranges and compatibility remain unchanged.",
    ),
    "dependency.pydantic-runtime-minimum": Deprecation(
        id="dependency.pydantic-runtime-minimum",
        kind="dependency",
        target="DCG runtime Pydantic <2.8.2",
        message="Support for DCG runtime Pydantic <2.8.2 will end in a future release.",
        warning_since="0.78.0",
        removal_version=None,
        replacement="Upgrade DCG runtime Pydantic to >=2.8.2; generated-code targets are unchanged.",
        warning_category="FutureWarning",
        note="This is advance notice only. Current dependency ranges and compatibility remain unchanged.",
    ),
    "config.yaml-non-lowercase-bool": Deprecation(
        id="config.yaml-non-lowercase-bool",
        kind="config",
        target="YAML bool values True, False, TRUE, FALSE",
        message="Non-lowercase YAML bool values are deprecated. Use lowercase true or false instead.",
        warning_since="0.48.0",
        removal_version=None,
        replacement="Use lowercase true or false.",
    ),
    "config.json-config-strict-validation": Deprecation(
        id="config.json-config-strict-validation",
        kind="config",
        target="JSON configuration values accepted by legacy validation",
        message=(
            "JSON configuration values that do not match the documented schema are deprecated and will become "
            "validation errors in a future release."
        ),
        warning_since="0.64.2",
        removal_version=None,
        replacement="Update the JSON configuration to match --output-format-json-schema config.",
        warning_category="FutureWarning",
    ),
    "python-api.python-version-has-type-alias": Deprecation(
        id="python-api.python-version-has-type-alias",
        kind="python-api",
        target="PythonVersion.has_type_alias",
        message="has_type_alias is deprecated and will be removed in a future version.",
        warning_since="0.52.1",
        removal_version=None,
        replacement=None,
        note="The project minimum Python version already supports TypeAlias.",
    ),
    "schema.openapi-nullable": Deprecation(
        id="schema.openapi-nullable",
        kind="schema",
        target="OpenAPI 3.1 nullable keyword",
        message='nullable keyword is deprecated in OpenAPI 3.1, use type: ["string", "null"] instead.',
        warning_since="0.53.0",
        removal_version=None,
        replacement='Use type arrays such as type: ["string", "null"].',
    ),
    "schema.jsonschema-items-array": Deprecation(
        id="schema.jsonschema-items-array",
        kind="schema",
        target="JSON Schema Draft 2020-12 items array tuple validation",
        message="items as array tuple validation is deprecated in Draft 2020-12. Use prefixItems instead.",
        warning_since="0.53.0",
        removal_version=None,
        replacement="Use prefixItems.",
        warning_category="UserWarning",
    ),
}

_WARNING_CATEGORIES: dict[str, type[Warning]] = {
    "DeprecationWarning": DeprecationWarning,
    "FutureWarning": FutureWarning,
    "UserWarning": UserWarning,
}


_MIGRATION_WARNINGS = frozenset({
    "format.default-formatters",
    "dependency.external-formatters-optional",
    "dependency.black-minimum",
    "dependency.isort-minimum",
    "dependency.pydantic-runtime-minimum",
})
_CLI_MIGRATION_WARNINGS: ContextVar[set[DeprecationId] | None] = ContextVar("cli_migration_warnings", default=None)


@contextmanager
def cli_migration_warning_scope() -> Generator[None, None, None]:
    """Deduplicate migration notices within one CLI invocation, including batch/watch."""
    token = _CLI_MIGRATION_WARNINGS.set(set())
    try:
        yield
    finally:
        _CLI_MIGRATION_WARNINGS.reset(token)


@lru_cache(maxsize=16)
def _dependency_version_is_legacy(version: str, minimum: tuple[int, int, int]) -> bool:
    """Compare release numbers and prereleases without a transitive packaging dependency."""
    import re  # ruff: ignore[import-outside-top-level]

    if (match := re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?(.*)", version.partition("+")[0])) is None:
        return False
    major, minor, patch, suffix = match.groups(default="0")
    release = (int(major), int(minor), int(patch))
    return release < minimum or (release == minimum and re.match(r"(?:a|b|rc|\.?dev)", suffix) is not None)


def warn_legacy_dependency(deprecation_id: DeprecationId, version: str, minimum: tuple[int, int, int]) -> None:
    """Warn only when the dependency actually used is below its future runtime floor."""
    if _dependency_version_is_legacy(version, minimum):
        warn_deprecated(deprecation_id, stacklevel=2)


def iter_deprecations() -> tuple[Deprecation, ...]:
    """Return all deprecations in stable display order."""
    return tuple(sorted(DEPRECATIONS.values(), key=lambda item: (item.removal_version or "", item.kind, item.target)))


def get_deprecation(deprecation_id: DeprecationId) -> Deprecation:
    """Return a registered deprecation by id."""
    return DEPRECATIONS[deprecation_id]


def warn_deprecated(deprecation_id: DeprecationId, *, stacklevel: int = 2, details: str | None = None) -> None:
    """Emit a warning from the central registry."""
    if (deprecation := get_deprecation(deprecation_id)).status == "scheduled":
        return
    seen = _CLI_MIGRATION_WARNINGS.get() if deprecation_id in _MIGRATION_WARNINGS else None
    if seen is not None and deprecation_id in seen:
        return
    message = deprecation.message if details is None else f"{deprecation.message} {details}"
    warnings.warn(
        message,
        _WARNING_CATEGORIES[deprecation.warning_category],
        stacklevel=stacklevel,
    )
    if seen is not None:
        seen.add(deprecation_id)


def deprecation_message(deprecation_id: DeprecationId) -> str:
    """Return the user-facing message for a deprecation."""
    return get_deprecation(deprecation_id).message


def _format_removal_version(deprecation: Deprecation) -> str:
    """Return the display value for a removal version."""
    return deprecation.removal_version or "TBD"


def deprecation_as_dict(deprecation: Deprecation) -> dict[str, str | None]:
    """Serialize a deprecation entry to primitive values."""
    return asdict(deprecation)


def render_deprecations_json() -> str:
    """Render all deprecations as JSON."""
    return _render_registry_json(deprecation_as_dict(deprecation) for deprecation in iter_deprecations())


def render_deprecations_table() -> str:
    """Render all deprecations as a plain text table."""
    return _render_registry_table([
        [
            "ID",
            "Status",
            "Kind",
            "Target",
            "Since",
            "Removal",
            "Replacement",
        ],
        *[
            [
                deprecation.id,
                deprecation.status,
                deprecation.kind,
                deprecation.target,
                deprecation.warning_since,
                _format_removal_version(deprecation),
                deprecation.replacement or "-",
            ]
            for deprecation in iter_deprecations()
        ],
    ])


def render_deprecations_markdown(*, include_header: bool = True) -> str:
    """Render all deprecations as Markdown."""
    lines: list[str] = []
    if include_header:
        lines.extend([
            "# Deprecations",
            "",
            "<!-- Generated by scripts/build_deprecation_docs.py. Do not edit manually. -->",
            "",
            "This page lists deprecations and scheduled breaking changes.",
            "",
        ])
    lines.extend([
        "| ID | Status | Kind | Target | Since | Removal | Replacement |",
        "|----|--------|------|--------|-------|---------|-------------|",
    ])
    for deprecation in iter_deprecations():
        replacement = deprecation.replacement or "-"
        lines.append(
            f"| `{deprecation.id}` | {deprecation.status} | {deprecation.kind} | `{deprecation.target}` | "
            f"{deprecation.warning_since} | {_format_removal_version(deprecation)} | {replacement} |"
        )
    lines.extend(("", "## Details", ""))
    for deprecation in iter_deprecations():
        lines.extend([
            f"### `{deprecation.id}`",
            "",
            f"- **Status:** {deprecation.status}",
            f"- **Kind:** {deprecation.kind}",
            f"- **Target:** `{deprecation.target}`",
            f"- **Since:** {deprecation.warning_since}",
            f"- **Planned removal:** {_format_removal_version(deprecation)}",
            f"- **Warning category:** `{deprecation.warning_category}`",
        ])
        if deprecation.replacement:
            lines.append(f"- **Replacement:** {deprecation.replacement}")
        lines.extend([
            "",
            deprecation.message,
            "",
        ])
        if deprecation.note:
            lines.extend([deprecation.note, ""])
    return "\n".join(lines).rstrip() + "\n"


def render_deprecations(format_: DeprecationFormat) -> str:
    """Render deprecations in the requested format."""
    if format_ == "json":
        return render_deprecations_json() + "\n"
    if format_ == "markdown":
        return render_deprecations_markdown()
    return render_deprecations_table()


def render_release_note_deprecations(version: str) -> str:
    """Render release-note text for deprecations that start or end in a version."""
    warning_started: list[Deprecation] = []
    scheduled_changes: list[Deprecation] = []
    removals: list[Deprecation] = []
    for item in iter_deprecations():
        if item.warning_since == version:
            match item.status:
                case "active":
                    warning_started.append(item)
                case "scheduled":  # pragma: no branch - DeprecationStatus is exhaustive
                    scheduled_changes.append(item)
        if item.removal_version == version:
            removals.append(item)

    lines: list[str] = []
    if warning_started:
        lines.extend(["## Deprecations", ""])
        lines.extend(
            f"- `{item.target}` now emits `{item.warning_category}`. Planned removal: {_format_removal_version(item)}. "
            f"{item.message}"
            for item in warning_started
        )
        lines.append("")

    if scheduled_changes:
        lines.extend(["## Scheduled Breaking Changes", ""])
        lines.extend(
            f"- `{item.target}` is registered without a runtime warning. "
            f"Planned removal: {_format_removal_version(item)}. {item.message}"
            for item in scheduled_changes
        )
        lines.append("")

    if removals:
        lines.extend(["## Removed Deprecated Features", ""])
        lines.extend(f"- `{item.target}` was scheduled for removal in {version}. {item.message}" for item in removals)
        lines.append("")

    return "\n".join(lines)
