"""Template directories of generation targets: overlays of the builtin roles, rendered like custom model templates."""

from __future__ import annotations

from collections.abc import Callable
from functools import cached_property
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, TypeAlias

from datamodel_code_generator._api_types import APIGenerationError, Diagnostic

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from jinja2 import Environment

Role: TypeAlias = Callable[..., Callable[..., str]]


class TemplateOverlay:
    """The `subdir` of a custom template directory, whose files replace the builtin roles of the same name.

    The directory loads before the builtin templates through the Jinja environment of the model templates, so an
    override may include or import any other builtin template. A missing directory or role falls back to the builtin
    role, as model templates do, and overrides receive the template data, as custom model templates do. A target names
    its builtin directory, its roles, the code of a template that does not render, and the setting of the directory.
    A `Role` returns the renderer of a builtin role from its name, its compiled renderer, and the values only overrides
    receive.
    """

    BUILTIN: ClassVar[Path]
    ROLES: ClassVar[frozenset[str]]
    INVALID: ClassVar[str]
    OPTION: ClassVar[str]

    def __init__(
        self,
        custom_template_dir: Path,
        subdir: str,
        target_id: str,
        data: Mapping[str, object] = MappingProxyType({}),
    ) -> None:
        """Find the roles the directory overrides."""
        self.subdir = subdir
        self.directory = custom_template_dir / subdir
        self.target_id = target_id
        self.data = data
        self.roles = frozenset(role for role in self.ROLES if (self.directory / role).is_file())

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

        A template that does not parse or render is reported with its path in the custom template directory and, when
        Jinja knows it, its line.
        """
        from jinja2 import TemplateError, TemplateNotFound  # noqa: PLC0415

        try:
            return self.environment.get_template(template).render(**values)
        except TemplateNotFound as error:
            message = f"{PurePosixPath(self.subdir, template)}: template {error.name!r} not found"
            raise APIGenerationError((self.problem(message),)) from None
        except TemplateError as error:
            name = PurePosixPath(self.subdir, getattr(error, "name", None) or template)
            line = getattr(error, "lineno", None)
            where = name if line is None else f"{name} line {line}"
            raise APIGenerationError((self.problem(f"{where}: {error}"),)) from None

    def role(self, name: str, compiled: Callable[..., str], **frame: object) -> Callable[..., str]:
        """Return the renderer of one builtin role: this directory's override, or the builtin.

        An override receives the template data, then the role's values and the frame, which take precedence.
        """
        if name not in self.roles:
            return compiled

        def render(**values: object) -> str:
            return self.render(name, {**self.data, **values, **frame})

        return render

    def problem(self, message: str) -> Diagnostic:
        """Return a template diagnostic of this target."""
        return self.diagnostic(self.INVALID, message)

    def diagnostic(self, code: str, message: str) -> Diagnostic:
        """Return a diagnostic of this target's templates."""
        return Diagnostic(
            code=code,
            severity="error",
            stage="target",
            message=message,
            option_path=self.OPTION,
            target_id=self.target_id,
        )


def builtin_role(_name: str, compiled: Callable[..., str], **_frame: object) -> Callable[..., str]:
    """Return the compiled builtin renderer of a role, for a target without a template directory."""
    return compiled
