"""Write CLI arguments as a `pyproject.toml` table (`--generate-pyproject-config`) and a table as a command line.

With `--write-pyproject-config`, the table is merged into the project's pyproject.toml.
"""

from __future__ import annotations

import codecs
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, cast

import tomllib

from datamodel_code_generator._config_fields import PYPROJECT_PATH_FIELDS
from datamodel_code_generator._message_text import wrapped_lines
from datamodel_code_generator._option_names import (
    CONFIG_TABLE,
    SENSITIVE_FIELDS,
    compact_json,
    field_name,
    hyphenated,
    mapping_argument,
    option_flag,
    toml_table,
    toml_text,
)
from datamodel_code_generator._option_table import COMMAND_DESTS
from datamodel_code_generator._paths import relative_spelling, working_directory
from datamodel_code_generator._url_redaction import redact_url

if TYPE_CHECKING:
    import tempfile
    from argparse import Namespace
    from collections.abc import Sequence

    from datamodel_code_generator._option_names import TomlValue
    from datamodel_code_generator._publication import PublicationAnchor

_MAIN_MODULE = "datamodel_code_generator.__main__"


EXCLUDED_CONFIG_OPTIONS: frozenset[str] = COMMAND_DESTS | {
    "check",
    "diff_against",
    "debug",
    "disable_warnings",
    "list_deprecations",
    "list_experimental",
    "watch",
    "watch_delay",
}
"""The options ``--generate-pyproject-config`` leaves out: those of a command and the config fields of a run mode."""


def _pyproject_toml_value(key: str, value: object) -> TomlValue:
    from datamodel_code_generator.json_config import JsonConfigSpecs  # noqa: PLC0415

    if key not in JsonConfigSpecs.by_field_name or not isinstance(value, Mapping):
        return cast("TomlValue", value)
    return compact_json(value)


def _cli_config_values(args: Namespace) -> dict[str, TomlValue]:
    """Return the configuration values of CLI arguments, keyed by field name."""
    return {
        key: _pyproject_toml_value(key, value)
        for key, value in sorted(vars(args).items())
        if value is not None and key not in EXCLUDED_CONFIG_OPTIONS
    }


def _pyproject_config_data(args: Namespace) -> dict[str, TomlValue]:
    """Return pyproject.toml configuration data from CLI arguments."""
    return hyphenated(_cli_config_values(args))


def _format_pyproject_config(config_data: Mapping[str, TomlValue], table: tuple[str, ...] = CONFIG_TABLE) -> str:
    lines: list[str] = [toml_table(table)]
    for key, value in config_data.items():
        lines.append(f"{key} = {toml_text(value)}")
    return "\n".join(lines) + "\n"


def generate_pyproject_config(args: Namespace) -> str:
    """Generate pyproject.toml [tool.datamodel-codegen] section from CLI arguments."""
    return _format_pyproject_config(_pyproject_config_data(args))


def _format_cli_value(value: str | list[str]) -> str:
    """Format a value for CLI argument."""
    if isinstance(value, list):
        return " ".join(f'"{v}"' if " " in v else v for v in value)
    return f'"{value}"' if " " in value else value


def generate_cli_command(config: dict[str, TomlValue]) -> str:
    """Generate CLI command from pyproject.toml configuration."""
    from datamodel_code_generator.cli_options import _option_arguments  # noqa: PLC0415

    parts: list[str] = ["datamodel-codegen"]

    for key, value in sorted(config.items()):
        if key in EXCLUDED_CONFIG_OPTIONS:
            continue
        if isinstance(value, Mapping):
            if argument := mapping_argument(key, value):
                parts.extend((option_flag(key), argument))
            continue
        match _option_arguments(key, value):
            case [option, *items] if items and isinstance(value, list | tuple):
                parts.extend((option, _format_cli_value(items)))
            case [option, *items]:
                parts.extend((option, *(_format_cli_value(str(item)) for item in items)))

    return " ".join(parts)


