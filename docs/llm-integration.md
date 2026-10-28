# LLM Integration

<!-- related-cli-options: --generate-prompt, --output-format, --output-format-json-schema -->

The `--generate-prompt` option generates a formatted prompt that you can use
to consult Large Language Models (LLMs) about datamodel-code-generator CLI options.

## Overview

When you're unsure which CLI options to use for your specific use case,
generate a prompt that includes the options already selected for the command,
the available options grouped by category, and the full help text. Give that
prompt to an LLM and ask it to propose the smallest command that satisfies your
schema and generation goal.

```bash
datamodel-codegen --generate-prompt "How do I generate strict Pydantic v2 models?"
```

The generated prompt includes:

- Your question (if provided)
- Current CLI options you've specified
- All options organized by category with descriptions
- Full help text for reference

## If You Are an LLM Agent

Use `--generate-prompt` as an option discovery step before you recommend or run
a final `datamodel-codegen` command.

1. Inspect the user's goal and the input schema when it is available.
2. Start from any options the user or existing project already selected.
3. Run `datamodel-codegen [known options] --generate-prompt "<goal>"`. When the input
   schema is available, add `--recommend-options` (experimental) so the prompt includes
   the schema analysis and the recommended command to start from.
4. Preserve current options unless they conflict with the goal or another option.
5. Choose the minimal additional options needed for the requested output.
6. Return the final CLI command, reasons for each non-obvious option, rejected
   alternatives, and a verification command.

Use current options in the prompt command so the generated prompt reflects the
real configuration you are improving:

```bash
datamodel-codegen \
    --input openapi.yaml \
    --input-file-type openapi \
    --output models.py \
    --output-model-type pydantic_v2.BaseModel \
    --target-python-version 3.12 \
    --generate-prompt "Find the minimal options for strict API response models."
```

A useful answer should keep the command runnable and explain tradeoffs:

- Final command to run
- Why each added or changed option is needed
- Current options that should remain unchanged
- Options considered but rejected, with a short reason
- Verification command, such as `datamodel-codegen ... --check` or a focused
  generation command against a fixture schema

## Structured Output for Tools

Use `--output-format json` when an LLM agent or tool should consume structured option
metadata instead of Markdown:

```bash
datamodel-codegen --generate-prompt "How do I generate strict Pydantic v2 models?" --output-format json
```

The JSON payload includes the user question, current options, options grouped by
category, full option metadata from argparse, and help text without ANSI color
codes.

Use `--output-format-json-schema` when a tool or agent needs a JSON Schema before
it consumes command output:

```bash
datamodel-codegen --output-format-json-schema config
datamodel-codegen --output-format-json-schema generate-prompt
datamodel-codegen --output-format-json-schema generation
datamodel-codegen --output-format-json-schema structured-output
```

The schema targets are scoped:

- `config`: schema for JSON configuration options such as `--aliases`,
  `--type-overrides`, and `--validators`
- `generate-prompt`: schema for `--generate-prompt --output-format json`
- `generation`: schema for normal generation with `--output-format json`
- `structured-output`: tagged union schema for all structured command outputs,
  including prompt, generation, command metadata, and check result payloads

## CLI LLM Tools

Pipe the generated prompt directly to CLI-based LLM tools:

### Claude Code

[Claude Code](https://docs.anthropic.com/en/docs/claude-code) is Anthropic's official CLI tool.
Use `-p` flag for non-interactive (pipe) mode:

```bash
datamodel-codegen --generate-prompt "Best options for API response models?" | claude -p
```

### OpenAI Codex CLI

[Codex CLI](https://github.com/openai/codex) is OpenAI's CLI tool.
Use `exec` subcommand for non-interactive mode:

```bash
datamodel-codegen --generate-prompt "How to handle nullable fields?" | codex exec
```

For agents that can inspect structured input, prefer JSON:

```bash
datamodel-codegen --generate-prompt "How to handle nullable fields?" --output-format json | codex exec
```

### Other CLI Tools

Other popular LLM CLI tools that accept stdin:

| Tool | Command | Repository |
|------|---------|------------|
| llm | `\| llm` | [simonw/llm](https://github.com/simonw/llm) |
| aichat | `\| aichat` | [sigoden/aichat](https://github.com/sigoden/aichat) |
| sgpt | `\| sgpt` | [TheR1D/shell_gpt](https://github.com/TheR1D/shell_gpt) |
| mods | `\| mods` | [charmbracelet/mods](https://github.com/charmbracelet/mods) |

Check each tool's documentation for specific usage and options.

## Web LLM Chat Services

Copy the prompt to clipboard, then paste into your preferred web-based LLM chat:

### macOS

```bash
datamodel-codegen --generate-prompt | pbcopy
```

### Linux (X11)

```bash
datamodel-codegen --generate-prompt | xclip -selection clipboard
```

### Linux (Wayland)

```bash
datamodel-codegen --generate-prompt | wl-copy
```

### Windows (PowerShell)

```powershell
datamodel-codegen --generate-prompt | Set-Clipboard
```

### WSL2

```bash
datamodel-codegen --generate-prompt | clip.exe
```

## Usage Examples

### Basic Usage

Generate a prompt without a specific question:

```bash
datamodel-codegen --generate-prompt
```

### JSON Output

Generate structured option metadata for automated tools:

```bash
datamodel-codegen --generate-prompt "Find the minimal strict-model options." --output-format json
```

### With a Question

Include your specific question in the prompt:

```bash
datamodel-codegen --generate-prompt "What options should I use for GraphQL schema?"
```

### With Current Options

Show your current configuration and ask for improvements:

```bash
datamodel-codegen \
    --input schema.json \
    --output-model-type pydantic_v2.BaseModel \
    --use-annotated \
    --generate-prompt "Are there any other options I should consider?"
```

### Strict Pydantic v2 Models

```bash
datamodel-codegen \
    --input openapi.yaml \
    --input-file-type openapi \
    --output models.py \
    --output-model-type pydantic_v2.BaseModel \
    --target-python-version 3.12 \
    --strict-types str int float bool \
    --generate-prompt "Which additional options should I use for strict API response models?" \
    | claude -p
```

### Review an Existing Command

```bash
datamodel-codegen \
    --input schema.json \
    --output models.py \
    --output-model-type pydantic_v2.BaseModel \
    --target-python-version 3.11 \
    --snake-case-field \
    --generate-prompt "Review this command and suggest only necessary option changes." \
    | codex exec
```

## Tips

1. **Be specific** - Include a clear question to get more relevant recommendations
2. **Show context** - Add your current options so the LLM understands your setup
3. **Keep options minimal** - Prefer the fewest options that satisfy the goal
4. **Verify output** - Run the final command against the target schema or fixture
