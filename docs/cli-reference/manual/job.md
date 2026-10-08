## `--job` {#job}

Run one or more named generation jobs declared in `pyproject.toml` (experimental). Jobs are
executed sequentially in TOML declaration order, even when `--job` is supplied
in a different order.

!!! warning "Experimental"

    Named batch jobs are experimental; their configuration schema, batch output,
    and transactional/watch execution contracts may change.

**Related:** [pyproject.toml Configuration](../../pyproject_toml.md),
[`--all-jobs`](all-jobs.md#all-jobs), [`--diff-against`](../general-options.md#diff-against)

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

A job whose settings select `generate-server` also generates the FastAPI server
package, and such jobs can share one models `output`; see [Batch jobs](../../fastapi-server.md#batch-jobs).
