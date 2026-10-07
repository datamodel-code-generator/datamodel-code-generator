"""Generate preset documentation and preset-powered quick-start examples.

Usage:
    python scripts/build_preset_docs.py
    python scripts/build_preset_docs.py --check
    python scripts/build_preset_docs.py --format json
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

ROOT = Path(__file__).resolve().parents[1]
DOCS_PATH = ROOT / "docs" / "presets.md"
DOCS_GETTING_STARTED_PATH = ROOT / "docs" / "getting-started.md"
DOCS_FASTAPI_SERVER_PATH = ROOT / "docs" / "fastapi-server.md"
README_PATH = ROOT / "README.md"
SRC_PATH = ROOT / "src"
PRESET_NAMES_PATH = SRC_PATH / "datamodel_code_generator" / "preset_names.py"
QUICK_START_SCHEMA_PATH = ROOT / "tests" / "data" / "jsonschema" / "preset_quick_start_pet.json"
QUICK_START_SCHEMA_NAME = "schema.json"
QUICK_START_OUTPUT_NAME = "model.py"
QUICK_START_TARGET_PYTHON_VERSION = "3.12"
PRESET_VERSION_DATE_LENGTH = 8
QUICK_START_BEGIN_MARKER = "<!-- BEGIN AUTO-GENERATED PRESET QUICK START -->"
QUICK_START_END_MARKER = "<!-- END AUTO-GENERATED PRESET QUICK START -->"
FASTAPI_QUICK_START_BEGIN_MARKER = "<!-- BEGIN AUTO-GENERATED FASTAPI QUICK START -->"
FASTAPI_QUICK_START_END_MARKER = "<!-- END AUTO-GENERATED FASTAPI QUICK START -->"
FASTAPI_PYTHON_API_BEGIN_MARKER = "<!-- BEGIN AUTO-GENERATED FASTAPI PYTHON API -->"
FASTAPI_PYTHON_API_END_MARKER = "<!-- END AUTO-GENERATED FASTAPI PYTHON API -->"
README_SUPPORTED_INPUT_BEGIN_MARKER = "<!-- BEGIN AUTO-GENERATED README SUPPORTED INPUT -->"
README_SUPPORTED_INPUT_END_MARKER = "<!-- END AUTO-GENERATED README SUPPORTED INPUT -->"
README_SUPPORTED_OUTPUT_BEGIN_MARKER = "<!-- BEGIN AUTO-GENERATED README SUPPORTED OUTPUT -->"
README_SUPPORTED_OUTPUT_END_MARKER = "<!-- END AUTO-GENERATED README SUPPORTED OUTPUT -->"
README_PRESETS_LINK = "https://datamodel-code-generator.koxudaxi.dev/presets/"
README_CLI_REFERENCE_LINK = "https://datamodel-code-generator.koxudaxi.dev/cli-reference/"
README_PRESET_OPTION_LINK = "https://datamodel-code-generator.koxudaxi.dev/cli-reference/base-options/#preset"
README_INPUT_FILE_TYPE_OPTION_LINK = (
    "https://datamodel-code-generator.koxudaxi.dev/cli-reference/base-options/#input-file-type"
)
README_OUTPUT_MODEL_TYPE_OPTION_LINK = (
    "https://datamodel-code-generator.koxudaxi.dev/cli-reference/model-customization/#output-model-type"
)
README_FORMATTER_BEHAVIOR_LINK = "https://datamodel-code-generator.koxudaxi.dev/formatter-behavior/"
README_PERFORMANCE_BENCHMARKS_LINK = "https://datamodel-code-generator.koxudaxi.dev/performance-benchmarks/"
DOCS_PRESETS_LINK = "presets.md"
DOCS_CLI_REFERENCE_LINK = "cli-reference/index.md"
DOCS_PRESET_OPTION_LINK = "cli-reference/base-options.md#preset"
DOCS_INPUT_FILE_TYPE_OPTION_LINK = "cli-reference/base-options.md#input-file-type"
DOCS_OUTPUT_MODEL_TYPE_OPTION_LINK = "cli-reference/model-customization.md#output-model-type"
DOCS_FORMATTER_BEHAVIOR_LINK = "formatter-behavior.md"
DOCS_PERFORMANCE_BENCHMARKS_LINK = "performance-benchmarks.md"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from datamodel_code_generator.enums import DataModelType, InputFileType  # noqa: E402
from datamodel_code_generator.preset import get_preset_names, render_presets  # noqa: E402

if TYPE_CHECKING:
    from datamodel_code_generator.preset_names import PresetName

PresetDocsFormat = Literal["markdown", "json"]

README_INPUT_TYPE_LABELS: dict[InputFileType, str] = {
    InputFileType.OpenAPI: "OpenAPI 3 (YAML/JSON)",
    InputFileType.AsyncAPI: "AsyncAPI (YAML/JSON)",
    InputFileType.JsonSchema: "JSON Schema",
    InputFileType.MCPTools: "MCP tool schemas",
    InputFileType.XMLSchema: "XML Schema (XSD)",
    InputFileType.Protobuf: "Protocol Buffers / gRPC (`.proto`)",
    InputFileType.Avro: "Apache Avro schema (AVSC)",
    InputFileType.Json: "JSON data",
    InputFileType.Yaml: "YAML data",
    InputFileType.Dict: "Python dictionary",
    InputFileType.CSV: "CSV data",
    InputFileType.GraphQL: "GraphQL schema",
}
README_EXTRA_INPUT_ITEMS = ("Python types (Pydantic, dataclass, TypedDict) via `--input-model`",)

README_OUTPUT_TYPE_LABELS: dict[DataModelType, str] = {
    DataModelType.PydanticV2BaseModel: "[pydantic v2](https://docs.pydantic.dev/) BaseModel",
    DataModelType.PydanticV2Dataclass: "[pydantic v2](https://docs.pydantic.dev/) dataclass",
    DataModelType.DataclassesDataclass: "[dataclasses](https://docs.python.org/3/library/dataclasses.html)",
    DataModelType.TypingTypedDict: "[TypedDict](https://docs.python.org/3/library/typing.html#typing.TypedDict)",
    DataModelType.MsgspecStruct: "[msgspec](https://github.com/jcrist/msgspec) Struct",
}


FORMATTER_SELECTION_GUIDE = """Choose a formatter to match your project and generation priorities:

