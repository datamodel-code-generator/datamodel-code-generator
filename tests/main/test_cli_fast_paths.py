"""CLI startup fast-path regressions."""

from __future__ import annotations

import contextlib
import io
import json
import os
import runpy
import subprocess
import sys
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.conftest import assert_output

ROOT = Path(__file__).parents[2]
SRC = ROOT / "src"
MISSING = object()


def _run_probe(script: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{SRC}{os.pathsep}{env.get('PYTHONPATH', '')}"
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
        timeout=30,
    )
    return json.loads(result.stdout)


def _run_module_version_fast_path(version_option: str) -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            f"""
            import contextlib
            import io
            import json
            import runpy
            import sys

            sys.argv = ["datamodel-codegen", {version_option!r}]
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                try:
                    runpy.run_module("datamodel_code_generator.__main__", run_name="__main__", alter_sys=True)
                except SystemExit as exc:
                    code = exc.code
                else:
                    code = None

            from datamodel_code_generator._version import __version__

            print(json.dumps({{
                "code": code,
                "imported_metadata": "importlib.metadata" in sys.modules,
                "stdout_matches_embedded": stdout.getvalue() == "datamodel-codegen " + __version__ + "\\n",
            }}, indent=2, sort_keys=True))
            """
        )
    )


def _run_module_fast_path_in_process(args: list[str]) -> dict[str, Any]:
    module_name = "datamodel_code_generator.__main__"
    previous_module = sys.modules.pop(module_name, MISSING)
    original_argv = sys.argv[:]
    sys.argv = ["datamodel-codegen", *args]
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            try:
                runpy.run_module("datamodel_code_generator.__main__", run_name="__main__", alter_sys=True)
            except SystemExit as exc:
                code = exc.code
            else:  # pragma: no cover
                code = None
    finally:
        sys.argv = original_argv
        sys.modules.pop(module_name, None)
        if isinstance(previous_module, ModuleType):  # pragma: no branch
            sys.modules[module_name] = previous_module
    return {"code": code, "stdout": stdout.getvalue()}


def _run_module_schema_fast_path_in_process(schema_options: list[str]) -> dict[str, Any]:
    return _run_module_fast_path_in_process(schema_options)


def _run_module_version_fast_path_in_process(version_option: str) -> dict[str, Any]:
    result = _run_module_fast_path_in_process([version_option])

    from datamodel_code_generator import get_version

    return {
        "code": result["code"],
        "stdout_matches_embedded": result["stdout"] == f"datamodel-codegen {get_version()}\n",
    }


def _run_module_schema_fast_path(schema_options: list[str]) -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            f"""
            import contextlib
            import io
            import json
            import runpy
            import sys

            sys.argv = ["datamodel-codegen", *{schema_options!r}]
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                try:
                    runpy.run_module("datamodel_code_generator.__main__", run_name="__main__", alter_sys=True)
                except SystemExit as exc:
                    code = exc.code
                else:
                    code = None

            print(json.dumps({{
                "code": code,
                "stdout": stdout.getvalue(),
                "imported_arguments": "datamodel_code_generator.arguments" in sys.modules,
            }}))
            """
        )
    )


def _run_module_help_fast_path() -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            """
            import contextlib
            import io
            import json
            import runpy
            import sys

            sys.argv = ["datamodel-codegen", "--help"]
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                try:
                    runpy.run_module("datamodel_code_generator.__main__", run_name="__main__", alter_sys=True)
                except SystemExit as exc:
                    code = exc.code
                else:
                    code = None

            print(json.dumps({
                "code": code,
                "stdout": stdout.getvalue(),
                "imported_arguments": "datamodel_code_generator.arguments" in sys.modules,
                "imported_difflib": "difflib" in sys.modules,
                "imported_format": "datamodel_code_generator.format" in sys.modules,
                "imported_json_config": "datamodel_code_generator.json_config" in sys.modules,
                "imported_pydantic": "pydantic" in sys.modules,
                "imported_validators": "datamodel_code_generator.validators" in sys.modules,
            }))
            """
        )
    )


