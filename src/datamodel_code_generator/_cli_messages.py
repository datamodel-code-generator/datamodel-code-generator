"""Messages of the command line that the target runner shares: option conflicts and the invalid class name hint."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from collections.abc import Iterable

    from datamodel_code_generator._shared_types import InvalidClassNameError


def _recommend_options_conflict(args: Namespace) -> str | None:
    """Return the first option that cannot be combined with --recommend-options."""
    conflicts = (
        (args.check, "--check"),
        (args.watch, "--watch"),
        (args.diff_against is not None and args.recommend_options == "quick", "--diff-against in quick mode"),
        (args.all_jobs, "--all-jobs"),
        (len(args.job or ()) > 1, "more than one --job"),
        (args.generate_pyproject_config, "--generate-pyproject-config"),
        (args.generate_cli_command, "--generate-cli-command"),
        (args.install_skill is not None, "--install-skill"),
        (args.list_deprecations is not None, "--list-deprecations"),
        (args.list_experimental is not None, "--list-experimental"),
    )
    return _first_conflict(conflicts)


def _invalid_class_name_message(error: InvalidClassNameError, args: Namespace, class_name: str | None) -> str:
    """Return the CLI message of an invalid class name with a hint at its fix.

    A name that --class-name sets is fixed there, as the recommender keeps it; a name from the schema gets the
    --recommend-options hint where the command takes that option.
    """
    message = f"{error} You have to set `--class-name` option"
    if error.class_name == class_name:
        return f"{message}\nHint: --class-name sets this name; change it to a valid Python class name or remove it."
    if _recommend_options_conflict(args) is not None:
        return message
    return (
        f"{message}\nHint: add --recommend-options to this command to get a class name and naming options for this "
        "schema (experimental)."
    )


def _first_conflict(conflicts: Iterable[tuple[object, str]]) -> str | None:
    """Return the message of the first conflict that applies."""
    return next((message for applies, message in conflicts if applies), None)