- **Projects using Ruff:** use `--formatters ruff-check ruff-format` to keep generated code consistent with the
  project's formatting and lint policy. Install it with `pip install 'datamodel-code-generator[ruff]'`.
- **No Ruff, Black, or isort, or generation speed is the priority:** use `--formatters builtin` to avoid running
  external formatters on standard generated model modules.
- **Projects using Black/isort:** keep `--formatters black isort` to preserve the project's formatting and existing
  generated output.

The current default remains Black/isort, which are still required dependencies. Omitting formatter options continues
normal generation. The future builtin default is intended to reduce required installation dependencies and version
constraints; Ruff will still be recommended for projects that use Ruff. Formatters are never selected automatically
based on installed packages or Ruff configuration. The new `[black]` and `[isort]` extras prepare for later
optional installation; their ranges and environment markers match the current required dependencies.
Selecting only a formatter preserves your other generation settings; a preset also supplies model-generation options.
Explicit formatter selection does not pin formatter versions or guarantee byte-for-byte output stability."""


@dataclass(frozen=True, slots=True)
class GeneratedDoc:
    """Generated file content."""

    path: Path
    content: str


def build_docs(*, check: bool) -> int:
    """Generate or check preset documentation outputs."""
    preset_names_doc = GeneratedDoc(PRESET_NAMES_PATH, _render_preset_names_module())
    if check:
        if not _doc_is_current(preset_names_doc):
            _print_out_of_date((preset_names_doc.path,))
            return 1
    else:
        preset_names_doc.path.write_text(preset_names_doc.content, encoding="utf-8")

    docs = _generate_docs(preset_names_doc)
    if not check:
        for doc in docs:
            doc.path.write_text(doc.content, encoding="utf-8")
        return 0

    out_of_date = [doc.path for doc in docs if not _doc_is_current(doc)]
    if not out_of_date:
        return 0

    _print_out_of_date(tuple(out_of_date))
    return 1


def _doc_is_current(doc: GeneratedDoc) -> bool:
    return doc.path.exists() and doc.path.read_text(encoding="utf-8") == doc.content


def _print_out_of_date(paths: tuple[Path, ...]) -> None:
    for path in paths:
        print(f"Preset docs are out of date: {path.relative_to(ROOT)}", file=sys.stderr)
    print("Run 'python scripts/build_preset_docs.py' to update.", file=sys.stderr)


def _generate_docs(preset_names_doc: GeneratedDoc) -> tuple[GeneratedDoc, ...]:
    standard_preset_name = _get_latest_preset_name_by_prefix(
        "standard",
        target_python_version=QUICK_START_TARGET_PYTHON_VERSION,
    )
    practical_preset_name = _get_latest_preset_name_by_prefix(
        "practical",
        target_python_version=QUICK_START_TARGET_PYTHON_VERSION,
    )
    standard_model_output = _generate_quick_start_model(standard_preset_name)
    readme_content = README_PATH.read_text(encoding="utf-8")
    readme_content = _replace_marked_section(
        readme_content,
        QUICK_START_BEGIN_MARKER,
        QUICK_START_END_MARKER,
        _render_readme_quick_start(
            standard_preset_name,
            standard_model_output,
            practical_preset_name,
        ),
    )
    readme_content = _replace_marked_section(
        readme_content,
        README_SUPPORTED_INPUT_BEGIN_MARKER,
        README_SUPPORTED_INPUT_END_MARKER,
        _render_readme_supported_input(),
    )
    readme_content = _replace_marked_section(
        readme_content,
        README_SUPPORTED_OUTPUT_BEGIN_MARKER,
        README_SUPPORTED_OUTPUT_END_MARKER,
        _render_readme_supported_output(),
    )
    return (
        preset_names_doc,
        GeneratedDoc(DOCS_PATH, render_presets("markdown")),
        GeneratedDoc(README_PATH, readme_content),
        GeneratedDoc(
            DOCS_GETTING_STARTED_PATH,
            _replace_marked_section(
                DOCS_GETTING_STARTED_PATH.read_text(encoding="utf-8"),
                QUICK_START_BEGIN_MARKER,
                QUICK_START_END_MARKER,
                _render_docs_quick_start(
                    standard_preset_name,
                    standard_model_output,
                    practical_preset_name,
                ),
            ),
        ),
        GeneratedDoc(
            DOCS_FASTAPI_SERVER_PATH,
            _replace_marked_section(
                _replace_marked_section(
                    DOCS_FASTAPI_SERVER_PATH.read_text(encoding="utf-8"),
                    FASTAPI_QUICK_START_BEGIN_MARKER,
                    FASTAPI_QUICK_START_END_MARKER,
                    _render_fastapi_quick_start(standard_preset_name),
                ),
                FASTAPI_PYTHON_API_BEGIN_MARKER,
                FASTAPI_PYTHON_API_END_MARKER,
                _render_fastapi_python_api(standard_preset_name),
            ),
        ),
    )


def _get_latest_preset_name_by_prefix(prefix: str, *, target_python_version: str) -> PresetName:
    target = target_python_version.replace(".", "")
    prefix_with_separator = f"{prefix}-py{target}-"
    matching_names = tuple(name for name in get_preset_names() if name.startswith(prefix_with_separator))
    if not matching_names:  # pragma: no cover
        msg = f"No built-in preset starts with {prefix_with_separator!r}"
        raise RuntimeError(msg)
    return cast("PresetName", max(matching_names, key=_preset_version_sort_key))


def _preset_version_sort_key(name: str) -> tuple[str, str]:
    prefix, separator, version = name.rpartition("-")
    if separator and version.isdecimal():
        return prefix, version
    return name, ""  # pragma: no cover


def _render_preset_names_module() -> str:
    preset_names = get_preset_names()
    if len(preset_names) == 1:
        names_literal = f'("{preset_names[0]}",)'
        preset_name_type = f'Literal["{preset_names[0]}"]'
    else:
        names = "\n".join(f'    "{name}",' for name in preset_names)
        names_literal = f"(\n{names}\n)"
        literal_names = "\n".join(f'    "{name}",' for name in preset_names)
        preset_name_type = f"Literal[\n{literal_names}\n]"
    return f'''"""Generated built-in preset names for CLI choices.

