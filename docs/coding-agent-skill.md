# Using datamodel-code-generator with coding agents

This repository includes an experimental Agent Skill for skills-compatible coding agents. The skill teaches an agent to run `datamodel-codegen` when a user asks for Python models from OpenAPI, AsyncAPI, JSON Schema, GraphQL, JSON/YAML/CSV sample data, MCP tool schemas, Protocol Buffers, XML Schema, Apache Avro, or existing Python model objects.

The skill is a directory with a `SKILL.md` entrypoint and supporting files under
`references/`. It is bundled with the CLI and can be installed without checking
out this repository.

Treat this skill as experimental. The workflow, trigger wording, and client-specific install guidance can change as Codex, Claude Code, and other skills-compatible agents evolve.

## Why agents should run the CLI

Agents should run `datamodel-codegen` instead of hand-writing generated models when a usable input artifact exists.

The CLI handles refs, enums, nested models, unions, `allOf`, `oneOf`, `anyOf`, and model reuse. It also produces output that can be imported and checked after generation.

Agents should also use the CLI for option discovery. Before choosing options, run the recommender (experimental) and apply its result:

```bash
datamodel-codegen --input SCHEMA [--input-file-type TYPE] [--output-model-type TYPE] --recommend-options --output-format json
```

Use `arguments` from the payload (or `config` for a project configured in `pyproject.toml`) as the starting command, and do not add options it lists under `not_recommended` without a stated reason. When the goal needs judgment beyond the schema's shape, add `--recommend-options` to a `--generate-prompt` command so the prompt includes the recommendation, and decide from both. See [Option recommendations](recommend-options.md).

`datamodel-codegen --help` links to this skill documentation, and `--generate-prompt` can produce either Markdown guidance or structured JSON for tool-aware agents. Add `--recommend-options` when an input schema is available; without an input schema, run `--generate-prompt` alone.

```bash
datamodel-codegen [known options] --generate-prompt "Find the minimal options for this schema." --recommend-options
datamodel-codegen [known options] --generate-prompt "Find the minimal options for this schema."
datamodel-codegen [known options] --generate-prompt "Find the minimal options for this schema." --output-format json
datamodel-codegen --output-format-json-schema structured-output
```

Use the generated prompt output before recommending a final command when the correct input type, output model type, strictness, aliasing, module layout, or verification flags are not obvious.

## Install for Claude Code

Claude Code project skills load from `.claude/skills/` in the starting directory and parent directories up to the repository root. Personal skills can be placed under `~/.claude/skills/`.

For a project skill, run:

```bash
datamodel-codegen --install-skill claude-code
```

For a personal skill, run:

```bash
datamodel-codegen --install-skill claude-code --skill-scope user
```

Claude Code can invoke a skill directly with `/datamodel-code-generator`, or load it automatically when the description matches the task. Existing top-level skill directories are watched for changes. If the top-level skills directory did not exist when the session started, restart Claude Code.

## Install for Codex

Codex reads project skills from `.agents/skills` in the current directory, parent directories, and the repository root. It reads personal skills from `$HOME/.agents/skills`.

For a project skill, run:

```bash
datamodel-codegen --install-skill codex
```

For a personal skill, run:

```bash
datamodel-codegen --install-skill codex --skill-scope user
```

Codex can invoke skills explicitly through `/skills` or `$` mention, or load them automatically when the description matches the task.

The installer preserves an existing skill directory by default. Use
`--overwrite-skill` only when you want to replace it; symlinks and
non-directory targets are never overwritten.

## Other agents

Other skills-compatible agents may support this directory format. Check the client documentation for its skill search path.

## Distribution paths

| Form | Install method | Best use |
| --- | --- | --- |
| Repository skill | Copy to `.agents/skills/` or `.claude/skills/` | OSS and internal repositories |
| Personal skill | Copy to `~/.agents/skills/` or `~/.claude/skills/` | Reuse across a user's projects |
| Codex local install | Use `$skill-installer` when appropriate | Codex local experiments |
| Codex plugin | Package separately as a plugin | Reusable Codex distribution |
| Claude plugin | Package separately as a plugin | Bundle skills with hooks, MCP, or agents |

Start with the repository skill. Consider Codex or Claude plugin packaging only as a separate distribution task.

## Example prompt

```text
Create Pydantic v2 models from openapi.yaml.
```

Expected agent behavior:

1. Runs `datamodel-codegen`.
2. Writes the generated file.
3. Imports the generated file.
4. Summarizes generated classes.

## Troubleshooting

- The CLI command is `datamodel-codegen`.
- The package name is `datamodel-code-generator`.
- Use extras for HTTP, GraphQL, and protobuf when needed. Install `msgspec` separately when targeting `msgspec.Struct`.
- If a JSON or YAML file is a schema, choose `openapi`, `asyncapi`, or `jsonschema` input type instead of raw sample data mode.

## References

- [Agent Skills specification](https://agentskills.io/specification)
- [Codex Agent Skills](https://developers.openai.com/codex/skills)
- [Claude Code skills](https://docs.anthropic.com/en/docs/claude-code/skills)