def _run_generate_prompt_invalid_option_fast_path() -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            """
            import contextlib
            import io
            import json
            import runpy
            import sys

            sys.argv = ["datamodel-codegen", "--generate-prompt", "--output-model-tipe"]
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                try:
                    runpy.run_module("datamodel_code_generator.__main__", run_name="__main__")
                except SystemExit as exc:
                    code = exc.code
                else:
                    code = None

            print(json.dumps({
                "code": code,
                "stderr": stderr.getvalue(),
                "imported_difflib": "difflib" in sys.modules,
                "imported_pydantic": "pydantic" in sys.modules,
            }, indent=2, sort_keys=True))
            """
        )
    )


def _run_agent_skill_install_fast_path() -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            """
            import contextlib
            import io
            import json
            import os
            import runpy
            import sys
            import tempfile
            from pathlib import Path

            previous_cwd = Path.cwd()
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                os.chdir(root)
                os.environ["HOME"] = str(root / "home")
                os.environ["USERPROFILE"] = str(root / "home")
                sys.argv = ["datamodel-codegen", "--install-skill", "codex"]
                stdout = io.StringIO()
                stderr = io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    try:
                        runpy.run_module("datamodel_code_generator.__main__", run_name="__main__", alter_sys=True)
                    except SystemExit as exc:
                        code = exc.code
                    else:
                        code = None
                target = root / ".agents" / "skills" / "datamodel-code-generator"
                result = {
                    "code": code,
                    "files": sorted(path.relative_to(target).as_posix() for path in target.rglob("*")),
                    "imported_config": "datamodel_code_generator.config" in sys.modules,
                    "imported_format": "datamodel_code_generator.format" in sys.modules,
                    "imported_model": "datamodel_code_generator.model" in sys.modules,
                    "imported_pydantic": "pydantic" in sys.modules,
                    "imported_reference": "datamodel_code_generator.reference" in sys.modules,
                    "imported_types": "datamodel_code_generator.types" in sys.modules,
                    "imported_validators": "datamodel_code_generator.validators" in sys.modules,
                    "stderr": stderr.getvalue(),
                    "stdout": stdout.getvalue().replace(str(target), "<target>"),
                }
                os.chdir(previous_cwd)

            print(json.dumps(result, indent=2, sort_keys=True))
            """
        )
    )


def _run_argument_parser_json_option_parse() -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            """
            import json
            import sys

            from datamodel_code_generator.arguments import arg_parser, namespace

            vars(namespace).clear()
            namespace.no_color = False
            arg_parser.parse_args(["--model-name-map", '{"User": "Account"}'], namespace=namespace)

            print(json.dumps({
                "model_name_map": namespace.model_name_map,
                "imported_json_config": "datamodel_code_generator.json_config" in sys.modules,
                "imported_pydantic": "pydantic" in sys.modules,
            }))
            """
        )
    )


def _run_main_import_probe() -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            """
            import json
            import sys

            from datamodel_code_generator.__main__ import main

            print(json.dumps({
                "main_callable": callable(main),
                "imported_config": "datamodel_code_generator.config" in sys.modules,
                "imported_format": "datamodel_code_generator.format" in sys.modules,
                "imported_builtin_formatter": "datamodel_code_generator._builtin_formatter" in sys.modules,
                "imported_model": "datamodel_code_generator.model" in sys.modules,
                "imported_pydantic": "pydantic" in sys.modules,
                "imported_reference": "datamodel_code_generator.reference" in sys.modules,
                "imported_types": "datamodel_code_generator.types" in sys.modules,
                "imported_validators": "datamodel_code_generator.validators" in sys.modules,
            }))
            """
        )
    )


def _run_file_url_http_import_probe() -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            """
            import json
            import sys

            from datamodel_code_generator.http import join_url

            joined = join_url("file:///schemas/root.json", "child.json")
            print(json.dumps({
                "imported_httpcore": "httpcore" in sys.modules,
                "imported_httpcore2": "httpcore2" in sys.modules,
                "imported_httpx": "httpx" in sys.modules,
                "imported_httpx2": "httpx2" in sys.modules,
                "joined": joined,
            }, indent=2, sort_keys=True))
            """
        )
    )


