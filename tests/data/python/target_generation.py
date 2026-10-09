"""Replay target scenarios through generate() and report the returned files, the written files, and failures."""

from __future__ import annotations

import json
import re
import shutil
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias
from urllib.parse import urlparse

import yaml

from datamodel_code_generator import Error, GenerateConfig, generate
from datamodel_code_generator.remote_lock import RemoteLockError, RemoteReferenceLock
from tests.data.python.client_generation import cyclic_input_failure

if TYPE_CHECKING:
    import pytest

SOURCE = Path(__file__).parents[1] / "generation_platform" / "targets"
_PRIVATE = re.compile(r"(?<![0-9a-f])(?:[0-9a-f]{32}|[0-9a-f]{16})(?![0-9a-f])")
_SERVER: dict[str, Any] = {
    "server_output": "server",
    "server_package": "example.server",
    "server_model_package": "example.models",
}
Files: TypeAlias = dict[tuple[str, ...], str]


def _runtime(path: Path) -> bool:
    return "_runtime" in path.parts


def _settings(model: dict[str, Any], server: dict[str, Any], root: Path, *, write: bool) -> GenerateConfig:
    """Build the generate() configuration of a step: its model options and the server options of the example target.

    A `{root}` document of an operation reference names the working directory. Without `write`, the run has no output
    and returns the files.
    """
    values = {"disable_timestamp": True, "formatters": [], **model}
    converted: dict[str, Any] = {"target_python_version": "3.11", "generate_server": "fastapi"}
    resolved = None
    for key, value in values.items():
        match key:
            case "output" | "emit_model_metadata" | "lockfile" | "custom_template_dir":
                converted[key] = None if value is None else Path(value)
            case "resolved_lock":
                resolved = value
            case _:
                converted[key] = value
    if not write:
        converted["output"] = None
    for key, value in server.items():
        match key:
            case "server_operation_names" | "server_primary_responses":
                converted[key] = {name.replace("{root}", Path.cwd().as_uri()): item for name, item in value.items()}
            case _:
                converted[key] = value
    config = GenerateConfig(**converted)
    if resolved is not None:
        config.resolve_remote_lock(
            RemoteReferenceLock.open(root / "resolved.lock", update=resolved == "update", locked=False)
        )
    return config


def _input(value: dict[str, Any], server: str | None) -> object:
    match value:
        case {"path": str() as path}:
            return Path("spec", path)
        case {"text": str() as path}:
            return Path("spec", path).read_text(encoding="utf-8")
        case {"mapping": str() as path}:
            return yaml.safe_load(Path("spec", path).read_text(encoding="utf-8"))
        case {"list": list() as paths}:
            return [Path("spec", path) for path in paths]
        case {"directory": str() as path}:
            return Path("spec", path)
        case {"url": str() as path}:
            return urlparse(f"{server}/{path}")
        case _:
            raise ValueError(value)


def _runtime_line(actions: list[str]) -> list[str]:
    return [f"  {len(actions)} runtime modules {sorted(set(actions))}"] if actions else []


