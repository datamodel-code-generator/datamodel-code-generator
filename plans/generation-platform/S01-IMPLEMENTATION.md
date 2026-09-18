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
Measurements, mutation negative controls, cumulative coverage, independent PR review, and CI are pending; this record does not claim those gates passed.

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
Full tests, cumulative diff coverage, PR review, and repeated performance measurements are still running or pending. No merge has been performed.