def _run_no_formatter_generation_probe() -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            """
            import json
            import sys
            from pathlib import Path

            from datamodel_code_generator import InputFileType, generate

            generated = generate(
                Path("tests/data/jsonschema/person.json"),
                input_file_type=InputFileType.JsonSchema,
                disable_timestamp=True,
                formatters=[],
            )
            print(json.dumps({
                "generated": generated,
                "imported_format": "datamodel_code_generator.format" in sys.modules,
                "imported_jinja2": "jinja2" in sys.modules,
                "imported_python_type_codec": (
                    "datamodel_code_generator._python_type_annotation_codec" in sys.modules
                ),
                "imported_python_type_ir": "datamodel_code_generator._python_type_annotation" in sys.modules,
            }, indent=2, sort_keys=True))
            """
        )
    )


def _run_custom_template_include_probe() -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            """
            import json
            import sys
            import tempfile
            from collections import defaultdict
            from pathlib import Path

            from datamodel_code_generator.model.pydantic_v2.base_model import BaseModel
            from datamodel_code_generator.reference import Reference

            reference = Reference(name="Custom", path="Custom")
            with tempfile.TemporaryDirectory() as directory:
                template_dir = Path(directory) / "pydantic_v2"
                template_dir.mkdir()
                (template_dir / "ConfigDict.jinja2").write_text("custom_config = True\\n", encoding="utf-8")
                model = BaseModel(
                    fields=[],
                    reference=reference,
                    custom_template_dir=Path(directory),
                    extra_template_data=defaultdict(dict, {reference.path: {"config": {"extra": '\"allow\"'}}}),
                )
                include_only_generated = model.render()
                root_dir = Path(directory) / "root" / "pydantic_v2"
                root_dir.mkdir(parents=True)
                (root_dir / "BaseModel.jinja2").write_text("root_custom = '{{ class_name }}'\\n", encoding="utf-8")
                root_model = BaseModel(
                    fields=[],
                    reference=reference,
                    custom_template_dir=root_dir.parent,
                )
                root_generated = root_model.render()

            print(json.dumps({
                "custom_include_rendered": "custom_config = True" in include_only_generated,
                "custom_root_rendered": root_generated == "root_custom = 'Custom'",
                "imported_jinja2": "jinja2" in sys.modules,
            }, indent=2, sort_keys=True))
            """
        )
    )


def _run_schema_runtime_validation_helper_probe() -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            """
            import json
            import sys
            from collections import defaultdict

            from datamodel_code_generator.model.pydantic_v2.base_model import BaseModel
            from datamodel_code_generator.model.runtime_validation import (
                RequiredGroupsRule,
                _make_internal_schema_runtime_validation,
            )
            from datamodel_code_generator.reference import Reference

            reference = Reference(name="RuntimeModel", path="RuntimeModel")
            validation = _make_internal_schema_runtime_validation(
                required_groups=[RequiredGroupsRule(keyword="oneOf", groups=((("value",),),))]
            )
            model = BaseModel(
                fields=[],
                reference=reference,
                extra_template_data=defaultdict(
                    dict,
                    {
                        reference.path: {
                            "schema_runtime_validation": validation,
                            "schema_runtime_validation_enabled": True,
                        }
                    },
                ),
            )
            generated = BaseModel.render_module_code([model])
            print(json.dumps({
                "generated_helper": "_JsonSchemaRuntimeValidationBase" in generated,
                "imported_jinja2": "jinja2" in sys.modules,
            }, indent=2, sort_keys=True))
            """
        )
    )


def _run_playground_builtin_template_probe() -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            """
            import importlib.abc
            import importlib.util
            import json
            import sys
            from pathlib import Path

            from scripts.build_playground_assets import build_metadata

            class BlockBrowserTemplatePackages(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.partition(".")[0] in {"jinja2", "markupsafe"}:
                        raise ModuleNotFoundError(f"{fullname} is not installed in the browser runtime")
                    return None

            sys.meta_path.insert(0, BlockBrowserTemplatePackages())
            runtime_path = Path("docs/assets/playground/runtime.py")
            spec = importlib.util.spec_from_file_location("playground_runtime", runtime_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("Could not load playground runtime")
            runtime = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(runtime)

            metadata = build_metadata()
            runtime.set_ui_metadata(json.dumps(metadata))
            result = json.loads(
                runtime.generate_in_browser(
                    runtime.sample_schema("jsonschema"),
                    "jsonschema",
                    json.dumps({"custom_template_dir": "templates"}),
                )
            )
            custom_template = next(option for option in metadata["options"] if option["dest"] == "custom_template_dir")
            cli_options = runtime.build_cli_options(
                json.dumps({"custom_template_dir": "templates"}),
                "jsonschema",
            )
            print(json.dumps({
                "custom_template_browser_supported": custom_template["browser_supported"],
                "custom_template_ignored": "--custom-template-dir" not in cli_options,
                "generated_person": result["ok"] and "class Pet(BaseModel):" in result["output"],
                "imported_jinja2": "jinja2" in sys.modules,
                "imported_markupsafe": "markupsafe" in sys.modules,
            }, indent=2, sort_keys=True))
            """
        )
    )


