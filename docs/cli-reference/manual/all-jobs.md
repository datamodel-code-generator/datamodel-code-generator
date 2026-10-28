## `--all-jobs` {#all-jobs}

Run every named generation job from `[tool.datamodel-codegen.jobs]` in declaration
order (experimental).

!!! warning "Experimental"

    Named batch jobs are experimental; their configuration schema, batch output,
    and transactional/watch execution contracts may change.

**Related:** [`--job`](job.md#job), [`--diff-against`](../general-options.md#diff-against),
[pyproject.toml Configuration](../../pyproject_toml.md)

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
