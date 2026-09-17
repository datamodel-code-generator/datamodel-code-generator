# 🛠️ Development

Install the package in editable mode:

```sh
$ git clone git@github.com:datamodel-code-generator/datamodel-code-generator.git
$ pip install -e datamodel-code-generator
```

# 🤝 Contribute

We are waiting for your contributions to `datamodel-code-generator`.

## 📝 How to contribute

```bash
## 1. Clone your fork repository
$ git clone git@github.com:<your username>/datamodel-code-generator.git
$ cd datamodel-code-generator

## 2. Install [uv](https://docs.astral.sh/uv/getting-started/installation/)
$ curl -LsSf https://astral.sh/uv/install.sh | sh

## 3. Install tox with uv
$ uv tool install --python-preference only-managed --python 3.13 tox --with tox-uv

## 4. Create developer environment
$ tox run -e dev

.tox/dev is a Python environment you can use for development purposes

## 5. Install pre-commit hooks
$ uv tool install prek
$ prek install

## 6. Create new branch and rewrite code.
$ git checkout -b new-branch

## 7. Run unittest under Python 3.13 (you should pass all test and coverage should be 100%)
$ tox run -e 3.13

## 8. Format and lint code (will print errors that cannot be automatically fixed)
$ tox run -e fix

## 9. Check generated docs, diagrams, and playground assets are up to date
$ tox run -e docs-check-all -- --check

## 10. Commit and Push...
```

## 🧪 E2E test assertions

E2E tests that validate generated output must use the shared assertion helpers
instead of direct `assert` statements. The helpers compare complete generated
files, generated modules, warnings, errors, and HTTP request behavior with
consistent diffs and update hints.

Use helpers such as `run_main_and_assert`, `run_main_url_and_assert`,
`create_assert_file_content`, `assert_output`, `assert_directory_content`,
`assert_generated_modules_output`, `assert_generated_file_matches_output`,
`assert_httpx_get_kwargs`, and `assert_warnings_contain` where they fit. Prefer
full expected-file or inline snapshot comparisons for generated output over
substring checks.