def _run_input_model_type_transport_probe() -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            """
            import contextlib
            import io
            import json
            import sys

            from datamodel_code_generator.__main__ import main

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main([
                    "--input-model",
                    "tests.data.python.input_model.structured_annotations:StructuredAnnotations",
                    "--field-include-all-keys",
                    "--disable-timestamp",
                ])
            generated = stdout.getvalue()
            print(json.dumps({
                "code": int(code),
                "generated": generated,
                "imported_python_type_codec": (
                    "datamodel_code_generator._python_type_annotation_codec" in sys.modules
                ),
                "leaked_private_token": "<datamodel-code-generator-python-type:" in generated,
            }, indent=2, sort_keys=True))
            """
        )
    )


def _run_input_model_without_runtime_type_probe() -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            """
            import json
            import secrets
            import sys

            from datamodel_code_generator import InputFileType
            from datamodel_code_generator.input_model import (
                _load_model_schema_with_python_type_expressions,
            )

            # Pydantic 2.14 imports secrets itself, so count nonce draws instead of checking the module.
            nonces = []
            token_hex = secrets.token_hex
            secrets.token_hex = lambda *args: nonces.append(args) or token_hex(*args)
            loaded = _load_model_schema_with_python_type_expressions(
                ["tests.data.python.input_model.pydantic_models:User"],
                InputFileType.Auto,
            )
            print(json.dumps({
                "expression_count": len(loaded.python_type_expressions),
                "drew_nonce": bool(nonces),
                "imported_python_type_codec": (
                    "datamodel_code_generator._python_type_annotation_codec" in sys.modules
                ),
            }, indent=2, sort_keys=True))
            """
        )
    )


def _run_external_ref_type_transport_probe(ref_path: str) -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            f"""
            import json
            import sys

            from datamodel_code_generator import InputFileType, generate
            from datamodel_code_generator._input_model_transport import PythonTypeExpressionCollector
            from datamodel_code_generator._python_type_annotation import PythonTypeName

            collector = PythonTypeExpressionCollector()
            token = collector.add(PythonTypeName("int"))
            loaded = collector.loaded_schema({{
                "title": "Root",
                "type": "object",
                "properties": {{
                    "value": {{"type": "string", "x-python-type": token}},
                    "external": {{"$ref": {ref_path!r}}},
                }},
                "required": ["value", "external"],
            }})
            generated = generate(
                loaded,
                input_file_type=InputFileType.JsonSchema,
                disable_timestamp=True,
                formatters=[],
            )
            print(json.dumps({{
                "has_datetime_import": "from datetime import datetime" in generated,
                "imported_python_type_codec": (
                    "datamodel_code_generator._python_type_annotation_codec" in sys.modules
                ),
                "leaked_private_token": "<datamodel-code-generator-python-type:" in generated,
            }}, indent=2, sort_keys=True))
            """
        )
    )


def _run_python_type_codec_import_order_probe() -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            """
            import json

            from datamodel_code_generator._python_type_annotation_codec import (
                parse_python_type_annotation as codec_parse,
            )
            from datamodel_code_generator._python_type_annotation import (
                parse_python_type_annotation as public_parse,
                render_python_type_expr,
            )

            first = codec_parse("tuple[str, int]")
            second = public_parse("tuple[str, int]")
            print(json.dumps({
                "same_expression": first is second,
                "same_parser": codec_parse is public_parse,
                "value": render_python_type_expr(first),
            }, indent=2, sort_keys=True))
            """
        )
    )


def _run_invalid_args_probe() -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            """
            import contextlib
            import io
            import json
            import sys

            from datamodel_code_generator.__main__ import main

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                try:
                    main(["--unknown-option"])
                except SystemExit as exc:
                    code = exc.code
                else:
                    code = None

            print(json.dumps({
                "code": code,
                "stderr": stderr.getvalue(),
                "imported_pydantic": "pydantic" in sys.modules,
            }))
            """
        )
    )