Generated by scripts/build_preset_docs.py. Do not edit manually.
"""

from __future__ import annotations

from typing import Final, Literal, TypeAlias

PresetName: TypeAlias = {preset_name_type}
PRESET_NAMES: Final[tuple[PresetName, ...]] = {names_literal}
'''


def _replace_marked_section(markdown_text: str, begin_marker: str, end_marker: str, generated: str) -> str:
    if (start := markdown_text.find(begin_marker)) == -1:
        msg = f"Could not find generated section begin marker: {begin_marker}"
        raise ValueError(msg)
    content_start = start + len(begin_marker)
    if (end := markdown_text.find(end_marker, content_start)) == -1:
        msg = f"Could not find generated section end marker after {begin_marker}: {end_marker}"
        raise ValueError(msg)
    return markdown_text[:content_start] + "\n" + generated.rstrip() + "\n" + markdown_text[end:]


def _render_readme_supported_input() -> str:
    return _render_markdown_list(_readme_supported_input_items())


def _readme_supported_input_items() -> tuple[str, ...]:
    return (
        tuple(
            README_INPUT_TYPE_LABELS[input_type] for input_type in InputFileType if input_type is not InputFileType.Auto
        )
        + README_EXTRA_INPUT_ITEMS
    )


def _render_readme_supported_output() -> str:
    return _render_markdown_list(tuple(README_OUTPUT_TYPE_LABELS[model_type] for model_type in DataModelType))


def _render_readme_supported_sections() -> str:
    return f"{_render_readme_supported_input()}\n\n{_render_readme_supported_output()}\n"


def _render_markdown_list(items: tuple[str, ...]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _quick_start_args(preset_name: str) -> tuple[str, ...]:
    return (
        "--input",
        QUICK_START_SCHEMA_NAME,
        "--input-file-type",
        "jsonschema",
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--preset",
        preset_name,
        "--output",
        QUICK_START_OUTPUT_NAME,
    )


def _render_quick_start_command(preset_name: str) -> str:
    return _render_shell_command(("datamodel-codegen", *_quick_start_args(preset_name)))


def _render_shell_command(command: tuple[str, ...]) -> str:
    lines: list[str] = []
    index = 0
    while index < len(command):
        token = command[index]
        if index == 0:
            argument = token
            index += 1
        elif index + 1 < len(command) and token.startswith("-") and not command[index + 1].startswith("-"):
            argument = f"{token} {command[index + 1]}"
            index += 2
        else:
            argument = token
            index += 1

        indent = "" if not lines else "  "
        continuation = " \\" if index < len(command) else ""
        lines.append(f"{indent}{argument}{continuation}")
    return "\n".join(lines)


def _render_fastapi_quick_start(preset_name: str) -> str:
    return f"""```bash
datamodel-codegen \\
  --input openapi.yaml \\
  --input-file-type openapi \\
  --openapi-scopes schemas api \\
  --output-model-type pydantic_v2.BaseModel \\
  --preset {preset_name} \\
  --output models.py \\
  --generate-server fastapi \\
  --target-config fastapi.toml
```

