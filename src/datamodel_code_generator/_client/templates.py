"""Custom client templates: the `client` directory of the custom template directory overrides the builtin roles."""

from __future__ import annotations

from pathlib import Path

from datamodel_code_generator._target_templates import TemplateOverlay


class ClientTemplates(TemplateOverlay):
    """The client roles a custom template directory overrides; a template that does not render names its file."""

    BUILTIN = Path(__file__).parent / "templates"
    ROLES = frozenset({
        "arguments.jinja2",
        "client.jinja2",
        "facade.jinja2",
        "operations.jinja2",
        "readme.jinja2",
        "resource.jinja2",
        "runtime.jinja2",
        "security.jinja2",
        "types.jinja2",
    })
    SUBDIR = "client"
    INVALID = "E_TEMPLATE_INVALID"