def _step(case: dict[str, Any], overrides: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Merge a step's overrides into its case: the case itself, the model options, and the server options."""
    return (
        {**case, **{key: value for key, value in overrides.items() if key not in {"model", "config"}}},
        {"output": "models.py", **case.get("model", {}), **overrides.get("model", {})},
        {**_SERVER, **case.get("config", {}), **overrides.get("config", {})},
    )


def _written(parts: tuple[str, ...], model: dict[str, Any], server: dict[str, Any]) -> Path:
    """Return where a write run puts the file a run without an output returns under parts."""
    package, models = server["server_package"].split("."), server["server_model_package"].split(".")
    if list(parts[: len(package)]) == package:
        return Path(server["server_output"], *parts[len(package) :])
    return Path(model["output"], *parts[len(models) :])


def _state(location: Path, content: bytes) -> str:
    if not location.is_file():
        return "new"
    return "unchanged" if location.read_bytes() == content else "changed"


def _report_files(files: Files, model: dict[str, Any], server: dict[str, Any], lines: list[str]) -> None:
    """Report each returned file as new, changed, or unchanged against the file a write run would replace."""
    encoding = model.get("encoding", "utf-8")
    states = {
        (location := _written(parts, model, server)).as_posix(): _state(
            location, text.encode(encoding if parts[-1].endswith(".py") else "utf-8")
        )
        for parts, text in files.items()
    }
    lines.extend(f"  {state} {path}" for path, state in states.items() if not _runtime(Path(path)))
    lines.extend(_runtime_line([state for path, state in states.items() if _runtime(Path(path))]))


def _run(
    case: dict[str, Any], overrides: dict[str, Any], root: Path, server: str | None, *, write: bool
) -> Files | str | None:
    spec, model, config = _step(case, overrides)
    try:
        return generate(_input(spec["input"], server), config=_settings(model, config, root, write=write))
    except (Error, RemoteLockError, OSError, UnicodeError) as error:
        if case["input"] in ({"path": "input-cycle-dict.yaml"}, {"path": "input-cycle-reference.yaml"}):
            if not isinstance(error, Error):
                raise
            cycles = {
                "input-cycle-dict.yaml": ("input-cycle-dict.yaml", "/x-cycle/self", (12, 10)),
                "input-cycle-list.yaml": ("input-cycle-list.yaml", "/x-cycle/0", (12, 10)),
                "input-cycle-mutual.yaml": (
                    "input-cycle-mutual.yaml",
                    "/x-outer/nested/child/back~1to~0outer/0",
                    (13, 11),
                ),
                "input-cycle-reference.yaml": ("input-cycle-reference-model.yaml", "/x-cycle/self", (6, 10)),
            }
            filename, pointer, location = cycles[spec["input"]["path"]]
            return "  " + cyclic_input_failure(error, source=filename, pointer=pointer, location=location)
        name = "Error" if isinstance(error, Error) else type(error).__name__
        return (
            f"  {name}: {error}"
            .replace(root.resolve().as_posix(), "<root>")
            .replace(str(root.resolve()), "<root>")
            .replace("\\", "/")
        )


@dataclass
class _Scenario:
    """Replay one case's steps against a checkout, collecting the report lines."""

    case: dict[str, Any]
    root: Path
    monkeypatch: pytest.MonkeyPatch
    server: str | None
    lines: list[str] = field(default_factory=list)
    files: Files | None = None
    remembered: dict[str, Files] = field(default_factory=dict)

    @property
    def current(self) -> Files:
        if self.files is None:
            raise AssertionError(self.lines)
        return self.files

    def render(self, overrides: dict[str, Any]) -> None:
        """Generate without an output, reporting the returned files."""
        self.lines.append("render")
        match _run(self.case, overrides, self.root, self.server, write=False):
            case str() as failure:
                self.files = None
                self.lines.append(failure)
            case dict() as files:
                self.files = files
                _, model, server = _step(self.case, overrides)
                _report_files(files, model, server, self.lines)
            case report:
                raise AssertionError(report)

    def generate(self, overrides: dict[str, Any]) -> None:
        """Generate into the outputs, reporting the tree they leave."""
        self.lines.append("generate")
        match _run(self.case, overrides, self.root, self.server, write=True):
            case str() as failure:
                self.lines.append(failure)
            case None:
                self.lines.append("  returned None")
                self.tree(None)
            case project:
                raise AssertionError(project)

    def chmod(self, value: list[Any]) -> None:
        path, mode = value
        (self.root / path).chmod(int(mode, 8))
        self.lines.append(f"chmod {path} {mode}")

    def symlink(self, value: list[str]) -> None:
        link, target = value
        (self.root / link).symlink_to(target, target_is_directory=True)
        self.lines.append(f"symlink {link} -> {target}")

    def file_modes(self, paths: list[str]) -> None:
        for path in paths:
            self.lines.append(f"mode {path}: {(self.root / path).stat().st_mode & 0o777:04o}")

    def show(self, path: str) -> None:
        self.lines.append(f"show {path}")
        text = (self.root / path).read_bytes().decode("utf-8", errors="backslashreplace")
        self.lines.extend(f"  | {line}" for line in text.splitlines())

    def mkdir(self, path: str) -> None:
        (self.root / path).mkdir(parents=True)
        self.lines.append(f"mkdir {path}")

    def rewrite(self, value: list[Any]) -> None:
        newline, paths = value
        for path in paths:
            (location := self.root / path).write_text(
                location.read_text(encoding="utf-8"), encoding="utf-8", newline=newline
            )
        self.lines.append(f"rewrite with {newline!r} line endings {paths}")

    def newlines(self, paths: list[str]) -> None:
        for path in paths:
            (copy := self.root / "spec" / "text-mode-copy").write_text(
                (location := self.root / path).read_text(encoding="utf-8"), encoding="utf-8"
            )
            self.lines.append(f"written like a text file {path}: {copy.read_bytes() == location.read_bytes()}")

    def leftovers(self, _: None) -> None:
        hidden = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob(".*"))
        self.lines.append(f"leftovers {[_PRIVATE.sub('<private>', path) for path in hidden]}")

    def tree(self, _: None) -> None:
        self.lines.append("tree")
        files = [
            path.relative_to(self.root)
            for path in self.root.rglob("*")
            if path.is_file() and path.relative_to(self.root).parts[0] not in {"spec", "templates"}
        ]
        self.lines.extend(
            sorted(f"  {_PRIVATE.sub('<private>', path.as_posix())}" for path in files if not _runtime(path))
        )
        self.lines.extend(_runtime_line(["present" for path in files if _runtime(path)]))

    def write(self, value: list[str]) -> None:
        path, text = value
        (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        (self.root / path).write_bytes(text.encode())
        self.lines.append(f"write {path}")

    def remove(self, path: str) -> None:
        (self.root / path).unlink()
        self.lines.append(f"remove {path}")

    def remember(self, name: str) -> None:
        self.remembered[name] = self.current

    def compare(self, name: str) -> None:
        self.lines.append(f"files identical to {name}: {self.current == self.remembered[name]}")

    def relocate(self, name: str) -> None:
        other = self.root / name
        sources = list(self.root.iterdir())
        other.mkdir()
        for source in sources:
            (shutil.copytree if source.is_dir() else shutil.copy2)(source, other / source.name)
        self.monkeypatch.chdir(other)
        match _run(self.case, {}, other, self.server, write=False):
            case dict() as relocated:
                self.lines.append(f"relocated files identical: {relocated == self.current}")
            case failure:
                raise AssertionError(failure)
        self.monkeypatch.chdir(self.root)


def target_render_report(case_name: str, root: Path, monkeypatch: pytest.MonkeyPatch, server: str | None = None) -> str:
    """Run one scenario's renders, publications, and edits, reporting every observable outcome."""
    case = json.loads((SOURCE / "cases.json").read_text(encoding="utf-8"))[case_name]
    shutil.copytree(SOURCE / "spec", root / "spec")
    shutil.copytree(SOURCE / "templates", root / "templates")
    monkeypatch.chdir(root)
    scenario = _Scenario(case, root, monkeypatch, server, [f"# {case_name}"])
    for step in case["steps"]:
        ((name, value),) = step.items()
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always", UserWarning)
            getattr(scenario, name)(value)
        scenario.lines.extend(f"  {item.category.__name__}: {item.message}" for item in recorded)
    return "\n".join(scenario.lines) + "\n"
