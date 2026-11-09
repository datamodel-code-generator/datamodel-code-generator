# Troubleshooting

## Symptom: The command is not found

Cause: The package is not installed or the user ran the package name as the command.

Fix:

```bash
uvx --from datamodel-code-generator datamodel-codegen --help
```

Notes:

* The package name is `datamodel-code-generator`.
* The CLI command is `datamodel-codegen`.

## Symptom: JSON or YAML was parsed as sample data but it was really a schema

Cause: The wrong `--input-file-type` value was used.

Fix:

```bash
uvx --from datamodel-code-generator datamodel-codegen \
  --input schema.yaml \
  --input-file-type jsonschema \
  --output models.py
```

or:

```bash
uvx --from datamodel-code-generator datamodel-codegen \
  --input openapi.yaml \
  --input-file-type openapi \
  --output models.py
```

Notes:

* Inspect the file before choosing raw sample data mode.

## Symptom: Raw sample data produced surprising optional fields or unions

Cause: Types were inferred from incomplete or inconsistent examples.

Fix:

* Use a fuller sample.
* Prefer the real JSON Schema or OpenAPI spec.
* Review generated optionality and unions.
* Use documented flags only after checking help.

Notes:

* Sample inference cannot know fields missing from the sample.

## Symptom: oneOf or anyOf produced unexpected unions

Cause: The schema allows multiple alternatives.

Fix:

```bash
uvx --from datamodel-code-generator datamodel-codegen \
  --input schema.json \
  --input-file-type jsonschema \
  --union-mode smart \
  --output-model-type pydantic_v2.BaseModel \
  --output models.py
```

Notes:

* Verify `--union-mode` values against current help.

## Symptom: allOf did not merge fields as expected

Cause: `allOf` merge behavior depends on schema shape and merge mode.

Fix:

```bash
uvx --from datamodel-code-generator datamodel-codegen \
  --input schema.json \
  --input-file-type jsonschema \
  --output models.py \
  --allof-merge-mode all
```

Notes:

* Try `--allof-class-hierarchy` when class inheritance is the desired output.

## Symptom: Import errors after directory output

Cause: Module exports or circular imports may not match the application's import style.

Fix:

```bash
uvx --from datamodel-code-generator datamodel-codegen \
  --input schemas/ \
  --output models/ \
  --all-exports-scope children
```

Notes:

* For large trees, try recursive exports only after child exports import cleanly.

## Symptom: Duplicate class names or export collisions

Cause: Multiple schemas produce the same class name.

Fix:

```bash
uvx --from datamodel-code-generator datamodel-codegen \
  --input schemas/ \
  --output models/ \
  --all-exports-scope recursive \
  --all-exports-collision-strategy minimal-prefix
```

Notes:

* Prefer generator naming flags before editing generated classes by hand.

## Symptom: Enum member names look wrong

Cause: Enum values may not be valid Python identifiers or may conflict with naming conventions.

Fix:

* Check enum-related flags in current help.
* Use the exact supported enum naming flag.
* Do not rename generated enum members by hand unless the user accepts divergence from the schema.

Notes:

* Current help supports `--capitalise-enum-members` and `--capitalize-enum-members`.

## Symptom: Fields with `class`, `from`, or special characters changed names

Cause: Python identifiers cannot use reserved words or invalid characters.

Fix:

```bash
uvx --from datamodel-code-generator datamodel-codegen \
  --input schema.json \
  --input-file-type jsonschema \
  --output models.py \
  --special-field-name-prefix field
```

For camelCase to snake_case:

```bash
uvx --from datamodel-code-generator datamodel-codegen \
  --input schema.json \
  --input-file-type jsonschema \
  --output models.py \
  --snake-case-field
```

Notes:

* Keep aliases unless the project explicitly does not want them.

## Symptom: Generated output changes on every run

Cause: Timestamp headers or environment-dependent formatting.

Fix:

```bash
uvx --from datamodel-code-generator datamodel-codegen \
  --input schema.json \
  --input-file-type jsonschema \
  --output models.py \
  --disable-timestamp
```

Notes:

* Set `--formatters` explicitly when the project needs reproducibility.

## Symptom: Large specs are slow or produce too much duplicate code

Cause: Large schemas can create many repeated models.

Fix:

```bash
uvx --from datamodel-code-generator datamodel-codegen \
  --input openapi.yaml \
  --input-file-type openapi \
  --output models/ \
  --reuse-model \
  --disable-warnings
```

Notes:

* Use directory output when a single file becomes too large.

## Symptom: Remote URL or remote refs fail

Cause: Missing HTTP extra, authentication, local ref path, TLS, or network access.

Fix:

```bash
uvx --from 'datamodel-code-generator[http]' datamodel-codegen \
  --url https://example.com/openapi.yaml \
  --input-file-type openapi \
  --output models.py
```

For authenticated endpoints:

```bash
uvx --from 'datamodel-code-generator[http]' datamodel-codegen \
  --url https://example.com/openapi.yaml \
  --http-headers "Authorization:Bearer $TOKEN" \
  --input-file-type openapi \
  --output models.py
```

Notes:

* Use `--http-ignore-tls` only in trusted development or testing environments.

## Symptom: Generated code does not match installed Pydantic version

Cause: Output model type does not match project dependency versions.

Fix:

```bash
python -c "import pydantic; print(pydantic.VERSION)"
```

Notes:

* Default to `pydantic_v2.BaseModel` for new projects.
* Regenerate with the matching supported output model type.

## Symptom: Type checker complains about constraints

Cause: Some constrained types or field metadata may not be understood by the configured type checker.

Fix:

```bash
uvx --from datamodel-code-generator datamodel-codegen \
  --input schema.json \
  --input-file-type jsonschema \
  --output models.py \
  --field-constraints
```

or:

```bash
uvx --from datamodel-code-generator datamodel-codegen \
  --input schema.json \
  --input-file-type jsonschema \
  --output models.py \
  --use-annotated
```

Notes:

* Verify both flags against current help.
