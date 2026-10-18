# Option recommendations

Analyse a schema and get the `datamodel-codegen` options that suit it, starting from the latest dated preset.

!!! warning "Experimental"
    `--recommend-options`, its rules, the report, and its JSON payload are experimental and may change as the rules
    are calibrated on more schemas.

A [preset](presets.md) is a good general starting point, but some choices depend on the shape of your schema: how
inline models should be named, whether titles make good class names, whether inline enums should become `Literal`
types, or whether repeated inline objects should be reused. `--recommend-options` reads your schema, measures the
signals that drive these choices, generates the candidate configurations in memory to compare the class names they
give your schema, and prints:

- the latest dated preset to start from,
- the options to add on top of it, each with the rule that chose it, the reason with counts from your schema, caveats,
  the effect measured on public schemas, and the class names it changes in your schema (before -> after),
- options worth considering when the schema has signals for and against them,
- optional validation and policy options, such as `--strict-types`, which stay out of the command because they depend
  on what your project accepts,
- the compared candidates and the class names that change from your current configuration,
- where your existing configuration differs from the rules,
- a ready command and a `pyproject.toml` block.

## Run it

Add `--recommend-options` to the command you would use to generate models:

```bash
datamodel-codegen --input openapi.yaml --input-file-type openapi --recommend-options
```

Pass the options you have already decided on, such as `--output-model-type` and `--target-python-version`, so the
recommendation starts from the matching preset. When they are in `pyproject.toml`, run the command from the project
directory; `--profile NAME` and a single `--job NAME` select a profile or a job as they do for generation.

The command prints the report and exits. It does not write files or change your configuration.

