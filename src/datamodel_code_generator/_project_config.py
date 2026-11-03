"""Shared project configuration discovery and Python API loading."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from datamodel_code_generator import Error
from datamodel_code_generator._config_fields import (
    PYPROJECT_API_PATH_FIELDS,
    PYPROJECT_PATH_FIELDS,
    TARGET_JSON_FIELDS,
    names_path,
    option_path,
)
from datamodel_code_generator._option_names import FIELD_ALIASES, GENERATE_FIELD_NAMES, field_name
from datamodel_code_generator._paths import resolved
from datamodel_code_generator.util import load_toml

if TYPE_CHECKING:
    from datamodel_code_generator.config import GenerateConfig


def _resolve_profile_extends(
    profiles: Mapping[str, Any],
    profile_name: str,
    visited: tuple[str, ...] = (),
    *,
    missing: str | None = None,
) -> dict[str, Any]:
    """Resolve profile inheritance via extends key; ``visited`` is the chain of profiles that led here, in order.

    ``missing`` names a missing requested profile.
    """
    if profile_name in visited:
        msg = f"Circular extends detected: {' -> '.join((*visited, profile_name))}"
        raise Error(msg)

    if profile_name not in profiles:
        available = list(profiles.keys()) if profiles else "none"
        missing = missing or f"Extended profile '{profile_name}' not found in pyproject.toml"
        msg = f"{missing}. Available profiles: {available}"
        raise Error(msg)

    visited = (*visited, profile_name)
    profile = profiles[profile_name]
    if not isinstance(profile, Mapping):
        msg = f"Profile '{profile_name}' must be a table"
        raise Error(msg)
    extends = profile.get("extends")

    if not extends:
        return dict(profile.items())

    if not isinstance(extends, str | list) or (
        isinstance(extends, list) and not all(isinstance(parent, str) for parent in extends)
    ):
        msg = f"Profile '{profile_name}' extends must be a string or list of strings"
        raise Error(msg)
    parents = [extends] if isinstance(extends, str) else extends
    result: dict[str, Any] = {}

    for parent in parents:
        if parent == profile_name:
            msg = f"Profile '{profile_name}' cannot extend itself"
            raise Error(msg)
        parent_config = _resolve_profile_extends(profiles, parent, visited)
        result.update(parent_config)

    result.update({k: v for k, v in profile.items() if k != "extends"})
    return result


def _find_datamodel_codegen_project_config_with_path(source: Path) -> tuple[Path, Mapping[str, Any]] | None:
    """Return the closest datamodel-codegen TOML table and its pyproject path."""
    current_path = source
    while current_path != current_path.parent:
        pyproject_path = current_path / "pyproject.toml"
        if pyproject_path.is_file():
            pyproject_toml = load_toml(pyproject_path)
            tool_config = pyproject_toml.get("tool", {}).get("datamodel-codegen")
            if isinstance(tool_config, Mapping):
                return pyproject_path, tool_config

        if (current_path / ".git").exists():  # pragma: no cover
            break
        current_path = current_path.parent
    return None


def _get_pyproject_toml_config_with_path(
    source: Path,
    profile: str | None = None,
) -> tuple[dict[str, Any], Path | None]:
    """Return resolved project config together with the project file that supplied it."""
    if (project_config := _find_datamodel_codegen_project_config_with_path(source)) is not None:
        pyproject_path, tool_config = project_config
        base_config: dict[str, Any] = {
            key: value for key, value in tool_config.items() if key not in {"jobs", "profiles"}
        }

        if profile:
            profiles = tool_config.get("profiles", {})
            if not isinstance(profiles, Mapping):
                msg = "[tool.datamodel-codegen.profiles] must be a table"
                raise Error(msg)
            base_config.update(
                _resolve_profile_extends(profiles, profile, missing=f"Profile '{profile}' not found in pyproject.toml")
            )

        return _normalize_pyproject_config(base_config), pyproject_path

    if profile:
        msg = f"Profile '{profile}' requested but no [tool.datamodel-codegen] section found in pyproject.toml"
        raise Error(msg)

    return {}, None


def _normalize_pyproject_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Convert TOML option spelling to Config field spelling."""
    normalized = {field_name(key): value for key, value in config.items()}
    for alias, field in FIELD_ALIASES.items():
        if alias in normalized and field not in normalized:  # pragma: no cover
            normalized[field] = normalized.pop(alias)
    return normalized


