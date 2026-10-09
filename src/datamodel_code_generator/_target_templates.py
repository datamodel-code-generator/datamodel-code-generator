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
    from typing_extensions import Self

    from datamodel_code_generator.config import GenerateConfig

Role: TypeAlias = Callable[..., Callable[..., str]]


class TemplateOverlay:
    """A target's subdirectory of a custom template directory, whose files replace the builtin roles of that name.

    The directory loads before the builtin templates through the Jinja environment of the model templates, so an
    override may include or import any other builtin template. A missing directory or role falls back to the builtin
    role, as model templates do, and overrides receive the template data, as custom model templates do. A target names
    its builtin directory, its roles, its subdirectory, and the code of a template that does not render.
    A `Role` returns the renderer of a builtin role from its name and its compiled renderer.
    """

    BUILTIN: ClassVar[Path]
    ROLES: ClassVar[frozenset[str]]
    SUBDIR: ClassVar[str]
    INVALID: ClassVar[str]
    OPTION: ClassVar[str] = "model_config.custom_template_dir"

    def __init__(
        self,
        custom_template_dir: Path,
        data: Mapping[str, object] = MappingProxyType({}),
    ) -> None:
        """Find the roles the directory overrides."""
        self.directory = custom_template_dir / self.SUBDIR
        self.data = data
        self.roles = frozenset(role for role in self.ROLES if (self.directory / role).is_file())

    @classmethod
    def custom(cls, model_config: GenerateConfig, cwd: Path) -> Self | None:
        """Return the overrides of the model configuration's custom template directory, if it sets one.

        A relative directory resolves against the caller's working directory, as for the models, and overrides receive
        the `#all#` extra template data, as custom model templates do.
        """
        from datamodel_code_generator import _absolute_generation_path  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]

        if (directory := _absolute_generation_path(model_config.custom_template_dir, cwd)) is None:
            return None
        from datamodel_code_generator.model.base import ALL_MODEL  # noqa: PLC0415

        return cls(directory, (model_config.extra_template_data or {}).get(ALL_MODEL, {}))

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
            message = f"{PurePosixPath(self.SUBDIR, template)}: template {error.name!r} not found"
            raise APIGenerationError((self.problem(message),)) from None
        except TemplateError as error:
            name = PurePosixPath(self.SUBDIR, getattr(error, "name", None) or template)
            line = getattr(error, "lineno", None)
            where = name if line is None else f"{name} line {line}"
            raise APIGenerationError((self.problem(f"{where}: {error}"),)) from None

    def role(self, name: str, compiled: Callable[..., str]) -> Callable[..., str]:
        """Return the renderer of one builtin role: this directory's override, or the builtin.

        An override receives the template data, then the role's values, which take precedence.
        """
        if name not in self.roles:
            return compiled

        def render(**values: object) -> str:
            return self.render(name, {**self.data, **values})

        return render

    def problem(self, message: str) -> Diagnostic:
        """Return the diagnostic of a template of this target that does not render."""
        return Diagnostic(
            code=self.INVALID,
            severity="error",
            stage="target",
            message=message,
            option_path=self.OPTION,
        )


def builtin_role(_name: str, compiled: Callable[..., str]) -> Callable[..., str]:
    """Return the compiled builtin renderer of a role, for a target without a template directory."""
    return compiled
