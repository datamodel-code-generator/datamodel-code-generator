## `--overwrite-skill` {#overwrite-skill}

Replace an existing regular Agent Skill directory when installing with
`--install-skill`.

Without this option, an existing skill is preserved and the command exits with
an error. Symlinks and non-directory targets are never overwritten.

    datamodel-codegen --install-skill codex --overwrite-skill
