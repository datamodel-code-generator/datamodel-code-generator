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