The preset supplies the model options, as in the model [quick start](getting-started.md).
"""


def _render_fastapi_python_api(preset_name: str) -> str:
    return f"""```python
from pathlib import Path

from datamodel_code_generator import DataModelType, GenerateConfig, InputFileType, OpenAPIScope
from datamodel_code_generator.fastapi import FastAPIConfig, generate_fastapi

report = generate_fastapi(
    Path("openapi.yaml"),
    model_config=GenerateConfig(
        output=Path("models.py"),
        input_file_type=InputFileType.OpenAPI,
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
        output_model_type=DataModelType.PydanticV2BaseModel,
        preset="{preset_name}",
    ),
    config=FastAPIConfig(output=Path("server"), package="server", model_package="models"),
)
for record in report.written_files:
    print(record.path)
```
"""


def _generate_quick_start_model(preset_name: PresetName) -> str:
    from datamodel_code_generator import generate  # noqa: PLC0415
    from datamodel_code_generator.config import GenerateConfig  # noqa: PLC0415
    from datamodel_code_generator.enums import DataModelType, InputFileType  # noqa: PLC0415
    from datamodel_code_generator.format import Formatter  # noqa: PLC0415

    config = GenerateConfig(
        input_filename=QUICK_START_SCHEMA_NAME,
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        preset=preset_name,
        formatters=[Formatter.BLACK, Formatter.ISORT],
    )

    schema = QUICK_START_SCHEMA_PATH.read_text(encoding="utf-8")
    model_output = generate(input_=schema, config=config)
    if not isinstance(model_output, str):  # pragma: no cover
        msg = f"Expected quick-start generation to return a single module string, got {type(model_output).__name__}"
        raise TypeError(msg)
    return _normalize_quick_start_timestamp(model_output.rstrip(), preset_name)


def _normalize_quick_start_timestamp(model_output: str, preset_name: str) -> str:
    """Pin generated quick-start timestamps when the selected preset emits one."""
    timestamp = _preset_timestamp(preset_name)
    normalized = re.sub(
        r"^#   timestamp: .+$",
        f"#   timestamp: {timestamp}",
        model_output,
        count=1,
        flags=re.MULTILINE,
    )
    if normalized != model_output:
        return normalized
    return model_output


def _preset_timestamp(preset_name: str) -> str:
    _prefix, separator, version = preset_name.rpartition("-")
    if separator and len(version) == PRESET_VERSION_DATE_LENGTH and version.isdecimal():
        return f"{version[:4]}-{version[4:6]}-{version[6:8]}T00:00:00+00:00"
    msg = f"Preset name does not end with YYYYMMDD: {preset_name}"
    raise RuntimeError(msg)


def _render_readme_quick_start(
    preset_name: str,
    model_output: str,
    practical_preset_name: str,
) -> str:
    schema = QUICK_START_SCHEMA_PATH.read_text(encoding="utf-8").rstrip()
    command = _render_quick_start_command(preset_name)
    practical_preset_url = f"{README_PRESETS_LINK}#{practical_preset_name}"
    return f"""**Command**

