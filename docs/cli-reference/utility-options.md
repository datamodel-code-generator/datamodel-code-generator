# 📝 Utility Options

## 📋 Options

| Option | Description |
|--------|-------------|
| [`--all-jobs`](#all-jobs) | Run every named generation job from pyproject.toml (experimental) |
| [`--debug`](#debug) | Show debug messages during code generation |
| [`--generate-prompt`](#generate-prompt) | Generate a prompt for consulting LLMs about CLI options |
| [`--help`](#help) | Show help message and exit |
| [`--install-skill`](#install-skill) | Install the bundled Agent Skill (experimental) |
| [`--job`](#job) | Run a named generation job from pyproject.toml (experimental) |
| [`--list-deprecations`](#list-deprecations) | List registered deprecations and scheduled breaking changes |
| [`--list-experimental`](#list-experimental) | List registered experimental features |
| [`--no-color`](#no-color) | Disable colorized output |
| [`--output-format`](#output-format) | Choose the command output format |
| [`--output-format-json-schema`](#output-format-json-schema) | Output JSON Schema for structured command output or JSON configuration |
| [`--overwrite-skill`](#overwrite-skill) | Replace an existing Agent Skill installation |
| [`--profile`](#profile) | Use a named profile from pyproject.toml |
| [`--skill-scope`](#skill-scope) | Choose an Agent Skill installation scope |
| [`--version`](#version) | Show program version and exit |

---

## `--all-jobs` {#all-jobs}

Run every named generation job from `[tool.datamodel-codegen.jobs]` in declaration
order (experimental).

!!! warning "Experimental"

    Named batch jobs are experimental; their configuration schema, batch output,
    and transactional/watch execution contracts may change.

**Related:** [`--job`](#job), [`--diff-against`](general-options.md#diff-against),
[pyproject.toml Configuration](../pyproject_toml.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --all-jobs
    datamodel-codegen --all-jobs --check
    datamodel-codegen --all-jobs --output-format json
    datamodel-codegen --all-jobs --watch
    ```

All selected jobs are validated before generation starts. Jobs that write to
stdout, or whose output or model-metadata paths overlap, are rejected before
any generated file is written. With `--output-format json`, one `batch`
payload contains the result of every job.

`--diff-against` is intentionally unavailable for every named-job execution,
including a single selected job. Keeping one contract across `--job` and
`--all-jobs` avoids ambiguous partial support when the selection grows. Run the
comparison for one profile or input instead.

With `--watch`, changes to `pyproject.toml` replan the complete job membership.
All selected local dependencies are watched as one graph, and every event reruns
and publishes the whole batch transactionally rather than rebuilding only one
job.

---

## `--debug` {#debug}

Show debug messages during code generation.

Enables verbose debug output to help troubleshoot issues with schema parsing
or code generation. Requires the `debug` extra to be installed.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --debug # (1)!
    ```

    1. :material-arrow-left: `--debug` - the option documented here

!!! warning "Requires extra dependency"

    The debug feature requires the `debug` extra:

    ```bash
    pip install 'datamodel-code-generator[debug]'
    ```

---

## `--generate-prompt` {#generate-prompt}

Generate a prompt for consulting LLMs about CLI options.

Outputs a formatted prompt containing your current options, all available
options by category, and full help text. Pipe to CLI LLM tools or copy
to clipboard for web-based LLM chats.

Use `--output-format json` when an LLM agent or tool should consume structured
option metadata instead of Markdown.
Use `--output-format-json-schema generate-prompt` when the agent needs the JSON
Schema for that structured payload, such as when defining a tool contract.

**See also:** [LLM Integration](../llm-integration.md) for detailed usage examples

!!! note "For LLM agents"

    See [LLM Integration: If You Are an LLM Agent](../llm-integration.md#if-you-are-an-llm-agent)
    for workflow guidance.

!!! tip "Usage"

    ```bash
    datamodel-codegen --generate-prompt # (1)!
    datamodel-codegen --generate-prompt "How do I generate strict types?" # (2)!
    datamodel-codegen --generate-prompt --output-format json # (3)!
    datamodel-codegen --output-format-json-schema generate-prompt # (4)!
    ```

    1. :material-arrow-left: `--generate-prompt` - generate prompt without a question
    2. :material-arrow-left: Include a specific question in the prompt
    3. :material-arrow-left: Emit structured JSON for LLM/tool ingestion
    4. :material-arrow-left: Emit JSON Schema for structured prompt JSON

??? example "Quick Examples"

    **Pipe to CLI tools:**
    ```bash
    datamodel-codegen --generate-prompt | claude -p    # Claude Code
    datamodel-codegen --generate-prompt | codex exec   # OpenAI Codex
    datamodel-codegen --generate-prompt --output-format json | codex exec
    ```

    **Copy to clipboard:**
    ```bash
    datamodel-codegen --generate-prompt | pbcopy      # macOS
    datamodel-codegen --generate-prompt | xclip -selection clipboard  # Linux
    datamodel-codegen --generate-prompt | clip.exe    # WSL2
    ```

    **Ask about an existing OpenAPI command:**
    ```bash
    datamodel-codegen \
        --input openapi.yaml \
        --input-file-type openapi \
        --output models.py \
        --output-model-type pydantic_v2.BaseModel \
        --target-python-version 3.12 \
        --generate-prompt "Find the minimal options for strict API response models." \
        | claude -p
    ```

    **Review a command with current options:**
    ```bash
    datamodel-codegen \
        --input schema.json \
        --output models.py \
        --output-model-type pydantic_v2.BaseModel \
        --generate-prompt "Review this command for stable generated output in CI." \
        | claude -p
    ```

---

## `--help` {#help}

Show help message and exit.

Displays all available command-line options with their descriptions and default values.

**Aliases:** `-h`

!!! tip "Usage"

    ```bash
    datamodel-codegen --help # (1)!
    ```

    1. :material-arrow-left: `--help` - the option documented here

??? example "Output"

    ```text
    usage: datamodel-codegen [-h] [--input INPUT] [--url URL] ...

    Generate Python data models from schema files.

    options:
      -h, --help            show this help message and exit
      --input INPUT         Input file path (default: stdin)
      ...
    ```

---

## `--install-skill` {#install-skill}

Install the bundled experimental `datamodel-code-generator` Agent Skill for Codex or Claude Code, then exit.

The default `project` scope installs the skill in the current project's
`.agents/skills/` directory for Codex or `.claude/skills/` directory for Claude
Code. Use `--skill-scope user` to install the same skill below your home
directory instead.

!!! tip "Usage"

    ```bash
    datamodel-codegen --install-skill codex # (1)!
    datamodel-codegen --install-skill claude-code --skill-scope user # (2)!
    datamodel-codegen --install-skill codex --overwrite-skill # (3)!
    ```

    1. :material-arrow-left: Install for Codex in `.agents/skills/` for the current project
    2. :material-arrow-left: Install for Claude Code in `~/.claude/skills/`
    3. :material-arrow-left: Replace an existing regular skill directory

For safety, an existing skill directory is left untouched unless
`--overwrite-skill` is supplied. Symlinks and non-directory targets are never
replaced.

---

## `--job` {#job}

Run one or more named generation jobs declared in `pyproject.toml` (experimental). Jobs are
executed sequentially in TOML declaration order, even when `--job` is supplied
in a different order.

!!! warning "Experimental"

    Named batch jobs are experimental; their configuration schema, batch output,
    and transactional/watch execution contracts may change.

**Related:** [pyproject.toml Configuration](../pyproject_toml.md),
[`--all-jobs`](#all-jobs), [`--diff-against`](general-options.md#diff-against)

!!! tip "Usage"

    ```bash
    datamodel-codegen --job api
    datamodel-codegen --job api --job events --check
    ```

??? example "Configuration (pyproject.toml)"

    ```toml
    [tool.datamodel-codegen.profiles.strict]
    use-annotated = true

    [tool.datamodel-codegen.jobs.api]
    profile = "strict"
    input = "schemas/openapi.yaml"
    output = "src/models/api.py"

    [tool.datamodel-codegen.jobs.events]
    input = "schemas/events.json"
    output = "src/models/events.py"
    ```

Jobs require their own `input` and `output`. A job can select one reusable
profile using `profile = "name"`. Settings are resolved as base configuration,
job profile, job settings, then safe batch-wide CLI overrides. `--input`,
`--url`, `--input-model`, `--output`, and `--profile` cannot be combined with
job selection. `--diff-against` also cannot be combined with named jobs: compare
one profile or input at a time. With `--watch`, one outer scheduler watches the
selected jobs' combined dependency graph and `pyproject.toml`. Every event replans and
transactionally reruns the complete selection; failures retain the published
outputs and continue watching for recovery.

---

## `--list-deprecations` {#list-deprecations}

List registered deprecations and scheduled breaking changes, then exit.

The option reads from the central deprecation registry used by runtime warnings,
generated documentation, and release-note snippets.

    datamodel-codegen --list-deprecations
    datamodel-codegen --list-deprecations json
    datamodel-codegen --list-deprecations markdown

---

## `--list-experimental` {#list-experimental}

List registered experimental features, then exit.

The optional format argument can be `table`, `json`, or `markdown`. The default is `table`.

The option reads from the central experimental feature registry used by
generated documentation and release-note snippets.

    datamodel-codegen --list-experimental
    datamodel-codegen --list-experimental json
    datamodel-codegen --list-experimental markdown

---

## `--no-color` {#no-color}

Disable colorized output.

By default, datamodel-codegen uses colored output for better readability.
Use this option to disable colors, which is useful for CI/CD pipelines
or when redirecting output to files.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --no-color # (1)!
    ```

    1. :material-arrow-left: `--no-color` - the option documented here

!!! note "Environment variable"

    You can also disable colors by setting the `NO_COLOR` environment variable:

    ```bash
    NO_COLOR=1 datamodel-codegen --input schema.json
    ```

---

## `--output-format` {#output-format}

Choose the command output format.

The default output format is `text`. Use `json` when another program or LLM
agent should inspect structured output.

For backward compatibility, text stdout concatenates generated modules and does
not preserve their paths. Use `--fail-on-multi-module-stdout` to reject that
case, `--output <directory>` to preserve files, or `--output-format json` for
structured stdout. The fail option takes precedence over automatic repair of an
unusable modular stdout result, so an explicitly requested guard always rejects
the modular result instead of coalescing it.

In normal generation mode, `--output-format json` wraps generated modules in a
structured payload on stdout. If `--output` is also supplied, files are still
written to disk and the JSON payload mirrors the generated files. `--check`
also supports JSON output for difference reports. `--watch` keeps its existing
text output contract and does not support `--output-format json`.

Structured JSON is emitted on stdout for successful commands and for `--check`
difference reports. CLI usage errors, validation errors, and runtime generation
errors continue to use text on stderr with a non-zero exit code.

Generation JSON includes the normalized requested output path in top-level
`output` when `--output` is supplied, or `null` for stdout generation.
`files[].path` is the output file name for single-file disk output, and the
path relative to the output directory for directory output. For stdout-only
single-file generation it is `null`, and for multi-module stdout generation it
is the generated module path.

Use `--output-format json` with `--generate-prompt` to emit structured option
metadata instead of Markdown. Use `--output-format-json-schema` when an LLM
agent or tool needs the schema for a JSON payload.

Schema targets are intentionally scoped. `generate-prompt` emits the
`PromptPayload` schema for `--generate-prompt --output-format json`.
`generation` emits only the `GenerationPayload` schema for generated-file JSON.
`model-metadata` emits the schema for files written by `--emit-model-metadata`.
`structured-output` emits the broader `StructuredOutputPayload` schema, a union
covering `GenerationPayload`, `PromptPayload`, `CommandOutputPayload`, and
`CheckOutputPayload`. Structured payloads use `kind` as the discriminator.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-format text # (1)!
    datamodel-codegen --input schema.json --output-format json # (2)!
    datamodel-codegen --generate-prompt --output-format json # (3)!
    datamodel-codegen --output-format-json-schema generation # (4)!
    datamodel-codegen --output-format-json-schema generate-prompt # (5)!
    datamodel-codegen --output-format-json-schema model-metadata # (6)!
    datamodel-codegen --output-format-json-schema structured-output # (7)!
    ```

    1. :material-arrow-left: Emit the default generated Python text
    2. :material-arrow-left: Emit structured JSON containing generated files
    3. :material-arrow-left: Emit structured JSON with current options and argparse metadata
    4. :material-arrow-left: Emit JSON Schema for generated-file JSON output
    5. :material-arrow-left: Emit JSON Schema for structured prompt JSON
    6. :material-arrow-left: Emit JSON Schema for generated model metadata JSON
    7. :material-arrow-left: Emit JSON Schema for any structured command JSON output

??? example "Generation JSON output"

    ```json
    {
      "version": 1,
      "format": "json",
      "kind": "generation",
      "output": null,
      "files": [
        {
          "path": null,
          "content": "# generated by datamodel-codegen:\n..."
        }
      ]
    }
    ```

??? example "Prompt JSON output"

    ```bash
    datamodel-codegen \
        --input schema.json \
        --output-model-type pydantic_v2.BaseModel \
        --generate-prompt "Choose strict model options." \
        --output-format json
    ```

---

## `--output-format-json-schema` {#output-format-json-schema}

Output JSON Schema for a JSON output format and exit.

Use this when an LLM agent, tool call definition, or validation layer needs the
contract before consuming JSON output. The schema is emitted separately from the
JSON payload so tools can fetch the contract once and validate later command
output independently.

Currently supported schema targets:

- `generate-prompt`: schema for `--generate-prompt --output-format json`
- `generation`: schema for normal generation with `--output-format json`
- `model-metadata`: schema for files emitted by `--emit-model-metadata`
- `structured-output`: tagged union schema for all structured command outputs,
  discriminated by `kind`
- `config`: schema for JSON-valued configuration options

!!! tip "Usage"

    ```bash
    datamodel-codegen --output-format-json-schema generate-prompt # (1)!
    datamodel-codegen --output-format-json-schema generation # (2)!
    datamodel-codegen --output-format-json-schema model-metadata # (3)!
    datamodel-codegen --output-format-json-schema structured-output # (4)!
    datamodel-codegen --output-format-json-schema config # (5)!
    datamodel-codegen --generate-prompt --output-format json # (6)!
    datamodel-codegen --input schema.json --emit-model-metadata model-map.json # (7)!
    ```

    1. :material-arrow-left: Emit the JSON Schema for structured prompt output
    2. :material-arrow-left: Emit the JSON Schema for generated-file output
    3. :material-arrow-left: Emit the JSON Schema for generated model metadata
    4. :material-arrow-left: Emit the JSON Schema for all structured command outputs
    5. :material-arrow-left: Emit the JSON Schema for JSON-valued configuration options
    6. :material-arrow-left: Emit prompt payloads that match the prompt schema
    7. :material-arrow-left: Emit metadata payloads that match the model metadata schema

---

## `--overwrite-skill` {#overwrite-skill}

Replace an existing regular Agent Skill directory when installing with
`--install-skill`.

Without this option, an existing skill is preserved and the command exits with
an error. Symlinks and non-directory targets are never overwritten.

    datamodel-codegen --install-skill codex --overwrite-skill

---

## `--profile` {#profile}

Use a named profile from pyproject.toml configuration.

Profiles allow you to define multiple named configurations in your pyproject.toml
file. Each profile can override the default settings with its own set of options.

**Related:** [pyproject.toml Configuration](../pyproject_toml.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --profile strict # (1)!
    ```

    1. :material-arrow-left: `--profile` - the option documented here

??? example "Configuration (pyproject.toml)"

    ```toml
    [tool.datamodel-codegen]
    # Default configuration
    output-model-type = "pydantic_v2.BaseModel"

    [tool.datamodel-codegen.profiles.strict]
    # Strict profile with additional options
    strict-types = ["str", "int", "float", "bool"]
    strict-nullable = true

    [tool.datamodel-codegen.profiles.dataclass]
    # Dataclass profile
    output-model-type = "dataclasses.dataclass"
    ```

    Use profiles:

    ```bash
    # Use the strict profile
    datamodel-codegen --input schema.json --profile strict

    # Use the dataclass profile
    datamodel-codegen --input schema.json --profile dataclass
    ```

---

## `--skill-scope` {#skill-scope}

Choose whether `--install-skill` installs the bundled Agent Skill for the
current project or for the current user. The default is `project`.

`project` uses `.agents/skills/` for Codex and `.claude/skills/` for Claude
Code below the current working directory. `user` uses the matching directory
below your home directory.

    datamodel-codegen --install-skill codex --skill-scope project
    datamodel-codegen --install-skill claude-code --skill-scope user

---

## `--version` {#version}

Show program version and exit.

Displays the installed version of datamodel-code-generator.

!!! tip "Usage"

    ```bash
    datamodel-codegen --version # (1)!
    ```

    1. :material-arrow-left: `--version` - the option documented here

??? example "Output"

    ```text
    datamodel-codegen version: 0.x.x
    ```

---