def _shareable_values(values: Mapping[str, TomlValue]) -> tuple[dict[str, TomlValue], list[str]]:
    """Return config values to print or write, leaving out credentials and redacting URLs, and the notes saying so."""
    notes: list[str] = []
    shared: dict[str, TomlValue] = {}
    for name, value in values.items():
        if name in SENSITIVE_FIELDS:
            notes.append(f"{option_flag(name)} is not repeated here; add it to the command again.")
            continue
        if name == "url":
            notes.append("--url is repeated without its user information, query, and fragment; add them again.")
            value = redact_url(str(value))  # noqa: PLW2901
        shared[name] = value
    return shared, notes


_FORMS = "an inline table or dotted keys"


class _UnprovenEditError(ValueError):
    """An edit of a pyproject.toml table that ``tomllib`` does not read back as intended."""


def _parses(text: str) -> bool:
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return False
    return True


def _header(line: str) -> tuple[str, ...] | None:
    """Return the keys of a table header line, ``()`` for an array of tables, or None for any other line."""
    if not line.lstrip().startswith("[") or not _parses(line):
        return None
    node: object = tomllib.loads(line)
    keys: list[str] = []
    while isinstance(node, dict) and len(node) == 1:
        ((key, node),) = node.items()
        keys.append(key)
    return () if isinstance(node, list) else tuple(keys)


def _key_lines(lines: Sequence[str], start: int, end: int) -> dict[str, tuple[int, int]]:
    """Return the lines of each key of a table section by field name: a value extends until ``tomllib`` reads it."""
    found: dict[str, tuple[int, int]] = {}
    index = start
    while index < end:
        if not (text := lines[index].strip()) or text.startswith("#"):
            index += 1
            continue
        stop = next((stop for stop in range(index + 1, end + 1) if _parses("".join(lines[index:stop]))), None)
        if stop is None:
            raise _UnprovenEditError
        found[field_name(next(iter(tomllib.loads("".join(lines[index:stop])))))] = (index, stop)
        index = stop
    return found