def _run_config_api_probe() -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            """
            import json

            import datamodel_code_generator.__main__ as main_module
            from datamodel_code_generator.__main__ import Config

            default_config = Config()
            attribute_config = main_module.Config
            validated = Config.model_validate({
                "validators": {
                    "User": {
                        "validators": [
                            {"field": "name", "function": "myapp.validators.validate_name"}
                        ]
                    }
                }
            })
            json_validated = Config.model_validate_json('{"input_file_type": "jsonschema"}')
            strings_validated = Config.model_validate_strings({"input_file_type": "openapi"})
            schema = Config.model_json_schema()

            try:
                Config(validators={
                    "User": {
                        "validators": [
                            {"field": "bad-name", "function": "myapp.validators.validate_name"}
                        ]
                    }
                })
            except Exception as exc:
                invalid_message = str(exc).splitlines()[0]
            else:
                invalid_message = None

            print(json.dumps({
                "default_input_file_type": default_config.input_file_type.value,
                "same_config_class": Config is attribute_config,
                "validator_function": validated.validators["User"].validators[0].function,
                "json_input_file_type": json_validated.input_file_type.value,
                "strings_input_file_type": strings_validated.input_file_type.value,
                "schema_title": schema["title"],
                "invalid_message": invalid_message,
            }))
            """
        )
    )


def _run_cli_generate_config_import_probe() -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            """
            import json
            import sys
            import tempfile
            from pathlib import Path

            from datamodel_code_generator.__main__ import Config, run_generate_from_config

            schema = (
                '{"openapi":"3.0.0","info":{"title":"T","version":"1"},"paths":{},'
                '"components":{"schemas":{"User":{"type":"object","properties":{"id":{"type":"integer"}}}}}}'
            )
            config = Config.model_validate({
                "disable_timestamp": True,
                "input_file_type": "openapi",
                "output_model_type": "pydantic_v2.BaseModel",
            })
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "models.py"
                run_generate_from_config(config, schema, output, None, None, None, None, None)
                generated = output.read_text()

            print(json.dumps({
                "generated_user": "class User" in generated,
                "imported_config": "datamodel_code_generator.config" in sys.modules,
            }))
            """
        )
    )


def _run_parsed_schema_path(schema_name: str) -> dict[str, Any]:
    return _run_probe(
        textwrap.dedent(
            f"""
            import contextlib
            import io
            import json

            from datamodel_code_generator.__main__ import main

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--output-format-json-schema", {schema_name!r}])

            print(json.dumps({{"code": code, "stdout": stdout.getvalue()}}))
            """
        )
    )


@pytest.mark.allow_direct_assert
def test_output_format_json_schema_exact_fast_paths_skip_argument_parser_import() -> None:
    """Exact JSON Schema utility invocations bypass argparse without changing output."""
    for schema_name in ("config", "generation", "model-metadata", "structured-output"):
        parsed_path = _run_parsed_schema_path(schema_name)
        assert parsed_path["code"] == 0
        fast_paths = [
            _run_module_schema_fast_path([f"--output-format-json-schema={schema_name}"]),
            _run_module_schema_fast_path(["--output-format-json-schema", schema_name]),
        ]
        covered_fast_paths = [
            _run_module_schema_fast_path_in_process([f"--output-format-json-schema={schema_name}"]),
            _run_module_schema_fast_path_in_process(["--output-format-json-schema", schema_name]),
        ]

        for fast_path in fast_paths:
            assert fast_path["code"] == 0
            assert fast_path["imported_arguments"] is False
            assert fast_path["stdout"] == parsed_path["stdout"]
            schema = json.loads(fast_path["stdout"])
            assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        for fast_path in covered_fast_paths:
            assert fast_path["code"] == 0
            assert fast_path["stdout"] == parsed_path["stdout"]


@pytest.mark.allow_direct_assert
def test_help_fast_path_skips_json_config_and_formatter_imports() -> None:
    """--help builds argparse choices without importing validation or formatter runtimes."""
    fast_path = _run_module_help_fast_path()

    assert fast_path["code"] == 0
    assert "Generate Python data models" in fast_path["stdout"]
    assert fast_path["imported_arguments"] is True
    assert fast_path["imported_difflib"] is False
    assert fast_path["imported_format"] is False
    assert fast_path["imported_json_config"] is False
    assert fast_path["imported_pydantic"] is False
    assert fast_path["imported_validators"] is False


