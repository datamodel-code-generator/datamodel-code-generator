"""Install the bundled datamodel-code-generator Agent Skill."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from shutil import copyfileobj, rmtree
from tempfile import mkdtemp
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from importlib.abc import Traversable

AgentName = Literal["codex", "claude-code"]
SkillScope = Literal["project", "user"]

SKILL_NAME = "datamodel-code-generator"


class AgentSkillError(Exception):
    """Raised when an Agent Skill cannot be installed safely."""


def _bundled_skill() -> Traversable:
    """Return the package resource, or the repository skill during source development."""
    if (skill := files("datamodel_code_generator.resources").joinpath(SKILL_NAME)).is_dir():  # pragma: no cover
        # The wheel E2E exercises this branch; editable/source test runs use the repository fallback.
        return skill
    return Path(__file__).parents[2] / "skills" / SKILL_NAME  # pragma: no cover


def _skill_destination(agent: AgentName, scope: SkillScope, cwd: Path) -> Path:
    """Return the skill location supported by the selected coding agent."""
    match agent:
        case "codex":
            skill_directory = Path(".agents") / "skills"
        case "claude-code":
            skill_directory = Path(".claude") / "skills"
        case _:
            msg = f"Unsupported coding agent: {agent}"
            raise AgentSkillError(msg)

    match scope:
        case "project":
            root = cwd
        case "user":
            root = Path.home()
        case _:
            msg = f"Unsupported skill scope: {scope}"
            raise AgentSkillError(msg)
    return root / skill_directory / SKILL_NAME


def _copy_skill(source: Traversable, destination: Path) -> None:
    """Copy a Traversable skill tree without requiring an extracted package resource."""
    destination.mkdir()
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            _copy_skill(item, target)
            continue
        with item.open("rb") as source_file, target.open("wb") as target_file:
            copyfileobj(source_file, target_file)


def install_agent_skill(
    agent: AgentName,
    scope: SkillScope,
    *,
    overwrite: bool,
    cwd: Path | None = None,
) -> Path:
    """Install the bundled skill, replacing an existing regular directory only on request."""
    destination = _skill_destination(agent, scope, Path.cwd() if cwd is None else cwd)
    destination_exists = destination.exists() or destination.is_symlink()
    if destination_exists and not overwrite:
        msg = f"Agent skill already exists at {destination}. Re-run with --overwrite-skill to replace it."
        raise AgentSkillError(msg)
    if destination_exists and (destination.is_symlink() or not destination.is_dir()):
        msg = f"Agent skill target is not a regular directory: {destination}"
        raise AgentSkillError(msg)

    staging_directory: Path | None = None
    backup_directory: Path | None = None
    backup_needs_recovery = False
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging_directory = Path(mkdtemp(prefix=f".{SKILL_NAME}-", dir=destination.parent))
        staged = staging_directory / SKILL_NAME
        _copy_skill(_bundled_skill(), staged)
        if not destination_exists:
            staged.replace(destination)
            return destination

        backup_directory = Path(mkdtemp(prefix=f".{SKILL_NAME}-backup-", dir=destination.parent))
        previous = backup_directory / SKILL_NAME
        destination.replace(previous)
        backup_needs_recovery = True
        try:
            staged.replace(destination)
        except OSError as replacement_error:
            try:
                previous.replace(destination)
            except OSError as rollback_error:
                msg = f"Could not replace the Agent Skill. Its previous version is preserved at {previous}."
                raise AgentSkillError(msg) from rollback_error
            backup_needs_recovery = False
            msg = f"Could not replace the Agent Skill at {destination}: {replacement_error}"
            raise AgentSkillError(msg) from replacement_error
        else:
            backup_needs_recovery = False
            return destination
    except AgentSkillError:
        raise
    except OSError as e:
        msg = f"Could not install the Agent Skill at {destination}: {e}"
        raise AgentSkillError(msg) from e
    finally:
        if staging_directory is not None:
            rmtree(staging_directory, ignore_errors=True)
        if backup_directory is not None and not backup_needs_recovery:
            rmtree(backup_directory, ignore_errors=True)
