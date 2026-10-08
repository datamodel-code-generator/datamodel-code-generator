# Dependency migration notices

This release prepares later changes. Black and isort remain the default formatters and required dependencies;
all current dependency ranges, compatibility code and minimum-version tests remain in place. Generation still
continues when formatters are omitted. The built-in formatter remains experimental.

## Choose formatting independently of the default

- Use `--formatters ruff-check ruff-format` when your project uses Ruff and generated code should follow its lint
  and formatting settings. Install `datamodel-code-generator[ruff]` when needed.
- Use `--formatters builtin` when you use no external formatter or prioritize generation speed and avoiding
  external formatter execution.
- Keep `--formatters black isort` to preserve your existing Black/isort policy and generated formatting.

The future builtin default is intended to reduce mandatory installation dependencies and version constraints.
It does not change the recommendation to use Ruff in projects standardized on Ruff. Packages and Ruff configuration
never trigger automatic formatter selection.

Configure the formatter once in `pyproject.toml` to avoid repeating CLI options:

```toml
[tool.datamodel-codegen]
formatters = ["ruff-check", "ruff-format"]
```

The new `standard-py310-20260909` through `standard-py314-20260909` and corresponding `practical` presets include
builtin. Presets also change other generation settings, so explicitly selecting only a formatter is the smallest
migration when you want to preserve those settings. Override a new preset with
`--formatters ruff-check ruff-format` or `--formatters black isort` as appropriate. Earlier dated presets do not
supply a formatter and continue to use the implicit Black/isort default and its warning unless overridden.
An explicit empty formatter list in pyproject or the Python API disables standard formatting.

Explicit formatter selection does not pin formatter versions, configuration or every byte of generated output.
Custom templates can continue using Black/isort or Ruff; builtin does not exhaustively validate custom-template
output. See [Formatter behavior](formatter-behavior.md) for scope and configuration details.

## Separate notices

| Notice | When it is emitted |
| --- | --- |
| Default formatter change | Effective formatters are unspecified, including older presets and custom formatters added to the implicit pipeline. |
| Black/isort optional installation | Black or isort is explicitly selected, including through resolved configuration or a formatter-supplying preset. Not also emitted for an implicit pipeline. |
| Old Black/isort support | A selected formatter is below its proposed future minimum, whether the pipeline was implicit or explicit. Merely having it installed does not warn. |
| Old runtime Pydantic support | DCG generates code using Pydantic below 2.8.2 in its own runtime, even when generating dataclasses, TypedDict or msgspec models. |

Each notice is registered separately as an active `FutureWarning`. The default formatter notice retains its
original 0.52.0 history; the new notices are recorded for the planned 0.78.0 release. The registry contains the
[exact short messages](deprecations.md). Migration warnings are deduplicated within one CLI invocation, including
batch and watch regeneration. Non-generation operations such as help, version and listings do not emit them or
load dependency modules just to check versions. The Python API respects standard warning filters, including
`always`, `ignore` and `error`; the CLI continues to support `--disable-warnings`. Warnings do not enter stdout or
generated code.

## Prepare for optional Black/isort installation

The new `[black]` and `[isort]` extras currently repeat the mandatory dependency ranges and Emscripten markers;
`[all]` includes them. They prepare a stable installation spelling for the later optionalization. You do not need
to reinstall these currently mandatory dependencies merely because of this release.

For continued Black/isort use after the future default and installation changes, select both the dependencies and
formatter pipeline:

```bash
uvx --from 'datamodel-code-generator[black,isort]' \
  datamodel-codegen --input schema.json --output models.py \
  --formatters black isort
```

For a Python API or shared environment, declare `datamodel-code-generator[black,isort]` in your environment's
dependencies and pass `formatters=[Formatter.BLACK, Formatter.ISORT]`. Declare only the extras you actually use.
`uvx` can isolate the CLI's dependencies from application dependencies. Shared environments and API use remain
supported migration scenarios; application constraints alone do not imply a fixed long transition period.

## Proposed future dependency floors

These are follow-up targets, not changes to this release's requirements:

| Dependency | Current requirement | Proposed later requirement |
| --- | --- | --- |
| isort | `>=4.3.21,<9` | `>=6,<9` (keep 6, 7 and 8) |
| Black | `>=19.10b0` | `>=24.3.0` |
| Runtime Pydantic, Python <3.14 | `>=2,<3` | `>=2.8.2,<3` |
| Runtime Pydantic, Python >=3.14 | `>=2.12,<3` | Unchanged |

No other dependency floor changes are proposed here. The generator continues using its existing numeric runtime
compatibility boundaries; no version selection is inferred from string ordering. The notice comparisons use
numeric release components and distinguish prereleases at the final-release boundary without relying on an
undeclared packaging dependency.

No removal version, date, or transition period has been specified. This release only announces the proposed
changes; there is no date-based removal or automatic requirement change.

Public lock searches included many old DCG versions and false-positive mentions in other packages' extras. Counts
from those searches are not user counts or impact estimates. Recent public usage includes explicit Black/isort,
builtin and Ruff. This is a migration policy informed by public examples and maintenance cost, not evidence that
almost nobody will be affected.