def _resolve_relative_paths_from(
    config: dict[str, Any], directory: Path, fields: frozenset[str] = PYPROJECT_PATH_FIELDS
) -> dict[str, Any]:
    """Resolve the relative paths of the given fields from a directory.

    Only lexical: ``~`` expands, and inline JSON of a JSON field is a value, not a path. A ``~user`` that names no user
    raises ``Error``.
    """
    updates = {
        field_name: directory / resolved_path
        for field_name in fields
        if (path := config.get(field_name)) is not None
        and names_path(field_name, path)
        and not (resolved_path := option_path(field_name, path)).is_absolute()
    }
    if not updates:
        return config
    return {**config, **updates}


def _resolve_pyproject_relative_paths(
    config: dict[str, Any], pyproject_path: Path | None, fields: frozenset[str] = PYPROJECT_PATH_FIELDS
) -> dict[str, Any]:
    """Resolve the relative paths of the given fields from the pyproject directory.

    By default, as on the command line, the JSON files of model options stay relative to the working directory.
    """
    if pyproject_path is None:
        return config
    return _resolve_relative_paths_from(config, pyproject_path.parent, fields)


def load_pyproject_config(
    path: Path | str | None = None,
    profile: str | None = None,
    *,
    overrides: GenerateConfig | Mapping[str, Any] | None = None,
) -> GenerateConfig:
    """Load project settings for generate(), with explicit overrides taking precedence.

    Search from ``path`` (a directory or project file), or the working directory,
    using the CLI's project lookup and profile inheritance. Generation paths in
    TOML are relative to its directory. CLI-only options such as ``input`` and
    ``check`` are ignored. Overrides use Python API field names and caller-relative
    paths; only explicitly set fields of a GenerateConfig override are merged.
    Without project settings, return the API defaults with any overrides applied.
    """
    source = Path.cwd() if path is None else resolved(Path(path))
    if path is not None and source.is_file():
        source = source.parent
    options, pyproject_path = _get_pyproject_toml_config_with_path(source, profile)

    from datamodel_code_generator.config import GenerateConfig, _rebuild_generate_config  # ruff: ignore[import-outside-top-level]

    _rebuild_generate_config()
    match overrides:
        case None:
            explicit: dict[str, Any] = {}
        case GenerateConfig():
            explicit = {field: getattr(overrides, field) for field in overrides.model_fields_set}
        case Mapping():
            explicit = dict(overrides)
        case _:
            msg = "overrides must be a GenerateConfig or a mapping of generate() options"
            raise TypeError(msg)

    fields = GenerateConfig.model_fields
    if pyproject_path is not None:
        explicit.setdefault("settings_path", pyproject_path.parent)
    options = {
        key: value
        for key, value in options.items()
        if (field := GENERATE_FIELD_NAMES.get(key, key)) in fields
        and field not in explicit
        and (field != "parent_scoped_naming" or "naming_strategy" not in explicit)
    }
    if not options:
        return GenerateConfig.model_validate(explicit)

    from datamodel_code_generator._cli_config import (  # ruff: ignore[import-outside-top-level]
        _apply_implicit_cli_config_values,
        _get_config_class,
        _RawConfigValue,
        _template_data,
    )

    options = _resolve_pyproject_relative_paths(options, pyproject_path, PYPROJECT_API_PATH_FIELDS)
    config = _get_config_class().model_validate(options)
    if "output_model_type" in explicit:
        from datamodel_code_generator.enums import DataModelType  # noqa: PLC0415

        config.output_model_type = DataModelType(explicit["output_model_type"])
    for field in ("use_annotated", "field_constraints"):
        if field in explicit:
            setattr(config, field, explicit[field])
    _apply_implicit_cli_config_values(
        config, cast("Mapping[str, _RawConfigValue]", options), cast("Mapping[str, _RawConfigValue]", explicit)
    )
    _template_data(config)
    values = {
        name: getattr(config, field)
        for field in config.model_fields_set
        if (name := GENERATE_FIELD_NAMES.get(field, field)) in fields
    }
    values.update(explicit)
    loaded = GenerateConfig.model_validate(values)
    from datamodel_code_generator.json_config import document_base  # noqa: PLC0415

    directory = cast("Path", pyproject_path).parent
    if bases := {
        field: document_base(value, directory, directory)
        for field, value in options.items()
        if field in TARGET_JSON_FIELDS
    }:
        loaded._target_document_bases = bases  # noqa: SLF001
    return loaded