@pytest.mark.parametrize("version_option", ["--version", "-V"])
def test_version_fast_path_uses_embedded_version(version_option: str) -> None:
    """Read the build version without importing distribution metadata."""
    output = {
        "in_process": _run_module_version_fast_path_in_process(version_option),
        "subprocess": _run_module_version_fast_path(version_option),
    }

    assert_output(
        f"{json.dumps(output, indent=2, sort_keys=True)}\n",
        ROOT / "tests/data/expected/main/cli_fast_paths/version_fast_path.txt",
    )


def test_agent_skill_fast_path_skips_generation_imports() -> None:
    """Agent Skill installation copies the bundle without loading generation dependencies."""
    result = _run_agent_skill_install_fast_path()

    assert_output(
        f"{json.dumps(result, indent=2, sort_keys=True)}\n",
        ROOT / "tests/data/expected/main/cli_fast_paths/agent_skill_install.txt",
    )


@pytest.mark.allow_direct_assert
def test_argument_parser_json_option_loads_json_config_lazily() -> None:
    """JSON-backed argparse callbacks still load and validate only when invoked."""
    parsed = _run_argument_parser_json_option_parse()

    assert parsed["model_name_map"] == {"User": "Account"}
    assert parsed["imported_json_config"] is True
    assert parsed["imported_pydantic"] is True


@pytest.mark.allow_direct_assert
def test_main_import_skips_formatter_runtime() -> None:
    """Importing CLI main does not load formatter runtime until a black check is needed."""
    imported = _run_main_import_probe()

    assert imported["main_callable"] is True
    assert imported["imported_config"] is False
    assert imported["imported_format"] is False
    assert imported["imported_builtin_formatter"] is False
    assert imported["imported_model"] is False
    assert imported["imported_pydantic"] is False
    assert imported["imported_reference"] is False
    assert imported["imported_types"] is False
    assert imported["imported_validators"] is False


def test_empty_formatters_skip_formatter_runtime() -> None:
    """Explicit empty formatters keep the formatter runtime out of a fresh process."""
    result = _run_no_formatter_generation_probe()

    assert_output(
        f"{json.dumps(result, indent=2, sort_keys=True)}\n",
        ROOT / "tests/data/expected/main/cli_fast_paths/empty_formatters.txt",
    )


def test_custom_template_include_uses_jinja_in_a_fresh_process() -> None:
    """A custom directory keeps the complete root/include operation on Jinja."""
    result = _run_custom_template_include_probe()

    assert_output(
        f"{json.dumps(result, indent=2, sort_keys=True)}\n",
        ROOT / "tests/data/expected/main/cli_fast_paths/custom_template_include.txt",
    )


def test_schema_runtime_validation_module_helper_skips_jinja_in_a_fresh_process() -> None:
    """Module-level built-in rendering also uses a generated standalone renderer."""
    result = _run_schema_runtime_validation_helper_probe()

    assert_output(
        f"{json.dumps(result, indent=2, sort_keys=True)}\n",
        ROOT / "tests/data/expected/main/cli_fast_paths/schema_runtime_validation_helper.txt",
    )


@pytest.mark.skipif(sys.version_info < (3, 11), reason="The Playground runtime targets Pyodide Python 3.14")
def test_playground_builtin_generation_skips_jinja_and_custom_templates() -> None:
    """The browser runtime uses compiled templates and filters custom template input."""
    result = _run_playground_builtin_template_probe()

    assert_output(
        f"{json.dumps(result, indent=2, sort_keys=True)}\n",
        ROOT / "tests/data/expected/main/cli_fast_paths/playground_builtin_templates.txt",
    )


def test_input_model_transport_skips_codec_and_preserves_metadata() -> None:
    """Internal type IR reaches generated metadata without loading the text codec."""
    result = _run_input_model_type_transport_probe()

    assert_output(
        f"{json.dumps(result, indent=2, sort_keys=True)}\n",
        ROOT / "tests/data/expected/main/cli_fast_paths/input_model_type_transport.txt",
    )


