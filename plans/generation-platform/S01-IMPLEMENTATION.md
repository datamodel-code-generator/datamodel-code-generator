# S01 implementation record

The authoritative design is [PR #4098](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4098), plan commit `4faddb35e6f5fd73ec79fef7f10216183d3bc9c8`.
The implementation starts at main `4f96e22ea403a66faae96f3949d41dd61fc1186f`, identical to the pinned baseline; there are no intervening integration-point changes.
The planning PR remains separate and unmerged. This stack implements S01 only and exposes no new public generation target or scope.

## S01-1: comparison and observation

The existing public API signatures, generated snapshots, architecture/store checks, retry tests, remote-lock tests, and startup/generation benchmarks remain the primary oracles.
The additional end-to-end observation uses real generation across all five backends with inheritance, request/response variants, root collapse, and reuse.
It records generated file inventory and SHA256 of complete bytes, actual copy/ref/validation/render call counts, phase order, return value, new target imports, and weak-reference parser retention after collection.
Input mutation uses the existing assertion helper. Timestamps are explicitly disabled and formatters are explicitly empty; no output normalization is used before hashing.
The snapshots were established before product changes. Existing snapshots and API baselines are unchanged.

Initial verification: `pytest tests/main/test_generation_observation.py tests/main/test_public_api_signature_baseline.py tests/main/test_generation_determinism.py tests/test_architecture_boundaries.py tests/parser/test_generation_store_usage.py -q`: 80 passed.
Environment: CPython 3.13.2, macOS, dependencies installed from `uv.lock` using `uv sync --frozen --all-extras --group test --group fix --python 3.13`.

Performance procedure: existing startup cases and generation fixtures, independent processes, equal AB/BA ordering, fixed seed 4098, 30 A/A pairs and 60 A/B pairs per case, warmup before measurement, 99% bootstrap interval and two-sided sign test at 0.01.
The temporary measurement runner and raw results are outside the repository under `/tmp/dcg-s01-evidence`.
The completed evidence and remaining limits are recorded below. The original measurement environment included optional ryaml; final verification uses PyYAML, as the repository CI `all` extra does (that extra excludes ryaml). No dependency declarations or lockfiles changed.

## S01-2: bounded factories and copies

Added class-level store/resolver factories and three non-function partial copy callables, preserving original free helpers and their module exports. The same recursive helpers execute once with unchanged arguments. No store/model/reference fields, observers, backend classes, or runtime dependencies were added.
Extracted the existing dotted result-path expansion unchanged and retained dynamic dispatch to the existing name-mangled classmethod through a property.
A real parser/store/resolver consumer exercises constructor selection, registered/unregistered field copies, inherited copies, cross-module type copies, two-stage snooper decoration, and an operation callback replaced during traversal. Guarded methods remain inherited on the undecorated consumer.

Validation: 321 tests passed across observations, integration consumers, parser/store regressions, public API signatures, and assertion-helper enforcement. Architecture/store checkers, Ruff, and ty passed.
Performance: 30 A/A and 60 A/B pairs completed for 11 cases (six existing startup cases plus small/large/reference/multiple-module/builtin-format generation fixtures). No wall/CPU case met all predeclared regression conditions. This is not proof of zero overhead: builtin-format wall-time CI excluded zero but the sign test was 0.0135, and A/A multi-module RSS had directional drift. Memory and borderline timing conclusions remain inconclusive pending repeated cumulative measurements. Initial failed startup measurement was caused by an absent generated `_version.py` in the baseline checkout; both baselines were then given the identical generated version module before restarting all pairs.

## S01-3: shared driver and attempt lifetime

The legacy `_generate` signature and return value remain unchanged; it delegates to one `_run_generation`. Facade preparation and the separate ordinary/atomic remote-lock policies are extracted in their original order, without changing config-copy counts or collector ownership. The existing parser-result four-tuple is retained.
Private, type-only capture contracts select the same OpenAPI parser factory for initial and retry construction. Ordinary execution does not instantiate or runtime-import capture contracts. A bounded test consumer freezes immutable result observations before disposal, accepts only the driver's selected attempt, and exercises failures without implementing the S03 binding ledger or exposing public targets.
Capture recording failures are re-raised after the existing stdout repair suppression. Failure cleanup preserves the primary exception; the legacy disposal policy remains unchanged when capture is absent. Successful session lifetime remains owned by its caller.

Initial driver regression run: 481 passed. Nineteen real-parser attempt cases cover initial success, collapse retry/repeated failure, accepted stdout repair, rejection after the input file actually changes, suppressed ordinary retry failure, fatal recording/freezing/acceptance errors, disposal errors, discard errors, close errors, and actual filesystem emission failure. Generated output hashes and zero retained parser weak references are recorded with the existing assertion helper. These new fixtures were inspected for actual second-attempt execution; the rejection case preserves attempt 1 and the accepted repair selects attempt 2.
No merge has been performed. The completed verification and outstanding measurement limits follow.


## Stack and verification record

Native GitHub stack #4102 was registered using `gh stack init`, `gh stack add`, and `gh stack submit`. Subsequent propagation used `gh stack rebase --no-trunk --remote origin` and `gh stack push --remote origin`.

| Unit | Branch | PR | Reviewed implementation head |
| --- | --- | --- | --- |
| S01-1 | `generation-platform-observation` | [#4099](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4099) | `8d574112ec0a6b67de8a15496d8069fa0868c018` |
| S01-2 | `generation-platform-factories` | [#4100](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4100) | `91a812c1db26801066bfbbf0f35899932b0e7bc7` |
| S01-3 | `generation-platform-lifecycle` | [#4101](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4101) | `58ea7a28c3005b7a500b2480665672f90ae14c1e` |

The final record is a documentation-only follow-up to these implementation commits. Main was rechecked and remains the pinned baseline. PRs were created with empty bodies; subsequent bot summaries were left untouched. S02 and later stacks are not implemented.

### Compatibility and coverage

- The observation test passes against two independent unchanged-baseline checkouts (5 cases each). Deliberately changed output bytes, an added public parameter, and an extra resolver call are each rejected by the existing comparison helpers. The mutations were restored; only temporary control checkouts were modified.
- `pytest tests -n6 --dist worksteal -m 'not perf' --cov=datamodel_code_generator --cov=tests --cov-fail-under=0` completed with 19,145 passing tests and 15 skips. Two generated-document checks failed during the documentation update; rerunning the architecture and llms checks after regeneration passed all 5 tests. The full local coverage percentage is 99% because other Python versions, OSes, and dependency combinations cover the remaining existing branches in CI.
- `diff-cover --compare-branch origin/main` reported 100% of 415 changed executable lines. The 29 new observation/integration/lifetime cases also had 100% line and branch coverage for their test modules. CI combines the repository's complete coverage matrix. The final focused rerun, including the existing field-name binding matrix, passed 319 tests.
- Ruff 0.16.7 with the repository preview/ignore arguments, ty, architecture boundaries, generation-store usage, and generated-document checks passed. Existing output snapshots and public API baselines were not regenerated.
- Native Windows output hashes use separate expectations for native CRLF bytes. These hashes were established from baseline output and matched the baseline-only Windows CI results. The real emission failure records native `FileNotFoundError`/errno 2 on Windows and `NotADirectoryError`/errno 20 on POSIX; capture events and primary-error preservation are identical.
- The profiler callback is an executable fixture outside coverage tracing, following the existing fixture exclusion. Profiling callbacks cannot themselves be traced by coverage. No assertion helper or coverage configuration was added.

Failures investigated rather than hidden: the initial all-extras local environment reproduced seven YAML failures on unchanged main because ryaml is outside CI's `all` extra. Removing that optional package aligned the local environment with CI. Parser factory selection initially obscured OpenAPI routing from the existing AST-based documentation generator; restoring a direct default `OpenAPIParser` call preserved the original generated documents. An added `None` keyword initially changed a legacy builder-call trace; the ordinary call signature was restored. One isort 7 CI run exposed a pre-existing runtime Literal-order fluctuation; rerunning the failed jobs passed without changing code or goldens.

### Performance and retained allocations

All pairs use the same interpreter, dependency environment, source fixture, process cwd, timestamp setting, formatting selection, hash seed, timezone, and locale. Independent processes have one warmup per label; the repeated-API case executes 20 calls within each process. Labels use equal AB/BA ordering with seed 4098. The analysis uses 10,000 bootstrap resamples, a 99% median-difference interval, and a two-sided sign test at 0.01. Raw samples and temporary runners remain in `/tmp/dcg-s01-evidence`; they are not a new repository benchmark framework.

The cumulative installed-pysnooper run completed 30 A/A and 60 A/B pairs for 15 cases: six repository cold-start/help/version cases, small/large/reference/multiple-module schemas, builtin formatting, repeated APIs, a recursive schema, black/isort formatting, and enabled debug tracing. No wall/CPU case met all predeclared regression conditions. Selected paired wall differences (candidate minus baseline) are:

| Case | Median difference | 99% interval |
| --- | --- | --- |
| Small generation | -0.261 ms | [-1.030, 0.721] ms |
| Large generation | -0.687 ms | [-1.702, 0.539] ms |
| Reference-heavy generation | -0.169 ms | [-0.880, 0.935] ms |
| Multiple modules | -0.058 ms | [-1.353, 0.883] ms |
| Builtin formatter | +0.201 ms | [-0.732, 0.863] ms |
| 20 repeated calls | +0.013 ms | [-0.338, 0.298] ms |
| Debug tracing enabled | -2.648 ms | [-5.311, 0.152] ms |

Separate 30 A/A and 60 A/B microbenchmark pairs exercise the three real copy helpers through the partial attributes and the dynamic result postprocessor. No case met all three regression conditions. The field-copy loop had a +57.5 microsecond median difference over 2,000 copies (about 28.8 ns/call), CI [15.0, 111.9] microseconds, p=0.00267, but A/A's upper bound was 145.5 microseconds. This visible dispatch cost is not claimed to be free. Type-copy, inherited-copy, and descriptor intervals included zero. The first microbenchmark attempt incorrectly passed an empty module map to the existing postprocessor and raised StopIteration; the corrected fixture includes its required root module. A subsequent temporary-script quoting error was also corrected before the completed run.

Twenty paired tracemalloc/GC runs each performed ten reference-heavy generations. Output hashes and imported-module inventories matched in every pair. No additional retained dcg objects by type were found after collection. Median current/peak paired differences were both +621 bytes; these are not asserted to be zero. Follow-up snapshots after 10 and 100 calls showed the same allocation sites in both versions (JSON decoding, the existing ref-type cache, file opening, and saved GC thresholds), with no new retained site. The residual memory difference remains inconclusive; the observations do not establish universal memory equivalence. Source review confirms unchanged model/store layouts and recursive copy counts, no unused-target imports or per-instance capture objects, and only the planned class callables, helper functions, and conditional driver work.

The earlier factories-only run and cumulative run are separate measurement sessions. Neither established an end-to-end wall/CPU regression. This evidence has finite resolution and does not prove zero overhead for every input. No acceptable slowdown threshold was introduced. A separate environment with pysnooper uninstalled completed 30 A/A and 60 A/B pairs for small, reference-heavy, multiple-module, and 20-call repeated generation; no wall/CPU case met the regression conditions. The installed version was restored afterward. The absent-pysnooper reference case had a positive RSS difference inside the A/A variation bound, so memory remains inconclusive.

### Review and remaining actions

CodeRabbit reviewed each implementation boundary; S01-1 and S01-2 initially had no actionable code findings. New test callback docstrings were added in response to its documentation warning. S01-3's review through `58ea7a28` reported no concrete blocking behavior. Eight CodeQL findings about redundant ellipsis statements in documented protocol methods were fixed and replied to; all eight conversations are resolved. The code remains private and absent from ordinary runtime imports.

Implemented: S01's three integration boundaries. Verified: the compatibility, coverage, review, and measured performance scope above. Merged: none. Memory equivalence outside the measured scope remains unproven. At this record, all three implementation heads passed the test/coverage matrix, and S01-3 passed its isort 7 rerun. S01-2 has a noisy CodSpeed status; it is not substituted for the paired local evidence above. The next action is to verify final-head CI after the documentation-only record commit, then report the three open PRs and these measurement limits. Do not merge or start S02 without separate authorization.
