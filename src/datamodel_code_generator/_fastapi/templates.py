"""Custom FastAPI templates: the `fastapi` directory of the custom template directory overrides the builtin roles."""

from __future__ import annotations

from pathlib import Path

from datamodel_code_generator._target_templates import TemplateOverlay


class FastAPITemplates(TemplateOverlay):
    """The server roles a custom template directory overrides; a template that does not render names its file."""

    BUILTIN = Path(__file__).parent / "templates"
    SUBDIR = "fastapi"
    INVALID = "F_TEMPLATE_INVALID"
