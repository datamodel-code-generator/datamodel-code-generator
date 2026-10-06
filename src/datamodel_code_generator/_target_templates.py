"""Template directories of generation targets: overlays of the builtin roles, rendered like custom model templates."""

from __future__ import annotations

from collections.abc import Callable
from functools import cached_property
from typing import TYPE_CHECKING, ClassVar, TypeAlias

from datamodel_code_generator._api_types import APIGenerationError, Diagnostic

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from jinja2 import Environment

    from datamodel_code_generator._api_types import DiagnosticStage

Role: TypeAlias = Callable[..., Callable[..., str]]


class TemplateOverlay:
    """A template directory whose files replace the builtin roles of the same name.

    The directory loads before the builtin templates through the Jinja environment of the model templates, so an
    override may include or import any other builtin template. A target names its builtin directory, its roles, and
    the codes of a missing directory and of a template that does not render. A `Role` returns the renderer of a builtin
    role from its name, its compiled renderer, and the values only overrides receive.
    """

    BUILTIN: ClassVar[Path]
    ROLES: ClassVar[frozenset[str]]
    MISSING: ClassVar[tuple[str, DiagnosticStage]]
    INVALID: ClassVar[str]

    def __init__(self, directory: Path, target_id: str) -> None:
        """Find the overridden roles, rejecting a missing directory."""
        self.directory = directory
        self.target_id = target_id
        if not directory.is_dir():
            code, stage = self.MISSING
            raise APIGenerationError((self.diagnostic(code, "The template directory does not exist", stage),))
        self.roles = frozenset(role for role in self.ROLES if (directory / role).is_file())

    @cached_property
    def environment(self) -> Environment:
        """Return the Jinja environment of the model templates, loading this directory before the builtin roles."""
        from jinja2 import ChoiceLoader, FileSystemLoader  # noqa: PLC0415

        from datamodel_code_generator.model.base import (  # noqa: PLC0415
            _build_environment,  # pyright: ignore[reportPrivateUsage]
        )

        loader = ChoiceLoader([FileSystemLoader(str(self.directory)), FileSystemLoader(str(self.BUILTIN))])
        return _build_environment(loader, auto_reload=False)

    def render(self, template: str, values: Mapping[str, object]) -> str:
        """Render one template of the directory, or a builtin role it extends or includes.

        A template that does not parse or render is reported with its file name.
        """
        from jinja2 import TemplateError  # noqa: PLC0415

        try:
            return self.environment.get_template(template).render(**values)
        except TemplateError as error:
            name = getattr(error, "name", None) or template
            line = getattr(error, "lineno", None)
            where = name if line is None else f"{name} line {line}"
            raise APIGenerationError((self.problem(f"{where}: {error}"),)) from None

    def role(self, name: str, compiled: Callable[..., str], **frame: object) -> Callable[..., str]:
        """Return the renderer of one builtin role: this directory's override, given the frame too, or the builtin."""
        if name not in self.roles:
            return compiled

        def render(**values: object) -> str:
            return self.render(name, {**values, **frame})

        return render

    def problem(self, message: str) -> Diagnostic:
        """Return a template diagnostic of this target."""
        return self.diagnostic(self.INVALID, message)

    def diagnostic(self, code: str, message: str, stage: DiagnosticStage = "target") -> Diagnostic:
        """Return a diagnostic of this target's templates."""
        return Diagnostic(
            code=code,
            severity="error",
            stage=stage,
            message=message,
            option_path="templates",
            target_id=self.target_id,
        )


def builtin_role(_name: str, compiled: Callable[..., str], **_frame: object) -> Callable[..., str]:
    """Return the compiled builtin renderer of a role, for a target without a template directory."""
    return compiled
