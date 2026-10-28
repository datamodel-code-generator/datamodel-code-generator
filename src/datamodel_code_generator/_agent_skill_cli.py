"""Lazy CLI support for bundled Agent Skill installation."""

from __future__ import annotations

import sys


def install_agent_skill_command(agent: str, scope: str, *, overwrite: bool) -> int:
    """Install an Agent Skill without loading generation dependencies."""
    from typing import cast  # noqa: PLC0415

    from datamodel_code_generator.agent_skill import (  # noqa: PLC0415
        AgentName,
        AgentSkillError,
        SkillScope,
        install_agent_skill,
    )

    try:
        destination = install_agent_skill(
            cast("AgentName", agent),
            cast("SkillScope", scope),
            overwrite=overwrite,
        )
    except AgentSkillError as e:
        print(f"Error: {e}", file=sys.stderr)  # noqa: T201
        return 2
    print(f"Installed datamodel-code-generator skill at {destination}")  # noqa: T201
    return 0


def run_agent_skill_installer(args: list[str]) -> int:
    """Parse only the normal CLI arguments needed for the installer fast path."""
    from datamodel_code_generator.arguments import arg_parser, namespace  # noqa: PLC0415

    vars(namespace).clear()
    namespace.no_color = False
    arg_parser.parse_args(args, namespace=namespace)
    return install_agent_skill_command(
        namespace.install_skill,
        namespace.skill_scope or "project",
        overwrite=namespace.overwrite_skill,
    )
