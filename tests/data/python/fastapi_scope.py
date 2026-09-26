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
    "datamodel_code_generator._api_publication",
    "datamodel_code_generator._openapi_generation",
    "probe_hooks",
    "probe_codecs",
)
TARGET = (
    "datamodel_code_generator._target_cli",
    "datamodel_code_generator._api_generation",
    "datamodel_code_generator._fastapi",
    "datamodel_code_generator.fastapi",
    "datamodel_code_generator.api_types",
)
HOOKS = '[[hooks]]\nmodule = "probe_hooks"\n'
EMPTY = 'openapi: 3.1.0\ninfo: {title: Empty, version: "1.0"}\npaths: {}\n'
PRIMITIVE = EMPTY + "components:\n  schemas:\n    Name: {type: string}\n"
UNSELECTED = (
    'openapi: 3.1.0\ninfo: {title: Unselected, version: "1.0"}\npaths:\n  /names:\n    get:\n'
    "      tags: [names]\n      responses:\n        '204': {description: Done.}\n"
)
SELECTION = '[selection]\nexclude_tags = ["names"]\nreason = "Nothing is selected"\n'


def _write(root: Path) -> None:
    (root / "probe_hooks.py").write_text("def transform(context):\n    return context\n", encoding="utf-8")
    (root / "probe_codecs.py").write_text("adapter = object()\n", encoding="utf-8")
    for name, text, extra in (
        ("primitive", PRIMITIVE, ""),
        ("empty", EMPTY, ""),
        ("unselected", UNSELECTED, SELECTION),
    ):
        (root / f"{name}.yaml").write_text(text, encoding="utf-8")
        target = 'schema_version = 1\npackage = "server"\nmodel_package = "models"\noutput = "server"\n'
        (root / f"{name}.toml").write_text(target + extra + HOOKS, encoding="utf-8")


def _api(root: Path, name: str, mode: str, backend: str, *, sentinel: bool) -> str:
    from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope
    from datamodel_code_generator.fastapi import (
        APIGenerationError,
        CodecAdapterRegistration,
        CodecCapabilities,
        FastAPIConfig,
        HookReference,
        TypeUseRef,
        generate_fastapi,
        render_fastapi,
    )

    model = GenerateConfig(
        output=root / "models.py",
        input_file_type="openapi",
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
        output_model_type=DataModelType(backend),
        use_missing_sentinel=sentinel,
    )
    adapter = CodecAdapterRegistration(
        kind="model",
        name="probe",
        import_ref="probe_codecs:adapter",
        dependencies=(),
        python_requires=">=3.10",
        capabilities=CodecCapabilities(backends=("msgspec.Struct",), native_kinds=("model",)),
        uses=(TypeUseRef(operation="/paths/~1names/get", role="response_body", status="204", media_type="*/*"),),
    )
    config = FastAPIConfig(
        output=root / "server",
        package="server",
        model_package="models",
        model_mode="verify" if mode == "verify" else "generate",
        hooks=(HookReference(module="probe_hooks"),),
        codec_adapters=(adapter,),
    )
    entry = render_fastapi if mode == "render" else generate_fastapi
    try:
        entry(root / f"{name}.yaml", model_config=model, config=config)
    except APIGenerationError as error:
        return ", ".join(item.code for item in error.diagnostics)
    except Exception as error:  # noqa: BLE001
        return type(error).__name__
    return "ok"


def _cli(root: Path, name: str, backend: str) -> str:
    from datamodel_code_generator.__main__ import main

    arguments = [
        *("--input", str(root / f"{name}.yaml"), "--input-file-type", "openapi", "--openapi-scopes", "schemas", "api"),
        *("--output", str(root / "models.py"), "--output-model-type", backend, "--check"),
        *("--generate-server", "fastapi", "--target-config", str(root / f"{name}.toml")),
    ]
    stderr = StringIO()
    with redirect_stderr(stderr):
        code = main(arguments)
    return f"exit {int(code)} {stderr.getvalue().split(' ', 1)[0]}"


def _ordinary(root: Path) -> str:
    from datamodel_code_generator.__main__ import main  # noqa: PLC0415

    arguments = ["--input", str(root / "primitive.yaml"), "--input-file-type", "openapi"]
    code = main([*arguments, "--output", str(root / "models.py")])
    return f"exit {int(code)}, target modules {sorted(name for name in sys.modules if name.startswith(TARGET))}"


def main(root: Path) -> None:
    """Run every entry point for each input, then print the runs, the watched modules they imported, and new files."""
    sys.path.insert(0, str(root))
    _write(root)
    ordinary = _ordinary(root)
    import datamodel_code_generator.fastapi  # noqa: F401, PLC0415

    inputs = {path.name for path in root.iterdir()}
    loaded = {name for name in WATCHED if name in sys.modules}
    runs = [
        f"{name} {mode}: {_api(root, name, mode, 'msgspec.Struct', sentinel=False)}"
        for name in ("primitive", "empty", "unselected")
        for mode in ("generate", "render", "verify")
    ]
    runs.extend(f"{name} check: {_cli(root, name, 'msgspec.Struct')}" for name in ("primitive", "empty", "unselected"))
    runs.append(f"empty sentinel: {_api(root, 'empty', 'generate', 'msgspec.Struct', sentinel=True)}")
    imported = [name for name in WATCHED if name in sys.modules and name not in loaded]
    written = sorted(path.name for path in root.iterdir() if path.name not in inputs and path.name != "__pycache__")
    print(json.dumps({"ordinary": ordinary, "runs": runs, "imported": imported, "written": written}))  # noqa: T201


if __name__ == "__main__":
    main(Path(sys.argv[1]))
