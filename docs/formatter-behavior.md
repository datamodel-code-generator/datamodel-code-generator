<!-- related-cli-options: --formatters -->

# Formatter behavior

`datamodel-codegen` formats generated Python code after model generation. The default formatter list is currently `black` and `isort`.

!!! note "Stability"
    The built-in formatter (`--formatters builtin`) matches Black and isort for standard generated code, with the
    differences listed below. From now on its output changes only to move closer to Black's output, and every such
    change is announced in the release notes.

This page is the reference for the built-in formatter's exact scope and precedence. For a formatter selection guide,
see [Code Formatting](formatting.md).

This default changes no earlier than three months after 0.92.0 is released, after a transition period announced
in the release notes. The default formatter will become `builtin`, and external formatter dependencies will become opt-in so that
generated output can be produced without installing formatter packages. See the
[migration guide](dependency-migration.md#how-to-migrate).

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

The built-in formatter is intentionally small. It is not a general Python formatter. For the code generated from
the standard templates it writes what the current Black release writes in its stable style, with isort's default
ordering, so that it can become the default formatter. It reads only the settings in
[Line length](#line-length), [String normalization](#string-normalization), `[tool.isort].known_first_party` and
`--wrap-string-literal`.
It ignores other isort options (such as `profile` and `force_single_line`) and Black's `target-version`, `preview`
and `skip-magic-trailing-comma`, and it does not follow the layout of older Black releases. Output differs from
Black/isort where those settings or releases matter, and in the cases listed under "Known differences".

It covers the generated-code patterns that matter for model modules:

- Removes trailing whitespace outside string literals and writes a final newline.
- Parses the module with `ast`.
- Sorts only the leading import block. It stops at the first non-import statement.
- Splits `import a, b` into separate `import` lines.
- Groups imports in this order: `__future__`, standard library, third-party, relative imports.
- Sorts `from ... import ...` names and merges imports from the same module, relative ones too; aliased names get
  their own lines in name order, like isort.
- Wraps long `from ... import ...` statements with parentheses when they exceed the configured line length.
- Leaves commented import lines in place instead of dropping comments while sorting imports.
- Sorts each run of imports inside function, class and compound statement bodies (such as `if TYPE_CHECKING:`)
  the same way, wraps long ones at the width their indent leaves and puts a blank line after a run, like isort and
  Black. A comment line between two imports ends a run.
- Lays out module-level and class-level simple statements (assignments, annotated assignments, expression
  statements, `type` statements), class and function headers and decorators the way Black splits long lines: the
  right-hand split, splits at commas and operators, optional parentheses around annotations and assigned values,
  and magic trailing commas.
- Lays out docstrings the way Black does: re-indented, trailing whitespace removed and padded before the closing
  quotes when needed. A docstring with a backslash at a line end stays as it is, as Black leaves it.
- Keeps two blank lines around top-level `class`, `def`, or `async def` definitions and removes blank lines after
  a decorator.
- Chooses string quotes as Black does when `--use-double-quotes` is passed or `[tool.black].skip-string-normalization = false`: double quotes unless they need more escapes, without the escapes the chosen quotes make unneeded.

If the generated code cannot be parsed as Python, the built-in formatter only applies whitespace cleanup and returns the code.

Known differences from Black and isort:

- Statements inside function bodies and compound statements (`if`, `for`, `with`, `try`) are not laid out, except
  long `and`/`or` groups in generated methods.
- A statement that holds a comment, a multi-line string, a backslash continuation, a slice, a `**` power operator
  or a `lambda` with parameters is left as it is.
- Spacing inside a line that fits is kept as written (Black also normalizes spaces around operators and removes
  redundant parentheses: doubled ones, empty ones after a class name and those around a walrus in an `if` header).
- A conditional expression (`a if b else c`) that is split inside brackets or inside another conditional
  expression gets no parentheses of its own; Black wraps it in them.
- Module-level `del`, `assert`, `raise`, `global` and similar statements, and definitions with PEP 695 type
  parameters (`def f[T]`), are not laid out.
- Quote normalization leaves triple-quoted strings and string prefixes as written: Black also drops a `u`/`U` prefix
  (`u'x'` -> `"x"`) and lowercases `F`/`B` (`F'x'` -> `f"x"`).
- `--wrap-string-literal` lays out strings as Black's preview string processing does (the `string_processing` and
  `wrap_long_dict_values_in_parens` features that the option turns on for Black) in the statements listed above,
  except a string after a conditional expression's `else`, which is not put on its own line. With
  `--use-double-quotes` too, implicitly joined strings written with different quotes can be split at other spaces.
- Comment placement beyond preserving commented import lines.
- Import section rules beyond the simple groups listed above.

This scope matters when using `--custom-template-dir`. Custom templates can emit arbitrary Python code outside the
generated model patterns listed above, so custom-template output is not exhaustively validated with the built-in
formatter. If `--formatters builtin` produces invalid or poorly formatted output with a custom template, please open an
issue with a small reproducer.

For exact Black, isort, or Ruff behavior, continue to pass those formatters explicitly.
The built-in formatter is not run before external formatters.

## Line length

The built-in formatter uses line length for wrapping `from ... import ...` statements, docstrings and the
statements listed above. It measures a line in display columns, as Black does: a wide East Asian character counts as
two.

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

The built-in formatter keeps single quotes by default. It chooses the quotes of string literals as Black does when either condition is true:

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
