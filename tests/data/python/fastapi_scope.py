"""Run the FastAPI entry points with unsupported settings in a fresh interpreter and list the modules they imported."""

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
    "datamodel_code_generator._api_generation",
    "datamodel_code_generator._fastapi",
    "datamodel_code_generator.fastapi",
    "datamodel_code_generator._client",
    "datamodel_code_generator.client",
    "datamodel_code_generator.api_types",
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


def _api(root: Path, name: str, mode: str, backend: str, *, sentinel: bool) -> str:
    from datamodel_code_generator import DataModelType, Error, GenerateConfig, OpenAPIScope
    from datamodel_code_generator.fastapi import (
        FastAPIConfig,
        generate_fastapi,
        render_fastapi,
    )

    model = GenerateConfig(
        output=root / "models.py",
        input_file_type="openapi",
        target_python_version="3.11",
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
        output_model_type=DataModelType(backend),
        use_missing_sentinel=sentinel,
        openapi_include_paths=INCLUDED.get(name),
    )
    config = FastAPIConfig(output=root / "server", package="server", model_package="models")
    entry = render_fastapi if mode == "render" else generate_fastapi
    try:
        entry(root / f"{name}.yaml", model_config=model, config=config)
    except Error as error:
        return f"Error: {error}"
    except Exception as error:  # noqa: BLE001
        return type(error).__name__
    return "ok"


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
    from datamodel_code_generator.__main__ import main  # noqa: PLC0415

    arguments = ["--input", str(root / "primitive.yaml"), "--input-file-type", "openapi"]
    code = main([*arguments, "--output", str(root / "models.py")])
    return f"exit {int(code)}, target modules {sorted(name for name in sys.modules if name.startswith(TARGET))}"


def main(root: Path) -> None:
    """Run every entry point for each input, then print the runs, the watched modules they imported, and new files."""
    _write(root)
    ordinary = _ordinary(root)
    import datamodel_code_generator.fastapi  # noqa: F401, PLC0415

    inputs = {path.name for path in root.iterdir()}
    loaded = {name for name in WATCHED if name in sys.modules}
    runs = [
        f"{name} {mode}: {_api(root, name, mode, 'msgspec.Struct', sentinel=False)}"
        for name in ("primitive", "empty", "unselected")
        for mode in ("generate", "render")
    ]
    runs.extend(f"{name} check: {_cli(root, name, 'msgspec.Struct')}" for name in ("primitive", "empty", "unselected"))
    runs.append(f"empty sentinel: {_api(root, 'empty', 'generate', 'msgspec.Struct', sentinel=True)}")
    imported = [name for name in WATCHED if name in sys.modules and name not in loaded]
    written = sorted(path.name for path in root.iterdir() if path.name not in inputs and path.name != "__pycache__")
    print(json.dumps({"ordinary": ordinary, "runs": runs, "imported": imported, "written": written}))  # noqa: T201


if __name__ == "__main__":
    main(Path(sys.argv[1]))
