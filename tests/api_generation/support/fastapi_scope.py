"""Run server generation with unsupported settings in a fresh interpreter and list the modules it imported."""

from __future__ import annotations

import json
import sys
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

WATCHED = (
    "datamodel_code_generator.remote_lock",
    "datamodel_code_generator._publication",
    "datamodel_code_generator._openapi_generation",
)
TARGET = (
    "datamodel_code_generator._target_cli",
    "datamodel_code_generator._target_selection",
    "datamodel_code_generator._api_generation",
    "datamodel_code_generator._fastapi",
    "datamodel_code_generator._client",
)
EMPTY = 'openapi: 3.1.0\ninfo: {title: Empty, version: "1.0"}\npaths: {}\n'
PRIMITIVE = EMPTY + "components:\n  schemas:\n    Name: {type: string}\n"
UNSELECTED = (
    'openapi: 3.1.0\ninfo: {title: Unselected, version: "1.0"}\npaths:\n  /names:\n    get:\n'
    "      tags: [names]\n      responses:\n        '204': {description: Done.}\n"
)
INCLUDED = {"unselected": ["/none"]}


def _write(root: Path) -> None:
    for name, text in (("primitive", PRIMITIVE), ("empty", EMPTY), ("unselected", UNSELECTED)):
        (root / f"{name}.yaml").write_text(text, encoding="utf-8")


def _generate(root: Path, name: str, backend: str, *, output: bool, sentinel: bool = False) -> str:
    """Run generate() with the server options, writing to the model output or returning the files without one."""
    from datamodel_code_generator import Error, generate

    try:
        result = generate(
            root / f"{name}.yaml",
            input_file_type="openapi",
            openapi_scopes=["schemas", "api"],
            output=root / "models.py" if output else None,
            output_model_type=backend,
            target_python_version="3.11",
            use_missing_sentinel=sentinel,
            openapi_include_paths=INCLUDED.get(name),
            generate_server="fastapi",
            server_output=root / "server",
            server_package="server",
            server_model_package="models",
        )
    except Error as error:
        return f"Error: {error}"
    return "ok" if result is None else f"{len(result)} files"


def _cli(root: Path, name: str, backend: str) -> str:
    from datamodel_code_generator.__main__ import main

    arguments = [
        *("--input", str(root / f"{name}.yaml"), "--input-file-type", "openapi", "--openapi-scopes", "schemas", "api"),
        *(
            "--output",
            str(root / "models.py"),
            "--output-model-type",
            backend,
            "--target-python-version",
            "3.11",
            "--check",
        ),
        *("--generate-server", "fastapi", "--server-output", str(root / "server")),
        *("--server-package", "server", "--server-model-package", "models"),
        *(f"--openapi-include-paths={path}" for path in INCLUDED.get(name, ())),
    ]
    stderr = StringIO()
    with redirect_stderr(stderr):
        code = main(arguments)
    return f"exit {int(code)} {stderr.getvalue().strip()}"


def _ordinary(root: Path) -> str:
    """Generate models without a target through generate() and the command line, naming the target modules loaded."""
    from datamodel_code_generator import generate
    from datamodel_code_generator.__main__ import main

    generated = generate(root / "primitive.yaml", input_file_type="openapi", server_layout="single")
    arguments = ["--input", str(root / "primitive.yaml"), "--input-file-type", "openapi"]
    code = main([*arguments, "--output", str(root / "models.py")])
    loaded = sorted(name for name in sys.modules if name.startswith(TARGET))
    return f"generate {type(generated).__name__}, exit {int(code)}, target modules {loaded}"


def main(root: Path) -> None:
    """Run every entry point for each input, then print the runs, the watched modules they imported, and new files.

    The supported runs that return the files without an output follow the unsupported runs, whose imports are listed.
    """
    _write(root)
    ordinary = _ordinary(root)
    inputs = {path.name for path in root.iterdir()}
    loaded = {name for name in WATCHED if name in sys.modules}
    runs = [f"{name} check: {_cli(root, name, 'msgspec.Struct')}" for name in ("primitive", "empty", "unselected")]
    runs.extend(
        f"{name} generate() {mode}: {_generate(root, name, 'msgspec.Struct', output=mode == 'output')}"
        for name in ("primitive", "empty", "unselected")
        for mode in ("output", "memory")
    )
    runs.append(f"empty sentinel: {_generate(root, 'empty', 'msgspec.Struct', output=True, sentinel=True)}")
    imported = [name for name in WATCHED if name in sys.modules and name not in loaded]
    runs.extend(
        f"{name} generate() memory: {_generate(root, name, 'pydantic_v2.BaseModel', output=False)}"
        for name in ("primitive", "empty", "unselected")
    )
    written = sorted(path.name for path in root.iterdir() if path.name not in inputs and path.name != "__pycache__")
    print(json.dumps({"ordinary": ordinary, "runs": runs, "imported": imported, "written": written}))  # noqa: T201


if __name__ == "__main__":
    main(Path(sys.argv[1]))