## Runtime Pydantic versus generated-code Pydantic

The proposed 2.8.2 floor applies to **DCG's runtime**, not the environment that consumes generated code.
`--target-pydantic-version` values such as 2, 2.11 and 2.12 remain unchanged. Raising the runtime floor alone is not
a reason to delete every existing compatibility branch.

The following classification reflects the current source. Emitted code never depends on the installed Pydantic:
`model/pydantic_v2/version.py` keys every generated-code boundary on `--target-pydantic-version`. Leaving it unset
gives the newest supported feature forms, while config key naming keeps its existing rule (`populate_by_name` unless
the target is `2.11` or later); `2` means Pydantic 2.0+ compatible forms. Raising DCG's runtime floor therefore cannot
change generated output.

| Area | Classification and current effect | Follow-up test responsibility |
| --- | --- | --- |
| DCG configuration/internal Pydantic models | Internal runtime compatibility: validating generator configuration and constructing internal model/field objects relies on the installed Pydantic. | Keep the minimum DCG-runtime configuration and generation tests; reassess only when the actual floor changes. |
| Dataclass alias, 2.4 boundary | Generated-code compatibility: targets before 2.4 (`2`) use the `DataModelFieldBackport` alias fallback, which emits `Field` assignments with validation and serialization aliases for non-identifier aliases. | Generate alias cases on the new DCG runtime and execute them separately on supported consumer versions before removing fallback behavior. |
| `deprecated` / `json_schema_extra`, 2.7 boundary | Generated-code compatibility: targets before 2.7 (`2`) emit `deprecated` in `json_schema_extra`; unset, `2.11` and `2.12` emit `Field(deprecated=...)`. | Check emitted metadata and execute/schema-inspect generated models in consumer environments. |
| `regex_engine`, 2.5 boundary | Generated-code compatibility: the emitter adds `python-re` for pattern syntax no pydantic-core release supports, such as lookaround and backreferences, detected statically (see [Regular Expression Patterns](jsonschema.md#regular-expression-patterns)) for every target. Old generated models may still reject those expressions at runtime. | Retain regex pattern generation and consumer-runtime validation/known exclusions, independent of the DCG runtime floor. |
| RootModel, dictionary keys, forward references, 2.8 boundary | Generated-code compatibility: targets before 2.8 (`2`) sort models used as dictionary keys before their users; unset, `2.11` and `2.12` keep schema order. | Retain recursive RootModel and dictionary-key import/execution cases across generator and consumer environments. |
| `StringConstraints`, 2.1 boundary | Generated-code compatibility: targets before 2.1 (`2`) emit `Annotated[str, Field(...)]` for constrained strings with `--use-annotated`; unset, `2.11` and `2.12` emit `StringConstraints`. | Generate both forms on every DCG runtime and execute them on supported consumer versions. |
| Dataclass reference aliases, 2.10 boundary | Generated-code compatibility: targets before 2.10 (`2`) emit `Alias: TypeAlias = Model` for `pydantic_v2.dataclass` aliases of models; unset, `2.11` and `2.12` emit `TypeAliasType`. | Generate both forms on every DCG runtime and execute them on supported consumer versions. |
| Discriminator / Union | Both: internal schema/model construction and emitted union/discriminator annotations must agree; existing force-optional and payload cases exercise runtime-sensitive behavior. | Keep generation assertions and instantiate generated union cases on separately pinned consumer versions. |
| `additionalProperties` | Both: schema interpretation plus emitted typed extras, recursive references and runtime validators affect consumer behavior. | Preserve schema/alias/recursive extra-property tests and verify generated validation, rather than treating all failures as DCG-runtime-only. |
| Payload and generated-code execution | Both are currently exercised together in pinned environments: generated code is imported and validated using the same environment's Pydantic. | Split producer and consumer environments where necessary to retain old consumer coverage after raising DCG's own minimum. |

Current dedicated environments include `pydantic200` (2.0.0), `pydantic20` (2.0.3), `pydantic25` (2.5.3), and
`pydantic213` (2.13.4). Separate payload environments include `py311-payload-runtime-min` (Pydantic 2.0.3 and msgspec
0.18), `py314-payload-runtime-min` (Pydantic 2.12 and msgspec 0.20), and their latest counterparts. The payload and
execution suites contain runtime-specific exclusions and boundary expectations; they cannot be dropped just
because the generator's own floor increases. These environments and compatibility implementations are retained.

Future work should distinguish a minimum **producer** test running DCG from **consumer** tests that execute its
saved output in another Pydantic environment. First preserve alias, metadata, regex, recursive RootModel,
discriminator/Union, additional-properties and payload behavior; then identify which internal-only branches can
actually be removed. Changing target-version semantics would require a separate, explicit decision.

## Follow-up changes remain separate

1. Change the implicit default to builtin, retaining explicit Ruff and Black/isort choices.
2. Remove Black/isort from mandatory dependencies, retaining the extras and explicit dependency errors.
3. Raise the runtime dependency floors and reassess compatibility code and producer/consumer tests.

None of these three changes is performed by this preparation release.
