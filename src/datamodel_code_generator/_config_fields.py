"""Which config fields hold paths and JSON, and what each stage of a run does with them, free of pydantic.

A path field is listed once with the stages that read it: the pyproject.toml join (a relative path of a value read from
pyproject.toml resolves against its directory), the command line (``Config`` expands ``~`` and resolves it),
``generate()`` (a relative path resolves against the caller's working directory) and ``--watch`` (the file, directory
or output it watches). A JSON field holds an inline JSON value or names a JSON file; a target's JSON fields are those
with the prefix of the target's settings.
"""

from __future__ import annotations

from typing import Final, Literal, TypeAlias

from datamodel_code_generator._json_config_fields import JSON_CONFIG_FIELDS
from datamodel_code_generator._records import record

WatchedAs: TypeAlias = Literal["input", "file", "directory", "output", "file-output", "directory-output"]
"""What ``--watch`` watches a path field as: the input as the file or directory it is, a file, a directory, the model
output as what it names, an output that is always one file (the model metadata, also without a suffix), or an output
that is always a directory (a target package)."""


@record
class PathField:
    """A config field that holds a path, with the stages of a run that read it."""

    name: str
    cli_resolved: bool
    """The command line expands ``~`` and resolves the path when it validates the config."""
    caller_relative: bool
    """``generate()`` makes a relative path absolute against the caller's working directory."""
    watched_as: WatchedAs | None
    """What ``--watch`` watches the path as, or None when it watches nothing for the field."""


PATH_FIELDS: Final[tuple[PathField, ...]] = (
    PathField("input", cli_resolved=True, caller_relative=False, watched_as="input"),
    PathField("output", cli_resolved=True, caller_relative=True, watched_as="output"),
    PathField("emit_model_metadata", cli_resolved=False, caller_relative=True, watched_as="file-output"),
    PathField("server_output", cli_resolved=True, caller_relative=False, watched_as="directory-output"),
    PathField("client_output", cli_resolved=True, caller_relative=False, watched_as="directory-output"),
    PathField("custom_file_header_path", cli_resolved=True, caller_relative=True, watched_as="file"),
    PathField("custom_template_dir", cli_resolved=True, caller_relative=True, watched_as="directory"),
    PathField("http_local_ref_path", cli_resolved=True, caller_relative=False, watched_as="directory"),
    PathField("diff_against", cli_resolved=True, caller_relative=False, watched_as=None),
    PathField("lockfile", cli_resolved=False, caller_relative=False, watched_as=None),
)
"""Every path field, each read relative to the pyproject.toml directory when it comes from that file. ``generate()``
resolves ``http_local_ref_path`` against the directory of the output instead of the caller's. The order is the order
in which ``--watch`` adds the outputs and the files."""

CLI_RESOLVED_PATH_FIELDS: Final = tuple(field.name for field in PATH_FIELDS if field.cli_resolved)
CALLER_RELATIVE_PATH_FIELDS: Final = tuple(field.name for field in PATH_FIELDS if field.caller_relative)
WATCHED_PATH_FIELDS: Final = tuple(
    field.name for field in PATH_FIELDS if field.watched_as in {"input", "file", "directory"}
)
WATCHED_FILE_FIELDS: Final = (
    *(field.name for field in PATH_FIELDS if field.watched_as == "file"),
    *JSON_CONFIG_FIELDS,
)
"""The fields whose files ``--watch`` watches: the path fields of a file, then every JSON field, which may name one."""
WATCHED_DIRECTORY_FIELDS: Final = tuple(field.name for field in PATH_FIELDS if field.watched_as == "directory")
_OUTPUT_IS_DIRECTORY: Final[dict[WatchedAs | None, bool | None]] = {
    "output": None,
    "file-output": False,
    "directory-output": True,
}
WATCHED_OUTPUT_FIELDS: Final = tuple(
    (field.name, _OUTPUT_IS_DIRECTORY[field.watched_as])
    for field in PATH_FIELDS
    if field.watched_as in _OUTPUT_IS_DIRECTORY
)
"""The output fields with whether each is a directory (None: judged by what the path names)."""

TARGET_SELECTORS: Final[tuple[tuple[str, str], ...]] = (("generate_server", "server_"), ("generate_client", "client_"))
"""The field that selects each target and the prefix of the fields that configure it."""

JSON_FIELDS: Final[frozenset[str]] = frozenset(JSON_CONFIG_FIELDS)
TARGET_JSON_FIELDS: Final[frozenset[str]] = frozenset(
    name for name in JSON_CONFIG_FIELDS if name.startswith(tuple(prefix for _, prefix in TARGET_SELECTORS))
)
"""The JSON fields of a target: a relative file they name resolves like a path field, not against the working
directory as the JSON files of model options do on the command line."""
MODEL_JSON_FIELDS: Final = JSON_FIELDS - TARGET_JSON_FIELDS
PYPROJECT_PATH_FIELDS: Final = frozenset({field.name for field in PATH_FIELDS} | TARGET_JSON_FIELDS)
"""The fields the command line reads relative to the pyproject.toml directory when they come from that file."""
PYPROJECT_API_PATH_FIELDS: Final = PYPROJECT_PATH_FIELDS | MODEL_JSON_FIELDS
"""The fields ``generate()`` reads relative to the pyproject.toml directory: every path field and JSON field."""

__all__ = [
    "CALLER_RELATIVE_PATH_FIELDS",
    "CLI_RESOLVED_PATH_FIELDS",
    "JSON_FIELDS",
    "MODEL_JSON_FIELDS",
    "PATH_FIELDS",
    "PYPROJECT_API_PATH_FIELDS",
    "PYPROJECT_PATH_FIELDS",
    "TARGET_JSON_FIELDS",
    "TARGET_SELECTORS",
    "WATCHED_DIRECTORY_FIELDS",
    "WATCHED_FILE_FIELDS",
    "WATCHED_OUTPUT_FIELDS",
    "WATCHED_PATH_FIELDS",
    "PathField",
    "WatchedAs",
]
