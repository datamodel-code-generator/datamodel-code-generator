"""Custom client templates: the `client` directory of the custom template directory overrides the builtin roles."""

from __future__ import annotations

from pathlib import Path

from datamodel_code_generator._target_templates import TemplateOverlay


class ClientTemplates(TemplateOverlay):
    """The client roles a custom template directory overrides; a template that does not render names its file."""

    BUILTIN = Path(__file__).parent / "templates"
    ROLES = frozenset({"client.jinja2", "resource.jinja2", "types.jinja2", "readme.jinja2"})
    SUBDIR = "client"
    INVALID = "E_TEMPLATE_INVALID"