To compare names in the layout you generate, pass `--output` as you would for generation: a file path selects one
module, a directory a package. Nothing is written there. Generating every candidate takes about as long as generating
your models once per candidate, so a schema estimated above 3000 models gets the rules only, and the notes give the
command that compares the candidates anyway (see [How candidates are compared](#how-candidates-are-compared)).

## When generation suggests it

When many generated classes are numbered collisions, a command line generation ends with a `NamingHintWarning`
(experimental) that points here:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: recommend-options.naming-hint -->
<!-- fmt: off -->

```text
NamingHintWarning: 11 of 24 class names are numbered collisions (Metadata1...Metadata11). Add --recommend-options to this command to see naming options for this schema (experimental).
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: recommend-options.naming-hint -->

A class name is a numbered collision when datamodel-codegen added the number because the name without it was taken,
such as `Metadata1` beside `Metadata`, or `Status1` when `--collapse-root-models` keeps a `Status` root model's name
without generating a class for it. Digits from the schema, such as `Level1` and `Level2`, and the suffix of
`--duplicate-name-suffix` do not count. The hint needs at least 10 numbered collisions that make at least one in five
classes, and the example is the largest family of them. A command shows it at most once, also when `watch = true` in
`pyproject.toml` regenerates the models, and only when `--recommend-options` accepts the command: not with `--watch`,
`--all-jobs`, or more than one `--job`.

It is left out when you already chose how classes are named, with `--naming-strategy`, `--model-name-map`, or the
deprecated `--parent-scoped-naming`, on the command line or in `pyproject.toml` (`naming-strategy = "numbered"` keeps
the numbered names without the hint), and with `--check`, `--diff-against`, `--output-format json`, and
`--disable-warnings`. When warnings are turned into errors, the hint is dropped and the run goes on. `generate()` never
shows it. The hint never changes the generated files, the result of `--check`, or the exit code.

## Read the report

For example, for this schema (`titled_variants.yaml`):

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: recommend-options.schema -->
<!-- fmt: off -->

```yaml
openapi: 3.1.0
info:
  title: Travel API
  version: 1.0.0
paths: {}
components:
  schemas:
    Booking:
      type: object
      properties:
        passenger:
          type: string
        payment:
          oneOf:
            - title: Card
              type: object
              properties:
                object:
                  const: card
                number:
                  type: string
            - title: Bank Account
              type: object
              properties:
                object:
                  const: bank_account
                iban:
                  type: string
    Trip:
      type: object
      properties:
        origin:
          type: string
        destination:
          type: string
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: recommend-options.schema -->

`datamodel-codegen --input titled_variants.yaml --target-python-version 3.14 --recommend-options` prints:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: recommend-options.report -->
<!-- fmt: off -->

```text
Option recommendations (experimental; full mode; rules 2026-10-18)

Schema  OpenAPI 3.1.0 (OpenAPI scopes: schemas)
  components                       2, 0 operations
  inline objects / enums           2 / 0, nesting depth 1
  shared property names            0 (none)
  inline names = component names   0
  repeated inline objects / enums  0 of 0 shapes / 0 of 0 value sets
  titled components                0: 0 shared titles (none), 0 equal another key, 0 equal their own key
  inline titles                    2 objects, most repeated 1 (Card x1, Bank Account x1)
  unions                           1 oneOf/anyOf, 0 with a discriminator, 2 inline variants
  union branch tags                2 tagged, 0 single required key, 0 tagged only through allOf $ref, 0 copy their parent
  component keys                   PascalCase (2 of 2); 0 dotted in 0 namespaces
  constraint keywords              uniqueItems 0, patternProperties 0 (0 beyond a typed map), multipleOf 0, OpenAPI 3.0 nullable 0, boolean exclusive bounds 0
  formats                          0 (none), decimal 0; int32/int64 0, ranges not enforced
  integer/number/boolean schemas   0

Start from  --preset standard-py314-20261017

Add (1 option)
  --use-title-as-name   [titles.names-from-unique-titles]
      Inline union variants carry titles that no other schema uses (titled variants: 2, e.g. Card
      x1, Bank Account x1). Titles name them after what they are, instead of their parent property
      and a number.
      measured: Train Travel API: BookingPayment.source variants became Card and BankAccount instead
      of Source and Source1
      trial: preset -> recommended - union-variant-names: classes 4 -> 4, problem names 1 -> 0; 2 of
      4 classes matched by schema location renamed (0 only renumbered), 0 removed, 0 added, e.g.
        Payment -> Card   at components/schemas/Booking > payment > oneOf 0
        Payment1 -> BankAccount   at components/schemas/Booking > payment > oneOf 1

Left out after the trial (the rules recommend these)
  --use-tag-as-union-variant-name   [unions.name-variants-after-tags]
      Inline oneOf/anyOf branches can be named after a tag or key (branches with a tag value no
      sibling shares: 2; objects with one required key: 0); otherwise they are named after their
      parent and a number.
      measured: OpenAI API description (59 tagged, 10 key branches): preset 797 -> 759 problem names
      (ThreadStreamEvent1 -> ThreadStreamEventThreadCreated)
      measured: GitHub REST API description (1 tagged, 4 key branches): preset 1808 -> 1805
      (Changes23 -> ChangesBody)
      measured: SchemaStore workflow schema (5 tagged): 49 -> 44 (WorkflowDispatchInput1 to 5 ->
      WorkflowDispatchInputString, Boolean, ...)
      measured: with full-path on the GitHub and OpenAI descriptions: no change in problem names;
      schemas without tagged or key branches: no change
      trial: recommended - union-variant-names -> recommended: classes 4 -> 4, problem names 0 -> 0;
      0 of 4 classes matched by schema location renamed (0 only renumbered), 0 removed, 0 added

Trial  5 candidates generated in memory without formatting; nothing written
  candidate                          classes   problems  numbered  long  doubled  displaced  median  unconstrained
  current                                  4  1 (25.0%)         1     0        0          0       7              0
  preset                                   4  1 (25.0%)         1     0        0          0       7              0
  recommended                              4   0 (0.0%)         0     0        0          0       7              0
  recommended - title-source               4   0 (0.0%)         0     0        0          0      11              0
  recommended - union-variant-names        4   0 (0.0%)         0     0        0          0       7              0   selected
  current: the configuration as set, without the preset; never selected
  unconstrained: fields that dcg warns it generates without some of their constraints

Changes from the current configuration (current -> recommended - union-variant-names)
  2 of 4 classes matched by schema location renamed (0 only renumbered), 0 removed, 0 added, e.g.
    Payment -> Card   at components/schemas/Booking > payment > oneOf 0
    Payment1 -> BankAccount   at components/schemas/Booking > payment > oneOf 1

Command
  datamodel-codegen --input titled_variants.yaml --preset standard-py314-20261017 --target-python-version 3.14 --use-title-as-name

pyproject.toml
  [tool.datamodel-codegen]
  input = "titled_variants.yaml"
  preset = "standard-py314-20261017"
  target-python-version = "3.14"
  use-title-as-name = true
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: recommend-options.report -->

The counts, presets and options come from your schema and the installed version. The report has these sections:

| Section | Meaning |
|---|---|
| Schema | The signals the rules read: components, inline objects and enums, shared property names, repeated shapes, titles, unions and their tags, key style, constraint keywords (`uniqueItems`; `patternProperties`, with how many a typed map cannot express; `multipleOf`; `nullable` in OpenAPI 3.0 and AsyncAPI 2 documents; boolean exclusive bounds), the formats generation maps to a type (`int32`/`int64` ranges are not enforced), and the integer, number and boolean schemas. |
| Start from | The latest dated `standard` preset for your output model type and target Python version, and the rules it already satisfies. It is the `practical` preset only when you configured a practical preset, or when no option can set a value a rule needs on the standard preset (the notes then say which). |
| Add | Options to add on top of the preset. Each names its rule in brackets, the reason, `caveat:` lines for known limits, `measured:` lines with the effect seen on public schemas, and a `trial:` line with the candidates without and with it, their class and problem-name counts, and up to three class names it changes in your schema. |
| Left out after the trial | Options the rules recommend that the selected candidate does without, because your schema's class names score better without them. |
| Consider | Options the schema has signals for and against (`against:`). Decide from your schema; they are in the command only when the candidate with them scores best. |
| Optional (validation) | Options for checks the schema declares that the models leave out by default, such as `--generate-schema-validators` for `uniqueItems` and for `patternProperties` a typed map cannot express (beside declared properties, with several patterns, or with other keys allowed) with Pydantic v2 models. They are experimental or change runtime behaviour, so they are not in the command; validate sample data with the generated models first. |
| Optional (policy) | Options that depend on what input your project accepts rather than on the schema, such as `--strict-types`, which rejects the strings and booleans Pydantic's lax mode coerces into numbers and booleans. They are never in the command. |
| Not recommended | Options a rule argues against for this schema. |
| Trial | The compared candidates with their class counts, problem names, median name length, and `unconstrained`: the fields dcg warns it generates without some of their constraints, counted per module in the generation each candidate keeps; `selected` marks the recommended one. |
| Changes from the current configuration | The class names the recommended configuration changes from the configuration you run now, matched by schema location. |
| Your configuration | Options you set that differ from the rules, and a newer preset when you set an older one. Your values are kept. |
| Notes | Assumptions, such as the target Python version used when you did not set one. |
| Command / pyproject.toml | The same recommendation as a command and as configuration. |

## Apply it

- **No `pyproject.toml` configuration yet:** copy the `pyproject.toml` block into your `pyproject.toml`, add `output`,
  and run `datamodel-codegen`. Or run the printed command with `--output`.
- **Existing `[tool.datamodel-codegen]` table:** the report shows `pyproject.toml: keys to add or change` with only
  the keys to add or change. Keys the recommender did not decide are never shown as removed. With `--profile` or `--job`,
  the changes are for that profile or job table.
- **Preset given on the command line:** the printed command includes the additions.

Options you set on the command line or in `pyproject.toml` always stay as you set them. When a rule disagrees, the
report lists it under "Your configuration" instead of changing it. Adopting a new naming option can rename generated
classes: "Changes from the current configuration" counts them and shows examples, so review imports of the generated
models before switching an existing project.

`--http-headers` and `--http-query-parameters` are not repeated in the printed command, and URLs are shown without
their user information, query, and fragment; add them again when you run it. A remote input is read once, checked
against the remote reference lock as generation checks it (`--locked`, `--lockfile`), and the lock is never written.

## JSON output

For tools and coding agents, add `--output-format json`:

```bash
datamodel-codegen --input openapi.yaml --input-file-type openapi --recommend-options --output-format json
datamodel-codegen --output-format-json-schema recommend-options
```

The payload has `kind: "recommend-options"` and contains the `mode` that ran (`full` when candidates were compared),
the `signals`, the starting preset under `baseline`, the `recommendations`, `left_out`, `consider`, `not_recommended`
and `disagreements` entries (each with its `rule`, `set` config values, CLI `arguments`, and the `trial` candidates
and class-name `changes` that measured it), the optional `validation` and `policy` entries (never in `arguments` or
`config`), the compared candidates under `trial` (each with its `metrics`, its `dropped_constraint_warnings`, and
`stability` with `--diff-against`, plus the `ranking`, the `selected` label, the `migration` from the current
configuration, and the `dropped` (candidates beyond the limit) and `remeasured` counts), `notes`, the full command as
`arguments`, the `config` table, and the text report as `content`.
`rules_revision` identifies the rule data and `trial.metrics_version` the class-name metrics that produced it.

## Ask an LLM with the recommendation

Add `--recommend-options` to a [`--generate-prompt`](llm-integration.md) command to include the report in the prompt,
under "Schema analysis and recommended options", and ask the LLM to start from the recommended command and give a
reason for every option it changes. With `--output-format json`, the prompt payload has the recommendation payload as
`recommendation`.

```bash
datamodel-codegen --input openapi.yaml --input-file-type openapi --recommend-options \
  --generate-prompt "Which options keep the class names stable across schema versions?"
```

## What it measures

- It reads the input once and measures the schema signals without generating models: how the schemas are named and
  nested, and which constraint keywords and formats they use. Keywords beside a `$ref` count where the document's
  version applies them (JSON Schema 2019-09 and later, OpenAPI 3.1 and later, AsyncAPI 3).
- For OpenAPI, it measures what your `--openapi-scopes` generate: component schemas with the `schemas` scope
  (otherwise only those the operations reference), and request, response, parameter, webhook, and request body
  schemas with those scopes, filtered by `--openapi-include-paths`; parameter schemas follow every walked operation.
  For AsyncAPI, it measures component schemas and the payload and header schemas of messages in components and
  channels, including multi-format schema objects.
- Generation reads the `definitions` of a Swagger 2.0 document with `--input-file-type jsonschema`, so that is how it
  is analysed. With `--input-file-type openapi`, generation finds no models in it; the report says so and prints no
  command or `pyproject.toml` block.
- With `--generate-server` or `--generate-client`, it checks the target's options and applies the settings the target
  implies (such as the `api` scope and Python 3.11) as generation does, so the recommendation starts from a preset the
  target can use.
- It applies the rules to the signals and computes the options on top of the latest preset for your output model type
  and target Python version.
- Schema rules cover OpenAPI, AsyncAPI, and JSON Schema documents. For other input types, a directory input, or
  `--input-model`, it recommends only the preset.
- When `--target-python-version` is not set, it assumes the default target and says so in the notes.

It cannot be combined with `--check`, `--watch`, `--all-jobs`, more than one `--job`, `--generate-pyproject-config`,
`--generate-cli-command`, `--install-skill`, `--list-deprecations`, or `--list-experimental`.

## How candidates are compared

The recommender then generates these configurations of your command in memory, with formatting off, and reads the
class names each one gives your schema:

- **current**: the configuration as you run it now, without the recommended preset. It is measured for the changes
  from your configuration and never selected.
- **preset**: the latest dated preset with your options.
- **recommended**: the preset with the options the rules add.
- **recommended + DECISION**: the recommendation with each option the schema also argues against (Consider).
- **recommended - DECISION**: the recommendation without each option it adds.

With a preset already configured, your configuration is the starting point and is labelled `current`. A
configuration is generated once under its first label, and at most eight are compared; the last ones are left out
first, and the notes say so.

Each candidate's class names are measured like the naming benchmark the rules come from. A name is a problem when it
is numbered (`Status1`), contains a status code, is a generic word (`Item`, `Data`), is longer than 40 characters,
repeats a word the schema does not repeat (`ShapeShape`), takes a component's name from the component, or names an
inline model like an unrelated component. Candidates are ranked by, in order: fewer numbered or doubled names; fewer
other problem names, such as names over 40 characters; with `--diff-against`, fewer classes renamed between the
previous and the current schema; a shorter median name; then fewer fields that dcg warns it generates without some
of their constraints. Numbered names count as worse than long descriptive names:
`Event2` says nothing about the class, while a long name still describes it. Only when two candidates generate exactly
the same class names does the one with fewer added options rank first; other ties keep the order above. The best
candidate other than `current` is selected, and the command and `pyproject.toml` block carry its options.

The `trial:` line of each option compares the candidates without and with it on the selected configuration, so it
shows what that one option changes in the recommended command. When the selected candidate is not the rules'
recommendation, the trial also generates the selected configuration without or with each option it needs for those
lines; these rows are listed last and never selected.

Classes are matched by schema location. The locations in the examples describe where a class comes from: the document
path, then each inline step after `>` (a union branch reads `oneOf 0`, an array item `item`). They are not JSON
Pointers and cannot be used as `--model-name-map` keys; `--emit-model-metadata` lists the class names and their
source paths.

A candidate whose generation fails is listed as rejected with a one-line reason and never selected. If every
candidate fails, the options are the rules' recommendation alone. The candidates share the parsed input and fetch each
remote document once; they print nothing, and the notes say which input type the trial read when it was not given.
With `--generate-server` or `--generate-client`, the trial compares the models only.

Candidates are compared when the schema is estimated at up to 3000 models: its components, inline objects and enums,
and inline union variants, plus those of `--diff-against`. The limit is a model count, never a time, so the same
schema always gives the same report. Larger schemas, and inputs whose size cannot be estimated (input types without
schema rules, directories, `--input-model`), get the rules only; the notes give the command that compares the
candidates anyway, for every input type.

### Stability between schema versions

Pass the previous version of the schema with `--diff-against` (and `--output`, which selects the layout as for
generation) to also rank candidates by how many classes they rename between the versions:

```bash
datamodel-codegen --input openapi.yaml --diff-against openapi.previous.yaml --output models.py --recommend-options
```

Every candidate is then generated for both versions, and the Trial table gains a `renamed` column.

## How recommendations are maintained

- **Rules are data.** The rules ship in the package as `recommender_rules.json`. Each rule maps schema signals to
  option values and carries its reason, caveats, and evidence. Rule values are validated through the same model as
  presets, so a rule cannot name an option that does not exist.
- **Every option is registered.** Each option has an entry in the rule data: either the rules that decide it or the
  reason the recommender leaves it to you (input/output paths, the target, runtime behaviour, formatting, and so on).
  A new option cannot be released without that decision.
- **Calibration.** The rules were derived from measurements of class names on public API descriptions and
  schemas, and rule changes go through normal reviewed pull requests. The report notes when the rules were
  calibrated on other presets than the one it starts from.
- **Presets stay immutable.** A dated preset never changes. An option that the rules recommend for most schemas is
  proposed for a new dated preset; once it exists, the recommendation starts from it and stops listing that option.

## FAQ

**Does it generate code?**
It writes nothing. To compare candidates, it generates their models in memory and reads only the class names.

**Does it change my `pyproject.toml`?**
No. It prints a command and a configuration block; you decide what to apply.

**Will it override options I set?**
No. Explicit options are kept, and disagreements are reported under "Your configuration".

**Is the output stable?**
The same input, options, and installed version give the same report. The report does not depend on the machine or
on installed libraries, and whether candidates are compared depends on the schema size, not on time. Upgrading datamodel-code-generator can change the recommendation when a new preset or a rule
revision is released.

**Why is an option I expected missing?**
Either the preset already sets it (listed under "Start from"), the schema does not show the signal its rule needs,
the candidate without it scored better on your schema (listed under "Left out after the trial"), it is an optional
validation or policy option that stays out of the command (listed under "Optional"), or the option is a project
decision the recommender never makes. Check [Presets](presets.md) and the
[CLI Reference](cli-reference/index.md) for the full list of options.
