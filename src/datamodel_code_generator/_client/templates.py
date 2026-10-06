"""Custom client templates: a directory whose files override the builtin roles of the same name."""

from __future__ import annotations

from pathlib import Path

from datamodel_code_generator._target_templates import TemplateOverlay


class ClientTemplates(TemplateOverlay):
    """A client template directory: the builtin roles it overrides.

    A missing directory is a configuration error, and a template that does not render is reported with its file.
    """

    BUILTIN = Path(__file__).parent / "templates"
    ROLES = frozenset({"client.jinja2", "resource.jinja2", "types.jinja2", "readme.jinja2"})
    MISSING = ("E_CONFIG_VALUE", "config")
    INVALID = "E_TEMPLATE_INVALID"
