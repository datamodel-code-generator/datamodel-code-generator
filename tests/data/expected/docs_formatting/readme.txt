An explanation before the generated example:

```python
name = "before"
```

**Command**

```bash
datamodel-codegen \
  --input schema.json \
  --input-file-type jsonschema \
  --output-model-type pydantic_v2.BaseModel \
  --preset example \
  --output model.py
```

This quick start uses `example` as the modern Python 3.12 baseline.
Preset names include the target Python version: `py312` means Python 3.12.

See [CLI Reference](https://datamodel-code-generator.koxudaxi.dev/cli-reference/) for all options. See [Presets](https://datamodel-code-generator.koxudaxi.dev/presets/),
[`--preset`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/base-options/#preset), [`--input-file-type`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/base-options/#input-file-type), and
[`--output-model-type`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/model-customization/#output-model-type) for this command.

For more schema-aware output that preserves schema-authored names, reuses models, and embeds generated
documentation, use [`practical`](https://datamodel-code-generator.koxudaxi.dev/presets/#practical).

<details>
<summary>Input (<code>schema.json</code>)</summary>

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Pet",
  "type": "object",
  "required": ["name"],
  "properties": {
    "name": {
      "type": "string",
      "description": "The pet's name"
    },
    "species": {
      "type": "string",
      "enum": ["dog", "cat", "bird", "fish"],
      "default": "dog"
    },
    "age": {
      "type": "integer",
      "minimum": 0,
      "description": "Age in years"
    },
    "vaccinated": {
      "type": "boolean",
      "default": false
    }
  }
}
```

</details>

**Output (`model.py`)**

<!-- fmt: off -->

```python
from pydantic import BaseModel, Field


class Pet(BaseModel):
    name: str = Field(
        'dog',
        description='Pet name',
    )
```

<!-- fmt: on -->

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
based on installed packages or Ruff configuration. The new `[black]` and `[isort]` extras prepare for later
optional installation; their ranges and environment markers match the current required dependencies.
Selecting only a formatter preserves your other generation settings; a preset also supplies model-generation options.
Explicit formatter selection does not pin formatter versions or guarantee byte-for-byte output stability.

Custom templates can emit Python outside the standard generated model patterns covered by `builtin`, so
custom-template output is not exhaustively validated. If `--formatters builtin` produces invalid or poorly formatted
output with a custom template, please open an issue with a small reproducer. See
[Formatter Behavior](https://datamodel-code-generator.koxudaxi.dev/formatter-behavior/) for details.

See [Performance Benchmarks](https://datamodel-code-generator.koxudaxi.dev/performance-benchmarks/) for release benchmark data and interactive charts.


After the example:

```python
name = "after"
```
