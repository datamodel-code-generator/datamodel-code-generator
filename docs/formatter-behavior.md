<!-- related-cli-options: --formatters -->

# Formatter behavior

`datamodel-codegen` formats generated Python code after model generation. The default formatter list is currently `black` and `isort`.

!!! warning "Experimental"
    The built-in formatter (`--formatters builtin`) is experimental and may change as generated-output coverage is
    expanded.

This page is the reference for the built-in formatter's exact scope and precedence. For a formatter selection guide,
see [Code Formatting](formatting.md).

This default will change in a future release. The default formatter will become `builtin`, and external formatter
dependencies will become opt-in so that generated output can be produced without installing formatter packages.

## Formatter selection

Use `--formatters` to select the formatter pipeline.

```bash
datamodel-codegen \
    --input schema.yaml \
    --output model.py \
    --formatters builtin
```

Available formatter names are:

| Name | Behavior |
| ---- | -------- |
| `builtin` | Internal formatter for generated model modules. No external package is required. |
| `black` | Runs Black against generated code. |
| `isort` | Runs isort against generated imports. |
| `ruff-check` | Runs `ruff check --fix`. |
| `ruff-format` | Runs `ruff format`. |

`--formatters` selects which formatter integrations are enabled. It does not reorder the built-in integrations:
single-file output is processed as isort, built-in formatter, Black, then Ruff check/format when those formatters are
selected.
The built-in formatter is an alternative to external formatting, not a pre-formatter.
If `builtin` is passed together with `black`, `isort`, `ruff-check`, or `ruff-format`, `builtin` is ignored.

### Choose a formatter

Choose a formatter to match your project and generation priorities:

- **Projects using Ruff:** use `--formatters ruff-check ruff-format` to keep generated code consistent with the
  project's formatting and lint policy. Install it with `pip install 'datamodel-code-generator[ruff]'`.
- **No Ruff, Black, or isort, or generation speed is the priority:** use `--formatters builtin` to avoid running
  external formatters on standard generated model modules.
- **Projects using Black/isort:** keep `--formatters black isort` to preserve the project's formatting and existing
  generated output.

The current default remains Black/isort, which are still required dependencies. Omitting formatter options continues
normal generation. The future builtin default is intended to reduce required installation dependencies and version
constraints; Ruff will still be recommended for projects that use Ruff. Formatters are never selected automatically
based on installed packages or Ruff configuration.

Custom templates can emit Python outside the standard generated model patterns covered by `builtin`, so
custom-template output is not exhaustively validated. If `--formatters builtin` produces invalid or poorly formatted
output with a custom template, please open an issue with a small reproducer.

## Built-in formatter scope

The built-in formatter is intentionally small. It is not a general Python formatter and is not expected to produce byte-for-byte identical output to the latest Black, isort, or Ruff for arbitrary Python files.

It covers the generated-code patterns that matter for model modules:

- Removes trailing whitespace and writes a final newline.
- Parses the module with `ast`.
- Sorts only the leading import block. It stops at the first non-import statement.
- Splits `import a, b` into separate `import` lines.
- Groups imports in this order: `__future__`, standard library, third-party, relative imports.
- Sorts `from ... import ...` names and merges imports from the same module.
- Wraps long `from ... import ...` statements with parentheses when they exceed the configured line length.
- Leaves commented import lines in place instead of dropping comments while sorting imports.
- Sorts imports inside a top-level `if TYPE_CHECKING:` block when that block contains only imports.
- Keeps two blank lines before a top-level `class`, `def`, or `async def` after the import block.
- Wraps generated model statements for common long `Field(...)`, `Annotated[...]`, and `ConfigDict(...)` lines.
- Normalizes simple generated string quotes to double quotes when `--use-double-quotes` is passed or `[tool.black].skip-string-normalization = false`.

If the generated code cannot be parsed as Python, the built-in formatter only applies whitespace cleanup and returns the code.

The built-in formatter does not currently format:

- General expressions inside classes or functions.
- List, dict, tuple, or arbitrary call argument layout outside the generated model patterns listed above.
- General quote style rewrites outside simple generated strings.
- String wrapping outside supported generated call arguments.
- Comment placement beyond preserving commented import lines.
- Import section rules beyond the simple groups listed above.

This scope matters when using `--custom-template-dir`. Custom templates can emit arbitrary Python code outside the
generated model patterns listed above, so custom-template output is not exhaustively validated with the built-in
formatter. If `--formatters builtin` produces invalid or poorly formatted output with a custom template, please open an
issue with a small reproducer.

For exact Black, isort, or Ruff behavior, continue to pass those formatters explicitly.
The built-in formatter is not run before external formatters.

## Line length

The built-in formatter uses line length for wrapping `from ... import ...` statements and the generated model
statements listed above.

Set it in `pyproject.toml`:

```toml
[tool.datamodel-codegen]
formatters = ["builtin"]
builtin-format-line-length = 100
```

The built-in formatter uses line length in this order:

1. API `builtin_format_line_length`
2. `[tool.datamodel-codegen].builtin-format-line-length` or `[tool.datamodel-codegen].builtin_format_line_length`
3. `[tool.ruff].line-length`
4. `[tool.black].line-length`
5. `[tool.isort].line_length`
6. `88`

The fallback formatter settings only read configuration values. They do not require Ruff, Black, or isort to be
installed.

## String normalization

The built-in formatter keeps single quotes by default. It normalizes simple generated string literals to double quotes when either condition is true:

1. `--use-double-quotes` is passed.
2. `[tool.black].skip-string-normalization = false` is set in `pyproject.toml`.

The built-in formatter only reads this Black setting when `builtin` is selected without an external formatter.

## API usage

Library users can pass the same setting through `generate()` or `GenerateConfig`.

```python
from pathlib import Path

from datamodel_code_generator import generate
from datamodel_code_generator.format import Formatter

generate(
    input_=Path("schema.yaml"),
    output=Path("model.py"),
    formatters=[Formatter.BUILTIN],
    builtin_format_line_length=100,
)
```

## Migration notes

To keep current output behavior, set the external formatters explicitly:

```bash
datamodel-codegen \
    --input schema.yaml \
    --output model.py \
    --formatters black isort
```

To test the dependency-free path, use:

```bash
datamodel-codegen \
    --input schema.yaml \
    --output model.py \
    --formatters builtin
```

Generated output may differ from Black or isort output in places outside the built-in formatter scope. Treat those differences as expected unless they change Python semantics or produce invalid code.

## Dependency migration

See [Dependency migration notices](dependency-migration.md) for Black/isort extras, separate optional-installation
and old-version warnings, unchanged current requirements, and the distinction between DCG runtime Pydantic and
generated-code compatibility. Old presets without formatters retain the implicit-default warning.
