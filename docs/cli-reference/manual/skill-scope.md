## `--skill-scope` {#skill-scope}

Choose whether `--install-skill` installs the bundled Agent Skill for the
current project or for the current user. The default is `project`.

`project` uses `.agents/skills/` for Codex and `.claude/skills/` for Claude
Code below the current working directory. `user` uses the matching directory
below your home directory.

    datamodel-codegen --install-skill codex --skill-scope project
    datamodel-codegen --install-skill claude-code --skill-scope user
