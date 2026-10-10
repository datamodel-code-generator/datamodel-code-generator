"""Replay target scenarios on the command line and report what each run prints and the files it leaves."""

from __future__ import annotations

import io
import json
import re
import shutil
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.util import get_yaml_backend
from tests.main.conftest import run_main_with_args

if TYPE_CHECKING:
    import pytest

SOURCE = Path(__file__).parents[1] / "generation_platform" / "targets"
_PRIVATE = re.compile(r"(?<![0-9a-f])(?:[0-9a-f]{32}|[0-9a-f]{16})(?![0-9a-f])")
_CHECKED = re.compile(r"^(?:(?P<kind>MISSING|EXTRA): |--- )(?P<path>\S+)")
_MODEL: dict[str, Any] = {
    "input_file_type": "openapi",
    "output": "models.py",
    "target_python_version": "3.11",
    "disable_timestamp": True,
    "formatters": ["builtin"],
}
_SERVER: dict[str, Any] = {
    "generate_server": "fastapi",
    "server_output": "server",
    "server_package": "example.server",
    "server_model_package": "example.models",
}
_DEPENDENCY_NOTICE = "Add the runtime dependencies of "
_TRACEBACK = "Traceback (most recent call last):"


def _runtime(path: str) -> bool:
    return "_runtime" in Path(path).parts


def _runtime_line(states: list[str]) -> list[str]:
    return [f"  {len(states)} runtime modules {sorted(set(states))}"] if states else []


def _options(values: dict[str, Any]) -> list[str]:
    """Spell options as the command line takes them: a flag per true value, a JSON object per mapping.

    A `{root}` document of an operation reference names the working directory.
    """
    args: list[str] = []
    for key, value in values.items():
        flag = f"--{key.replace('_', '-')}"
        match key, value:
            case _, bool():
                args.extend([flag] if value else [])
            case "custom_formatters", list():
                args.extend([flag, ",".join(value)])
            case _, list():
                args.extend([flag, *value])
            case _, dict():
                spelled = {name.replace("{root}", Path.cwd().as_uri()): item for name, item in value.items()}
                args.extend([flag, json.dumps(spelled)])
            case _:
                args.extend([flag, str(value)])
    return args


def _input(value: dict[str, Any], server: str | None) -> list[str]:
    match value:
        case {"path": str() as path} | {"directory": str() as path}:
            return ["--input", Path("spec", path).as_posix()]
        case {"url": str() as path}:
            return ["--url", f"{server}/{path}"]
        case {"text": str()}:
            return []
        case _:
            raise ValueError(value)


def _exit(value: str | dict[str, str]) -> Exit:
    """Return the exit a run expects; a mapping names it per YAML backend, which may reject an input."""
    return Exit[value if isinstance(value, str) else value[get_yaml_backend()]]


def _printed(stdout: str, stderr: str, root: Path) -> list[str]:
    """Report the files a check names and what a run prints on stderr, leaving out diff bodies.

    Copied runtime modules are counted. The runtime dependency notice is pinned with the command line's own output,
    and the frames of a traceback name the checkout, so both are left out.
    """
    lines, runtime = [], []
    for line in stdout.splitlines():
        if (found := _CHECKED.match(line)) is None:
            continue
        if _runtime(found["path"]):
            runtime.append(found["kind"] or "changed")
        else:
            lines.append(f"  {line}")
    lines.extend(_runtime_line(runtime))
    errors = stderr.replace(root.resolve().as_posix(), "<root>").replace(str(root.resolve()), "<root>")
    skipped = False
    for line in filter(None, errors.replace("\\", "/").splitlines()):
        skipped = line.startswith(_DEPENDENCY_NOTICE) or (skipped and line.startswith("  "))
        lines.extend([] if skipped else [f"  {line}"])
        skipped = skipped or line == _TRACEBACK
    return lines


@dataclass
class _Scenario:
    """Replay one case's steps against a checkout, collecting the report lines."""

    case: dict[str, Any]
    root: Path
    monkeypatch: pytest.MonkeyPatch
    capsys: pytest.CaptureFixture[str]
    server: str | None
    lines: list[str] = field(default_factory=list)

    def _run(self, overrides: dict[str, Any], *, check: bool) -> Exit:
        """Run the command line with the case's options and a step's overrides, reporting what it prints."""
        source = overrides.get("input", self.case["input"])
        model = {**_MODEL, **self.case.get("model", {}), **overrides.get("model", {})}
        server = {**_SERVER, **self.case.get("config", {}), **overrides.get("config", {})}
        args = [*_input(source, self.server), *_options(model), *_options(server), *(["--check"] if check else [])]
        if "text" in source:
            text = Path("spec", source["text"]).read_text(encoding="utf-8")
            self.monkeypatch.setattr("sys.stdin", io.StringIO(text))
        self.capsys.readouterr()
        exit_ = run_main_with_args(args, expected_exit=_exit(overrides.get("exit", "OK")))
        captured = self.capsys.readouterr()
        self.lines.extend(_printed(captured.out, captured.err, self.root))
        return exit_

    def check(self, overrides: dict[str, Any]) -> None:
        """Check the outputs against a run with the step's options, reporting the files it would write."""
        self.lines.append("check")
        self._run(overrides, check=True)

    def generate(self, overrides: dict[str, Any]) -> None:
        """Generate into the outputs, reporting the tree a successful run leaves."""
        self.lines.append("generate")
        if self._run(overrides, check=False) is Exit.OK:
            self.tree(None)

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
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file() and path.relative_to(self.root).parts[0] not in {"spec", "templates"}
        ]
        self.lines.extend(sorted(f"  {_PRIVATE.sub('<private>', path)}" for path in files if not _runtime(path)))
        self.lines.extend(_runtime_line(["present" for path in files if _runtime(path)]))

    def write(self, value: list[str]) -> None:
        path, text = value
        (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        (self.root / path).write_bytes(text.encode())
        self.lines.append(f"write {path}")

    def remove(self, path: str) -> None:
        (self.root / path).unlink()
        self.lines.append(f"remove {path}")

    def relocate(self, name: str) -> None:
        """Copy the checkout to a directory below it and check the copy there, where nothing may differ."""
        other = self.root / name
        sources = list(self.root.iterdir())
        other.mkdir()
        for source in sources:
            (shutil.copytree if source.is_dir() else shutil.copy2)(source, other / source.name)
        self.monkeypatch.chdir(other)
        self.lines.append(f"check in {name}")
        self._run({}, check=True)
        self.monkeypatch.chdir(self.root)


def target_render_report(
    case_name: str,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    server: str | None = None,
) -> str:
    """Run one scenario's checks, publications, and edits, reporting every observable outcome."""
    case = json.loads((SOURCE / "cases.json").read_text(encoding="utf-8"))[case_name]
    shutil.copytree(SOURCE / "spec", root / "spec")
    shutil.copytree(SOURCE / "templates", root / "templates")
    monkeypatch.chdir(root)
    scenario = _Scenario(case, root, monkeypatch, capsys, server, [f"# {case_name}"])
    for step in case["steps"]:
        ((name, value),) = step.items()
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always", UserWarning)
            getattr(scenario, name)(value)
        scenario.lines.extend(f"  {item.category.__name__}: {item.message}" for item in recorded)
    return "\n".join(scenario.lines) + "\n"