```bash
{command}
```

This quick start uses `{preset_name}` as the modern Python {QUICK_START_TARGET_PYTHON_VERSION} baseline.
Preset names include the target Python version: `py312` means Python 3.12.

See [CLI Reference]({README_CLI_REFERENCE_LINK}) for all options. See [Presets]({README_PRESETS_LINK}),
[`--preset`]({README_PRESET_OPTION_LINK}), [`--input-file-type`]({README_INPUT_FILE_TYPE_OPTION_LINK}), and
[`--output-model-type`]({README_OUTPUT_MODEL_TYPE_OPTION_LINK}) for this command.

For more schema-aware output that preserves schema-authored names, reuses models, and embeds generated
documentation, use [`{practical_preset_name}`]({practical_preset_url}).

<details>
<summary>Input (<code>{QUICK_START_SCHEMA_NAME}</code>)</summary>

```json
{schema}
```

</details>

**Output (`{QUICK_START_OUTPUT_NAME}`)**

<!-- fmt: off -->

```python
{model_output}
```

<!-- fmt: on -->

### Choose a formatter

{FORMATTER_SELECTION_GUIDE}

Custom templates can emit Python outside the standard generated model patterns covered by `builtin`, so
custom-template output is not exhaustively validated. If `--formatters builtin` produces invalid or poorly formatted
output with a custom template, please open an issue with a small reproducer. See
[Formatter Behavior]({README_FORMATTER_BEHAVIOR_LINK}) for details.