The file-output helpers use
[`inline-snapshot`](https://15r10nk.github.io/inline-snapshot/latest/) and
`external_file()` internally. When an expected output file is missing, the
failure message includes the command to create it, such as
`tox run -e <version> -- --inline-snapshot=create`. When an existing expected
file differs from the generated output, the failure includes a diff and the
command to update it, such as
`tox run -e <version> -- --inline-snapshot=fix`. Review the generated files and
the resulting `git diff` before committing. See the inline-snapshot
[`--inline-snapshot` pytest options](https://15r10nk.github.io/inline-snapshot/latest/pytest/#-inline-snapshotcreatefixtrimupdate)
for the meaning of `create` and `fix`.

Direct `assert` statements are blocked in guarded test modules by
`tests/test_assert_helper_usage.py`. New test files are guarded by default; add
legacy or unit-focused files to
`assert_helper_direct_assert_exempt_files` in `pyproject.toml` only when they
are intentionally outside the generated-output helper policy. Use
`@pytest.mark.allow_direct_assert` for narrow in-file exceptions that cannot
reasonably be expressed with the shared helpers, such as external-request mock
checks or intermediate-state checks.

## Documentation formatting

Ruff formats Python code blocks in Markdown, including hand-written explanations.
Documentation builders surround fixture-backed examples and generated output with
`<!-- fmt: off -->` and `<!-- fmt: on -->` so the displayed code keeps the exact
quote style and layout produced by the documented command. Keep these markers in
the builders rather than editing generated Markdown directly.

After changing documentation, regenerate the affected pages and then run
`python scripts/build_llms_txt.py`. Run `tox run -e fix` and
`tox run -e docs-check-all -- --check` to verify formatting and generated content
agree.

## Release notes

Release notes should describe changes that affect users: features, fixes,
generated output, performance, compatibility, installation requirements, and
the official GitHub Action or Playground.

Use the `skip-changelog` PR label for changes that only maintain tests, CI,
release automation, generated documentation, or development tooling. The
release workflow adds it automatically when every changed file is a known
maintenance file, including CHANGELOG updates and release benchmark data.
Other changes need a maintainer's judgment before applying the label.

Check the complete diff before excluding dependency updates, refactors, or
documentation changes. They can affect users through `action.yml`, packaged
CLI prompt data, Playground assets, or generated code. A PR that mixes
maintenance with user-facing changes should stay in the release notes.

The label only filters GitHub's generated change list. Breaking-change
analysis still runs independently, and the full comparison link remains
available. Excluded PRs receive no release notification unless they are
referenced elsewhere in the release body.

## Architecture boundaries

Before adding a dependency between layers, check `docs/architecture.md` and run:

```bash
python scripts/check_architecture_boundaries.py
```

The guard rejects concrete output-backend imports and backend-name inspection in
parsers, concrete backend imports or `sys.modules` lookups from shared model
modules, private `parser._*` imports from shared code, parser/backend dependencies
from configuration, and local output-family mappings in `input_model.py`. Move
output behavior to a neutral capability on `DataModel` or `DataModelFieldBase`,
keep backend lifecycle and cache management with its backend or the model
composition root, and move reusable helpers to a neutral package module. Do not
add an allowlist entry for new code. Existing entries document compatibility debt,
have bounded occurrence counts, and fail when stale.

Tests for the checker use source fixtures and expected reports under
`tests/data/architecture_boundaries/` and
`tests/data/expected/architecture_boundaries/`. Keep diagnostics specific enough
that a contributor or coding agent can identify the owning layer and the expected
remediation without reading the checker implementation.

## Parser generation state

Parser code must keep generated output, naming order, parse order, and
canonical model selection compatible with existing releases. `ModelResolver`
remains the only authority for names. Do not move name generation or parser
dispatch into the generation index.

Use `GenerationStore` for parser-side mutations that affect generation facts:

- Register parsed models with `generation_store.register_model(model)`.
- Replace references with `generation_store.replace_data_type_ref(...)` or
  `generation_store.detach_data_type_ref(...)`.
- Replace field or nested data types with `replace_field_type(...)`,
  `replace_nested_data_type(...)`, or `set_nested_data_types(...)`.
- Replace fields or base classes with `set_fields(...)`,
  `append_field(...)`, `insert_field(...)`, `remove_field(...)`, or
  `set_base_classes(...)`.
- Move or rename generated models with `move_model(...)`,
  `rename_model(...)`, or `update_model_reference(...)`.

Parser code should not directly assign `data_type.reference`, `model.fields`,
`model.base_classes`, `model.reference.name`, or call reference child mutation
helpers. Read dependency information through `GenerationIndex` queries instead
of using `Reference.children` as a reverse index.

The local pre-commit hook `generation-store-usage` runs
`scripts/check_generation_store_usage.py` and rejects common parser mutations
that bypass the store. If a new mutation helper is needed, add the method to
`GENERATION_STORE_MUTATION_METHODS` and update the checker tests so contributors
get an actionable message.

## ➕ Adding a New CLI Option

When adding a new CLI option to `datamodel-code-generator`, follow these steps:

### Step 1: Implement the option (Required)

Add the option to `src/datamodel_code_generator/arguments.py`:

```python
arg_parser.add_argument(
    "--my-new-option",
    help="Description of what this option does",
    action="store_true",  # or other action type
)
```

### Step 2: Add a test with documentation marker (Required)

Create a test that demonstrates the option and add the `@pytest.mark.cli_doc()` marker:

```python
@pytest.mark.cli_doc(
    options=["--my-new-option"],
    input_schema="jsonschema/example.json",  # Path relative to tests/data/
    cli_args=["--my-new-option"],
    golden_output="jsonschema/example_with_my_option.py",  # Expected output
)
def test_my_new_option(output_file: Path) -> None:
    """Short description of what the option does.

    This docstring becomes the documentation for the option.
    Explain when and why users would use this option.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "example.json",
        output_path=output_file,
        extra_args=["--my-new-option"],
        ...
    )
```

### Step 3: Categorize the option (Optional)

By default, new options appear in "General Options". To place in a specific category, add to `src/datamodel_code_generator/cli_options.py`:

```python
CLI_OPTION_META: dict[str, CLIOptionMeta] = {
    ...
    "--my-new-option": CLIOptionMeta(
        name="--my-new-option",
        category=OptionCategory.MODEL,  # or FIELD, TYPING, TEMPLATE, etc.
    ),
}
```

### Step 4: Generate and verify documentation

```bash
# Regenerate CLI docs
$ pytest --collect-cli-docs -p no:xdist -q
$ python scripts/build_cli_docs.py

# Verify generated docs are correct
$ tox run -e docs-check-all -- --check

# If you modified config.py, regenerate config TypedDicts
$ tox run -e config-types
```

### 🔧 Troubleshooting

If the `tox run -e docs-check-all -- --check` CLI docs step fails:

- **"No test found documenting option --xxx"**: Add `@pytest.mark.cli_doc(options=["--xxx"], ...)` to a test
- **"File not found: ..."**: Check that `input_schema` and `golden_output` paths are correct
- **"CLI docs are OUT OF DATE"**: Run `python scripts/build_cli_docs.py` to regenerate

## 📖 CLI Documentation Marker Reference

The `cli_doc` marker supports:

| Parameter | Required | Description |
|-----------|----------|-------------|
| `options` | Yes | List of CLI options this test documents |
| `input_schema` | Yes | Input schema path (relative to `tests/data/`) |
| `cli_args` | Yes | CLI arguments used in the test |
| `golden_output` | Yes* | Expected output file path |
| `model_outputs` | No | Dict of model type → output file (for multi-model tabs) |
| `version_outputs` | No | Dict of Python version → output file |
| `comparison_output` | No | Baseline output without option (for before/after) |
| `primary` | No | Set `True` if this is the main example for the option |

*Either `golden_output` or `model_outputs` is required.

See existing tests in `tests/main/` for examples.