def _same_value(left: object, right: object) -> bool:
    """Return whether two parsed TOML values are equal and of the same type: ``1`` is not ``True``."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same_value(value, right[key]) for key, value in left.items())
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(map(_same_value, left, right))
    return left == right


def _merged_table_text(text: str, table: tuple[str, ...], values: Mapping[str, TomlValue]) -> tuple[str, list[str]]:
    """Return a pyproject.toml text with ``values`` set in ``table``, and the keys that are new or change.

    Only those keys are written: an old line of the key (in either spelling) is replaced, a new key follows the last key
    of the table, and a table the file lacks is appended. The edit is kept only when ``tomllib`` reads it as the old
    document with those keys set; otherwise, as for an inline table or dotted keys, ``_UnprovenEditError`` is raised.
    """
    expected = tomllib.loads(text)
    wanted = tomllib.loads("".join(f"{key} = {toml_text(value)}\n" for key, value in values.items()))
    node = expected
    for key in table:
        node = node.setdefault(key, {})
    spelled = {field_name(key): key for key in node}
    if not (
        changed := [
            key for key, value in wanted.items() if not _same_value(node.get(spelled.get(field_name(key))), value)
        ]
    ):
        return text, []
    for key in changed:
        node.pop(spelled.get(field_name(key), key), None)
        node[key] = wanted[key]
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += newline
    new = {field_name(key): f"{key} = {toml_text(values[key])}{newline}" for key in changed}
    if (start := next((index for index, line in enumerate(lines) if _header(line) == table), None)) is None:
        lines.extend([*([newline] if lines else []), f"{toml_table(table)}{newline}", *new.values()])
    else:
        end = next((index for index in range(start + 1, len(lines)) if _header(lines[index]) is not None), len(lines))
        found = _key_lines(lines, start + 1, end)
        last = max((stop for _, stop in found.values()), default=start + 1)
        lines[last:last] = [line for name, line in new.items() if name not in found]
        for name, (begin, stop) in sorted(found.items(), key=lambda item: -item[1][0]):
            if name in new:
                lines[begin:stop] = [new[name]]
    edited = "".join(lines)
    if not _parses(edited) or not _same_value(tomllib.loads(edited), expected):
        raise _UnprovenEditError
    return edited, changed


class _WrittenConfig(NamedTuple):
    """What ``--write-pyproject-config`` did: the file and table, whether it created the file, the keys it wrote.

    ``refused`` says why nothing was written.
    """

    path: str
    table: tuple[str, ...]
    created: bool
    keys: list[str]
    refused: str | None = None

    def payload(self) -> dict[str, object]:
        """Return the ``written`` value of a JSON payload."""
        return {**self._asdict(), "table": list(self.table)}


def _write_pyproject_table(
    path: Path, table: tuple[str, ...], values: Mapping[str, TomlValue] | None
) -> _WrittenConfig:
    """Set hyphenated ``values`` in ``table`` of a pyproject.toml, creating the file with the table alone if missing.

    The file keeps its byte order mark and line endings, is read again just before it is replaced, and is replaced
    atomically. Nothing is written when there are no values, when relative paths of the command would name other
    files from the directory of a pyproject.toml outside the working directory, or when the edit is not proven.
    """
    shown = relative_spelling(path)
    original = path.read_bytes() if path.exists() else None
    written = partial(_WrittenConfig, shown, table, created=False)
    if values is None:
        return written(keys=[], refused="the recommendation has no pyproject.toml block to write")
    if path.parent != working_directory() and PYPROJECT_PATH_FIELDS.intersection(map(field_name, values)):
        return written(
            keys=[], refused=f"the paths of the command are relative to the working directory, not to {shown}"
        )
    data = original or b""
    bom = codecs.BOM_UTF8 if data.startswith(codecs.BOM_UTF8) else b""
    try:
        edited, keys = _merged_table_text(data[len(bom) :].decode(), table, values)
    except _UnprovenEditError:
        return written(
            keys=[], refused=f"{toml_table(table)} is written in a form this command does not edit: {_FORMS}"
        )
    except ValueError as error:
        return written(keys=[], refused=f"it is not valid TOML: {error}")
    if keys:
        if (path.read_bytes() if path.exists() else None) != original:
            return written(keys=[], refused="it changed while the command ran")
        from datamodel_code_generator._publication import (  # noqa: PLC0415
            publish_staged_files,
            release_all,
            stage_artifact,
        )

        contexts: list[tempfile.TemporaryDirectory[str]] = []
        anchors: list[PublicationAnchor] = []
        try:
            staged, _ = stage_artifact(path, path.name, path.parent, contexts, anchors)
            staged.write_bytes(bom + edited.encode())
            publish_staged_files([(staged, path)])
        finally:
            release_all((*anchors, *contexts))
    return _WrittenConfig(shown, table, original is None and bool(keys), keys)


def _written_text(written: _WrittenConfig, values: Mapping[str, TomlValue], notes: Sequence[str], command: str) -> str:
    """Return the report of a written table, without a final newline: the keys, the notes, and the next command."""
    if written.keys:
        head = f"{'Created' if written.created else 'Updated'} {written.path}:"
        body = _format_pyproject_config({key: values[key] for key in written.keys}, written.table).splitlines()
    elif not values and not Path(written.path).exists():
        head, body = f"There are no values to write to {written.path}; nothing was written.", []
    else:
        head, body = f"{written.path} already holds these values; nothing was written:", [toml_table(written.table)]
    lines = [head, *(f"  {line}" for line in body)]
    if notes:
        lines.extend(["", "Notes", *(line for note in notes for line in wrapped_lines(note, "  - ", "    "))])
    lines.extend(["", "Command", f"  {command}"])
    return "\n".join(lines)


generate_pyproject_config.__module__ = _MAIN_MODULE
generate_cli_command.__module__ = _MAIN_MODULE
