"""Custom FastAPI templates: overlays of the builtin roles, and the extra files a template manifest declares."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final, Literal, Protocol, TypeAlias

from typing_extensions import TypeIs

from datamodel_code_generator._api_types import APIGenerationError, Diagnostic

if TYPE_CHECKING:
    from jinja2 import Environment


MANIFEST: Final = "fastapi-templates.toml"
BUILTIN: Final = Path(__file__).parent / "templates"
ROLES: Final = frozenset({
    "application.jinja2",
    "router.jinja2",
    "services.jinja2",
    "readme.jinja2",
})

Scope: TypeAlias = Literal["project", "router", "operation"]
Format: TypeAlias = Literal["python", "markdown", "json", "toml", "yaml", "html", "xml", "text"]

_SCOPES: Final[dict[object, Scope]] = {"project": "project", "router": "router", "operation": "operation"}
_FORMATS: Final[dict[object, Format]] = {
    "python": "python",
    "markdown": "markdown",
    "json": "json",
    "toml": "toml",
    "yaml": "yaml",
    "html": "html",
    "xml": "xml",
    "text": "text",
}
_EXTENSIONS: Final[dict[str, Format]] = {
    ".py": "python",
    ".pyi": "python",
    ".md": "markdown",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".html": "html",
    ".xml": "xml",
    ".txt": "text",
}
ROUTER: Final = "{router}"
OPERATION: Final = "{operation}"
_TOKENS: Final[dict[Scope, str | None]] = {"project": None, "router": ROUTER, "operation": OPERATION}
_KEYS: Final = frozenset({"template", "path", "scope", "format", "header"})


class _Renderable(Protocol):
    def render(self, **values: object) -> str:
        """Return the template rendered with the values."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtraFile:
    """One extra file a template manifest declares: its template, path, scope, format, and header."""

    template: str
    path: str
    scope: Scope
    format: Format
    header: bool


class TemplateSet:
    """A template directory: the builtin roles it overrides, and the extra files its manifest declares."""

    def __init__(self, directory: Path, target_id: str) -> None:
        """Find the overridden roles and read the manifest, rejecting a missing directory or an invalid manifest."""
        self.directory = directory
        self.target_id = target_id
        if not directory.is_dir():
            raise APIGenerationError((self.problem("The template directory does not exist"),))
        self.roles = frozenset(role for role in ROLES if (directory / role).is_file())
        self.extras = self.manifest()

    @cached_property
    def environment(self) -> Environment:
        """Return the Jinja environment of the model templates, loading this directory before the builtin roles."""
        from jinja2 import ChoiceLoader, FileSystemLoader  # noqa: PLC0415

        from datamodel_code_generator.model.base import (  # noqa: PLC0415
            _build_environment,  # pyright: ignore[reportPrivateUsage]
        )

        loader = ChoiceLoader([FileSystemLoader(str(self.directory)), FileSystemLoader(str(BUILTIN))])
        return _build_environment(loader, auto_reload=False)

    def render(self, template: str, values: Mapping[str, object]) -> str:
        """Render one template of the directory, or a builtin role it extends or includes."""
        return self.template(template).render(**values)

    def template(self, name: str) -> _Renderable:
        """Return one template of the directory, or the builtin role of that name."""
        return self.environment.get_template(name)

    def manifest(self) -> tuple[ExtraFile, ...]:
        """Return the extra files of the manifest in declaration order; the directory may have no manifest."""
        from datamodel_code_generator.util import load_toml  # noqa: PLC0415

        if not (path := self.directory / MANIFEST).is_file():
            return ()
        try:
            data = load_toml(path)
        except ValueError as error:
            raise APIGenerationError((self.problem(f"{MANIFEST} is not TOML: {error}"),)) from None
        problems: list[str] = []
        version = data.pop("schema_version", None)
        if type(version) is not int or version != 1:
            problems.append(f"{MANIFEST} needs schema_version = 1")
        files: object = data.pop("files", [])
        problems.extend(f"{MANIFEST} has no setting {key!r}" for key in data)
        extras: list[ExtraFile] = []
        for index, entry in enumerate(files if _is_list(files) else ()):
            if isinstance(extra := _extra(entry, self.directory), str):
                problems.append(f"files[{index}] {extra}")
            else:
                extras.append(extra)
        if not _is_list(files):
            problems.append(f"{MANIFEST} files must be an array of tables")
        if problems:
            raise APIGenerationError(tuple(self.problem(problem) for problem in problems))
        return tuple(extras)

    def problem(self, message: str) -> Diagnostic:
        """Return a template diagnostic of this target."""
        return self.diagnostic("F_TEMPLATE_INVALID", message)

    def conflict(self, message: str) -> Diagnostic:
        """Return a diagnostic of two generated files that take one path."""
        return self.diagnostic("F_NAME_CONFLICT", message)

    def diagnostic(self, code: str, message: str) -> Diagnostic:
        """Return a diagnostic of this target's templates."""
        return Diagnostic(
            code=code,
            severity="error",
            stage="target",
            message=message,
            option_path="templates",
            target_id=self.target_id,
        )


def _extra(entry: object, directory: Path) -> ExtraFile | str:  # noqa: PLR0911
    if not _is_mapping(entry):
        return "must be a table"
    template, path, scope = (entry.get(key) for key in ("template", "path", "scope"))
    if not (isinstance(template, str) and isinstance(path, str)):
        return "needs the string keys template, path, and scope"
    if unknown := sorted(str(key) for key in entry if key not in _KEYS):
        return f"has no key {unknown[0]!r}"
    if (chosen_scope := _SCOPES.get(scope)) is None:
        return "scope must be 'project', 'router', or 'operation'"
    if not _relative(template) or not (directory / template).is_file():
        return f"template {template!r} is not a file inside the template directory"
    if not _relative(path):
        return f"path {path!r} must be a relative path inside the package"
    token = _TOKENS[chosen_scope]
    if path.replace(token or "", "").count("{") or path.replace(token or "", "").count("}"):
        return f"path {path!r} may only use {token or 'no placeholder'} in the {chosen_scope} scope"
    declared = entry.get("format", _EXTENSIONS.get(PurePosixPath(path).suffix))
    if (chosen_format := _FORMATS.get(declared)) is None:
        return f"path {path!r} needs a known format"
    header = entry.get("header", True)
    if not isinstance(header, bool) or (header and chosen_format != "python"):
        return "header must be a boolean, and false for files that are not Python"
    return ExtraFile(
        template=template,
        path=path,
        scope=chosen_scope,
        format=chosen_format,
        header=header,
    )


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_list(value: object) -> TypeIs[list[object]]:
    return isinstance(value, list)


def _relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\\" not in value


def invalid(format: Format, text: str) -> str | None:  # noqa: A002
    """Return why a rendered JSON, TOML, or YAML file does not parse; other formats are not parsed."""
    import json  # noqa: PLC0415

    from yaml import YAMLError  # noqa: PLC0415

    from datamodel_code_generator._source import load_yaml  # noqa: PLC0415

    try:
        match format:
            case "json":
                json.loads(text)
            case "toml":
                _toml(text)
            case "yaml":
                load_yaml(text)
            case _:
                pass
    except (ValueError, YAMLError) as error:
        return str(error).splitlines()[0]
    return None


def _toml(text: str) -> None:
    from datamodel_code_generator.util import _get_toml_loader  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]

    _get_toml_loader()(BytesIO(text.encode()))
