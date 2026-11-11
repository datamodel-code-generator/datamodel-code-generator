# datamodel-codegen

This package is an alias of [datamodel-code-generator](https://pypi.org/project/datamodel-code-generator/). It installs
the same release of `datamodel-code-generator` and the `datamodel-codegen` command, so that the command runs by its name:

```bash
uvx datamodel-codegen --input schema.json --output model.py
pipx run datamodel-codegen --input schema.json --output model.py
```

In a project's dependencies, list `datamodel-code-generator` itself, not this alias: both packages install the
`datamodel-codegen` command, so removing the alias from an environment that has both removes the command too. Documentation, issues and releases:
https://github.com/datamodel-code-generator/datamodel-code-generator
