"""Shared project configuration discovery and Python API loading."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from datamodel_code_generator import Error
from datamodel_code_generator.util import load_toml

if TYPE_CHECKING:
    from datamodel_code_generator.config import GenerateConfig


def _resolve_profile_extends(
    profiles: Mapping[str, Any],
    profile_name: str,
    visited: set[str] | None = None,
) -> dict[str, Any]:
    """Resolve profile inheritance via extends key."""
    if visited is None:
        visited = set()

    if profile_name in visited:
        chain = " -> ".join(visited) + f" -> {profile_name}"
        msg = f"Circular extends detected: {chain}"
        raise Error(msg)

    if profile_name not in profiles:
        available = list(profiles.keys()) if profiles else "none"
        msg = f"Extended profile '{profile_name}' not found in pyproject.toml. Available profiles: {available}"
        raise Error(msg)

    visited.add(profile_name)
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
        parent_config = _resolve_profile_extends(profiles, parent, visited.copy())
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
            if profile not in profiles:
                available = list(profiles.keys()) if profiles else "none"
                msg = f"Profile '{profile}' not found in pyproject.toml. Available profiles: {available}"
                raise Error(msg)
            resolved_profile = _resolve_profile_extends(profiles, profile)
            base_config.update(resolved_profile)

        return _normalize_pyproject_config(base_config), pyproject_path

    if profile:
        msg = f"Profile '{profile}' requested but no [tool.datamodel-codegen] section found in pyproject.toml"
        raise Error(msg)

    return {}, None


def _normalize_pyproject_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Convert TOML option spelling to Config field spelling."""
    normalized = {key.replace("-", "_"): value for key, value in config.items()}
    if "capitalize_enum_members" in normalized and "capitalise_enum_members" not in normalized:  # pragma: no cover
        normalized["capitalise_enum_members"] = normalized.pop("capitalize_enum_members")
    return normalized


_PYPROJECT_RELATIVE_PATH_FIELDS = frozenset({
    "custom_file_header_path",
    "custom_template_dir",
    "diff_against",
    "emit_model_metadata",
    "http_local_ref_path",
    "input",
    "lockfile",
    "output",
})

_PYPROJECT_JSON_CONFIG_FIELDS = frozenset({
    "aliases",
    "base_class_map",
    "custom_formatters_kwargs",
    "default_values",
    "duplicate_name_suffix",
    "enum_field_as_literal_map",
    "extra_template_data",
    "import_overrides",
    "model_name_map",
    "serialization_aliases",
    "type_overrides",
    "validators",
})

_PYPROJECT_PATH_FIELDS = _PYPROJECT_RELATIVE_PATH_FIELDS | _PYPROJECT_JSON_CONFIG_FIELDS


def _resolve_pyproject_relative_paths(config: dict[str, Any], pyproject_path: Path | None) -> dict[str, Any]:
    """Resolve only pyproject-relative paths from the pyproject directory."""
    if pyproject_path is None:
        return config

    config_directory = pyproject_path.parent
    updates = {
        field_name: config_directory / resolved_path
        for field_name in _PYPROJECT_PATH_FIELDS
        if isinstance(path := config.get(field_name), str | Path)
        and not (field_name in _PYPROJECT_JSON_CONFIG_FIELDS and str(path).lstrip().startswith(("{", "[")))
        and not (resolved_path := Path(path).expanduser()).is_absolute()
    }
    if not updates:
        return config
    return {**config, **updates}


_GENERATE_FIELD_NAMES = {
    "use_default": "apply_default_values_for_required_fields",
    "force_optional": "force_optional_for_required_fields",
    "default_values": "default_value_overrides",
}


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
    source = Path.cwd() if path is None else Path(path).expanduser().resolve()
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
        if (field := _GENERATE_FIELD_NAMES.get(key, key)) in fields
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

    options = _resolve_pyproject_relative_paths(options, pyproject_path)
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
        if (name := _GENERATE_FIELD_NAMES.get(field, field)) in fields
    }
    values.update(explicit)
    return GenerateConfig.model_validate(values)
