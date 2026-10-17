# Option recommendations

Analyse a schema and get the `datamodel-codegen` options that suit it, starting from the latest dated preset.

!!! warning "Experimental"
    `--recommend-options`, its rules, the report, and its JSON payload are experimental and may change as the rules
    are calibrated on more schemas.

A [preset](presets.md) is a good general starting point, but some choices depend on the shape of your schema: how
inline models should be named, whether titles make good class names, whether inline enums should become `Literal`
types, or whether repeated inline objects should be reused. `--recommend-options` reads your schema, measures the
signals that drive these choices, and prints:

- the latest dated preset to start from,
- the options to add on top of it, each with the rule that chose it, the reason with counts from your schema, caveats,
  and the effect measured on public schemas,
- options worth considering when the schema has signals for and against them,
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

The command prints the report and exits. It does not generate models, write files, or change your configuration.

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
Option recommendations (experimental; quick mode; rules 2026-10-17)

Schema  OpenAPI 3.1.0
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

Start from  --preset practical-py314-20261017
  already sets --use-title-as-name   [titles.names-from-unique-titles]

Add (1 option)
  --use-tag-as-union-variant-name   [unions.name-variants-after-tags]
      Inline oneOf/anyOf branches can be named after a tag or key (branches with a tag value no
      sibling shares: 2; objects with one required key: 0); otherwise they are named after their
      parent and a number.
      caveat: Tags that only a referenced schema declares through allOf are not used (branches: 0;
      #4839).
      caveat: With --naming-strategy full-path, a single-key branch whose value is an object repeats
      the key (FieldValueFieldValue).
      measured: OpenAI API description (59 tagged, 10 key branches): preset 797 -> 759 problem names
      (ThreadStreamEvent1 -> ThreadStreamEventThreadCreated)
      measured: GitHub REST API description (1 tagged, 4 key branches): preset 1808 -> 1805
      (Changes23 -> ChangesBody)
      measured: SchemaStore workflow schema (5 tagged): 49 -> 44 (WorkflowDispatchInput1 to 5 ->
      WorkflowDispatchInputString, Boolean, ...)
      measured: with full-path on the GitHub and OpenAI descriptions: no change in problem names;
      schemas without tagged or key branches: no change

Notes
  - The rules were calibrated on the 20261015 presets.

Command
  datamodel-codegen --input titled_variants.yaml --preset practical-py314-20261017 --target-python-version 3.14 --use-tag-as-union-variant-name

pyproject.toml
  [tool.datamodel-codegen]
  input = "titled_variants.yaml"
  preset = "practical-py314-20261017"
  target-python-version = "3.14"
  use-tag-as-union-variant-name = true
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: recommend-options.report -->

The counts, presets and options come from your schema and the installed version. The report has these sections:

| Section | Meaning |
|---|---|
| Schema | The signals the rules read: components, inline objects and enums, shared property names, repeated shapes, titles, unions and their tags, key style. |
| Start from | The latest dated preset of the family (`standard` or `practical`) that needs the fewest extra options, and the rules it already satisfies. A family that cannot express a rule's value is listed with `not`. |
| Add | Options to add on top of the preset. Each names its rule in brackets, the reason, `caveat:` lines for known limits (with issue numbers), and `measured:` lines with the effect seen on public schemas. |
| Consider | Options the schema has signals for and against (`against:`). Decide from your schema; they are not in the command. |
| Not recommended | Options a rule argues against for this schema. |
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
classes, so review imports of the generated models before switching an existing project.

`--http-headers` and `--http-query-parameters` are not repeated in the printed command, and URLs are shown redacted;
add them again when you run it.

## JSON output

For tools and coding agents, add `--output-format json`:

```bash
datamodel-codegen --input openapi.yaml --input-file-type openapi --recommend-options --output-format json
datamodel-codegen --output-format-json-schema recommend-options
```

The payload has `kind: "recommend-options"` and contains the `signals`, the starting preset under `baseline`, the
`recommendations`, `consider`, `not_recommended` and `disagreements` entries (each with its `rule`, `set` config
values and CLI `arguments`), `notes`, the full command as `arguments`, the `config` table, and the text report as
`content`. `rules_revision` identifies the rule data that produced it.

## What `quick` does

`--recommend-options` with no value runs `quick`, the only mode in this release:

- It reads the input once and measures the schema signals; no models are generated, so it is fast even on large
  specifications.
- It applies the rules to the signals and computes the options on top of the latest preset for your output model type
  and target Python version.
- Schema rules cover OpenAPI, AsyncAPI, and JSON Schema documents. For other input types, a directory input, or
  `--input-model`, it recommends only the preset.
- When `--target-python-version` is not set, it assumes the default target and says so in the notes.

It cannot be combined with `--check`, `--watch`, `--diff-against`, `--all-jobs`, more than one `--job`,
`--generate-pyproject-config`, or `--generate-cli-command`.

## How recommendations are maintained

- **Rules are data.** The rules ship in the package as `recommender_rules.json`. Each rule maps schema signals to
  option values and carries its reason, caveats, and evidence. Rule values are validated through the same model as
  presets, so a rule cannot name an option that does not exist.
- **Every option is registered.** Each option has an entry in the rule data: either the rules that decide it or the
  reason the recommender leaves it to you (input/output paths, the target, runtime behaviour, formatting, and so on).
  A new option cannot be released without that decision.
- **Re-calibration.** The rules were derived from measurements of class names on public API descriptions and
  schemas. They are re-checked against a set of public schemas over time, and rule changes go through normal reviewed
  pull requests. The report notes when the rules were calibrated on older presets than the one it recommends.
- **Presets stay immutable.** A dated preset never changes. An option that the rules recommend for most schemas is
  proposed for a new dated preset; once it exists, the recommendation starts from it and stops listing that option.

## FAQ

**Does it generate code?**
No. `quick` only reads the schema and evaluates the rules.

**Does it change my `pyproject.toml`?**
No. It prints a command and a configuration block; you decide what to apply.

**Will it override options I set?**
No. Explicit options are kept, and disagreements are reported under "Your configuration".

**Is the output stable?**
The same input, options, and installed version give the same report. The report does not depend on the machine or
on installed libraries. Upgrading datamodel-code-generator can change the recommendation when a new preset or a rule
revision is released.

**Why is an option I expected missing?**
Either the preset already sets it (listed under "Start from"), the schema does not show the signal its rule needs,
or the option is a project decision the recommender never makes. Check [Presets](presets.md) and the
[CLI Reference](cli-reference/index.md) for the full list of options.