def test_input_model_without_runtime_type_skips_nonce_and_codec() -> None:
    """The loader avoids nonce entropy and the text codec when no IR is collected."""
    result = _run_input_model_without_runtime_type_probe()

    assert_output(
        f"{json.dumps(result, indent=2, sort_keys=True)}\n",
        ROOT / "tests/data/expected/main/cli_fast_paths/input_model_without_runtime_type.txt",
    )


def test_external_ref_parses_only_external_raw_python_type() -> None:
    """External refs retain IR while raw external extension text uses the codec."""
    result = {
        "ordinary_external": _run_external_ref_type_transport_probe("tests/data/jsonschema/person.json"),
        "raw_python_type_external": _run_external_ref_type_transport_probe(
            "tests/data/jsonschema/external_python_type.json"
        ),
    }

    assert_output(
        f"{json.dumps(result, indent=2, sort_keys=True)}\n",
        ROOT / "tests/data/expected/main/cli_fast_paths/external_ref_type_transport.txt",
    )


def test_python_type_codec_supports_codec_first_import() -> None:
    """The lazy public API remains valid when the raw codec is imported first."""
    result = _run_python_type_codec_import_order_probe()

    assert_output(
        f"{json.dumps(result, indent=2, sort_keys=True)}\n",
        ROOT / "tests/data/expected/main/cli_fast_paths/python_type_codec_import_order.txt",
    )


def test_file_url_join_skips_http_backend_imports() -> None:
    """File URL handling keeps every optional HTTP package out of a fresh process."""
    result = _run_file_url_http_import_probe()

    assert_output(
        f"{json.dumps(result, indent=2, sort_keys=True)}\n",
        ROOT / "tests/data/expected/main/cli_fast_paths/file_url_http_imports.txt",
    )


@pytest.mark.allow_direct_assert
def test_invalid_args_skip_pydantic_import() -> None:
    """Argparse errors exit before loading CLI Config or Pydantic."""
    invalid_args = _run_invalid_args_probe()

    assert invalid_args["code"] == 2
    assert "--unknown-option" in invalid_args["stderr"]
    assert invalid_args["imported_pydantic"] is False


def test_generate_prompt_fast_path_suggests_invalid_options() -> None:
    """The prompt fast path retains unknown-option suggestions."""
    result = _run_generate_prompt_invalid_option_fast_path()

    assert_output(
        f"{json.dumps(result, indent=2, sort_keys=True)}\n",
        ROOT / "tests/data/expected/main/cli_fast_paths/generate_prompt_invalid_option.txt",
    )


@pytest.mark.allow_direct_assert
def test_cli_config_public_construction_rebuilds_lazy_validator_types() -> None:
    """CLI Config keeps direct construction and validators validation while imports stay lazy."""
    config = _run_config_api_probe()

    assert config["default_input_file_type"] == "auto"
    assert config["same_config_class"] is True
    assert config["validator_function"] == "myapp.validators.validate_name"
    assert config["json_input_file_type"] == "jsonschema"
    assert config["strings_input_file_type"] == "openapi"
    assert config["schema_title"] == "Config"
    assert "bad-name" in config["invalid_message"]


@pytest.mark.allow_direct_assert
def test_cli_config_public_validation_methods_handle_lazy_validator_types() -> None:
    """Coverage for public Config validation methods with lazy validator types."""
    from datamodel_code_generator.__main__ import Config

    validated = Config.model_validate({
        "validators": {"User": {"validators": [{"field": "name", "function": "myapp.validators.validate_name"}]}}
    })
    none_validated = Config.model_validate({"validators": None})
    json_validated = Config.model_validate_json('{"input_file_type": "jsonschema"}')
    strings_validated = Config.model_validate_strings({"input_file_type": "openapi"})
    schema = Config.model_json_schema()

    assert validated.validators["User"].validators[0].function == "myapp.validators.validate_name"
    assert none_validated.validators is None
    assert json_validated.input_file_type.value == "jsonschema"
    assert strings_validated.input_file_type.value == "openapi"
    assert schema["title"] == "Config"


@pytest.mark.allow_direct_assert
def test_cli_generation_with_validated_config_skips_parser_config_import() -> None:
    """Internal CLI generation reuses validated config without importing parser config models."""
    generated = _run_cli_generate_config_import_probe()

    assert generated["generated_user"] is True
    assert generated["imported_config"] is False