See [Performance Benchmarks]({README_PERFORMANCE_BENCHMARKS_LINK}) for release benchmark data and interactive charts.
"""


def _render_docs_quick_start(
    preset_name: str,
    model_output: str,
    practical_preset_name: str,
) -> str:
    schema = QUICK_START_SCHEMA_PATH.read_text(encoding="utf-8").rstrip()
    command = _render_quick_start_command(preset_name)
    practical_preset_link = f"{DOCS_PRESETS_LINK}#{practical_preset_name}"
    return f"""### Command

```bash
{command}
```

This quick start uses `{preset_name}` as the modern Python {QUICK_START_TARGET_PYTHON_VERSION} baseline.
Preset names include the target Python version: `py312` means Python 3.12.

See [CLI Reference]({DOCS_CLI_REFERENCE_LINK}) for all options. See [Presets]({DOCS_PRESETS_LINK}),
[`--preset`]({DOCS_PRESET_OPTION_LINK}), [`--input-file-type`]({DOCS_INPUT_FILE_TYPE_OPTION_LINK}), and
[`--output-model-type`]({DOCS_OUTPUT_MODEL_TYPE_OPTION_LINK}) for this command.

For more schema-aware output that preserves schema-authored names, reuses models, and embeds generated
documentation, use [`{practical_preset_name}`]({practical_preset_link}).

<details>
<summary>Input (<code>{QUICK_START_SCHEMA_NAME}</code>)</summary>

```json
{schema}
```

</details>

### Output

<!-- fmt: off -->

```python title="{QUICK_START_OUTPUT_NAME}"
{model_output}
```

<!-- fmt: on -->

🎉 That's it! Your schema is now a fully-typed Python model.

### Choose a formatter

{FORMATTER_SELECTION_GUIDE}

Custom templates can emit Python outside the standard generated model patterns covered by `builtin`, so
custom-template output is not exhaustively validated. If `--formatters builtin` produces invalid or poorly formatted
output with a custom template, please open an issue with a small reproducer. See
[Formatter Behavior]({DOCS_FORMATTER_BEHAVIOR_LINK}) for details.

See [Performance Benchmarks]({DOCS_PERFORMANCE_BENCHMARKS_LINK}) for release benchmark data and interactive charts."""


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Build preset documentation")
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Check whether preset docs, preset-powered quick-start examples, and README support lists are up to date "
            "(docs/presets.md, docs/getting-started.md, and generated sections in README.md and the FastAPI "
            "server page)"
        ),
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default=None,
        help="Print presets in the selected format instead of writing docs",
    )
    return parser.parse_args()


def main() -> int:
    """Script entrypoint."""
    args = parse_args()
    if args.check and args.format:
        print("Error: --check cannot be used with --format", file=sys.stderr)
        return 2
    if args.format:
        match args.format:
            case "markdown" | "json":
                print(render_presets(args.format), end="")
            case _:  # pragma: no cover
                print(f"Error: unsupported format {args.format!r}", file=sys.stderr)
                return 2
        return 0
    return build_docs(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
