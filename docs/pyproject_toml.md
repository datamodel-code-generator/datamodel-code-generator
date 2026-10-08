<!-- related-cli-options: --all-jobs, --ignore-pyproject, --generate-pyproject-config, --generate-cli-command, --job, --profile, --preset, --watch -->

# ⚙️ pyproject.toml Configuration

datamodel-code-generator can be configured using `pyproject.toml`. The tool automatically searches for `pyproject.toml` in the current directory and parent directories (stopping at the git repository root).

HTTP client selection uses the same values as `--http-backend`. For example,
the experimental HTTPX2 pair can be required explicitly:

```toml
[tool.datamodel-codegen]
http-backend = "httpx2"
```

The default is `"auto"`, which prefers stable HTTPX. See
[HTTP backend selection](faq.md#http-backend-selection).

## 🚀 Basic Usage

```toml
[tool.datamodel-codegen]
input = "schema.yaml"
output = "models.py"
target-python-version = "3.11"
snake-case-field = true
field-constraints = true
```

All CLI options can be used in `pyproject.toml` by converting them to kebab-case (e.g., `--snake-case-field` becomes `snake-case-field`).

## Formatter settings

Formatter selection is configured with the same keys as the CLI:

```toml
[tool.datamodel-codegen]
formatters = ["builtin"]
builtin-format-line-length = 100
```

`builtin-format-line-length` is used only by the built-in formatter. It controls wrapping for `from ... import ...` statements and generated model statements.

If it is not set, the built-in formatter reads existing formatter settings in this order:

1. `[tool.ruff].line-length`
2. `[tool.black].line-length`
3. `[tool.isort].line_length`
4. `88`

See [Formatter Behavior](formatter-behavior.md) for the full built-in formatter scope.

## 📋 Named Profiles

You can define multiple named profiles for different use cases within a single project:

```toml
[tool.datamodel-codegen]
target-python-version = "3.10"

[tool.datamodel-codegen.profiles.strict]
snake-case-field = true
use-annotated = true

[tool.datamodel-codegen.profiles.py311]
target-python-version = "3.11"
```

Base settings in `[tool.datamodel-codegen]` are used when no profile is specified, and also serve as defaults for profiles.

Use a profile with the `--profile` option:

```bash
datamodel-codegen --input schemas/api.yaml --output src/models/api.py --profile strict
datamodel-codegen --input schemas/db.json --output src/models/db.py --profile py311
```

## 🏃 Named Jobs (Experimental)

!!! warning "Experimental"

    Named batch jobs are experimental; their configuration schema, batch output,
    and transactional/watch execution contracts may change.

Profiles are reusable configuration fragments. A job is a runnable generation
that supplies its own `input` and `output` and can select one profile.

```toml
[tool.datamodel-codegen.jobs.api]
profile = "strict"
input = "schemas/api.yaml"
output = "src/models/api.py"

[tool.datamodel-codegen.jobs.database]
profile = "py311"
input = "schemas/db.json"
output = "src/models/db.py"
input-file-type = "jsonschema"
```

Run selected jobs or every job:

```bash
datamodel-codegen --job api
datamodel-codegen --job api --job database --check
datamodel-codegen --all-jobs
```

Jobs always run sequentially in their TOML declaration order. Before generation,
all selected jobs are validated: each must have an input and output, and output
or model-metadata paths cannot overlap. `--job` and `--all-jobs` are mutually
exclusive. Define job-specific input and output in TOML instead of combining
job selection with `--input`, `--url`, `--input-model`, `--output`, `--profile`,
`--diff-against`, or job-specific watch settings. Input comparison is a
single-profile/input operation and is rejected if it is inherited by any
selected job. `watch` and `watch-delay` may be set on the CLI or in the base
`[tool.datamodel-codegen]` table, but cannot be set in a job table or in the
selected profile. Batch watch observes the union of every selected job's local
dependencies plus `pyproject.toml`. Each event reloads the project, replans the
selection, and transactionally reruns the whole batch; it does not partially
rebuild individual jobs. A failed cycle keeps the last published outputs and
continues watching both prior and newly discovered dependencies for recovery.

A job whose settings select `generate-server` also generates the FastAPI server
package, and such jobs can share one models `output`; see [Batch jobs](fastapi-server.md#batch-jobs).

## 🎯 Configuration Priority

Settings are applied in the following priority order (highest to lowest):

1. **🖥️ CLI arguments** - Always take precedence
2. **🏃 Job settings** - From `[tool.datamodel-codegen.jobs.<name>]`
3. **📋 Profile settings** - From `[tool.datamodel-codegen.profiles.<name>]`
4. **⚙️ Base settings** - From `[tool.datamodel-codegen]`
5. **🔧 Default values** - Built-in defaults

## 🔀 Merge Rules

When using profiles, settings are merged using **shallow merge**:

- Profile values **completely replace** base values (no deep merging)
- Settings not specified in the profile are inherited from the base configuration
- Lists and dictionaries are replaced entirely, not merged

### 📝 Example

```toml
[tool.datamodel-codegen]
strict-types = ["str", "int"]
http-headers = ["Authorization: Bearer token"]

[tool.datamodel-codegen.profiles.api]
strict-types = ["bytes"]
```

When using `--profile api`:

- `strict-types` becomes `["bytes"]` (completely replaces base, not merged)
- `http-headers` is inherited from base as `["Authorization: Bearer token"]`

## 🚫 Ignoring pyproject.toml

To ignore all `pyproject.toml` configuration and use only CLI arguments:

```bash
datamodel-codegen --ignore-pyproject --input schema.yaml --output models.py
```

## 🔧 Generating Configuration

Generate a `pyproject.toml` configuration section from CLI arguments:

```bash
datamodel-codegen --input schema.yaml --output models.py --snake-case-field --generate-pyproject-config
```

**✨ Output:**

```toml
[tool.datamodel-codegen]
input = "schema.yaml"
output = "models.py"
snake-case-field = true
```

Generate CLI command from existing `pyproject.toml`:

```bash
datamodel-codegen --generate-cli-command
```

With a specific profile:

```bash
datamodel-codegen --profile api --generate-cli-command
```

---

## 📖 See Also

- 🧰 [Presets](presets.md) - Recommended immutable option bundles for modern output
- 🖥️ [CLI Reference: `--ignore-pyproject`](cli-reference/general-options.md#ignore-pyproject) - Ignore pyproject.toml configuration
- 🔧 [CLI Reference: `--generate-pyproject-config`](cli-reference/general-options.md#generate-pyproject-config) - Generate pyproject.toml from CLI arguments
- 🖥️ [CLI Reference: `--generate-cli-command`](cli-reference/general-options.md#generate-cli-command) - Generate CLI command from pyproject.toml
