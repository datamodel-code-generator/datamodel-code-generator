# `pragma: no cover` inventory

This inventory tracks coverage pragmas around refactoring targets. It is kept in
the tree because pull request descriptions for this work are intentionally left
empty.

## Current counts

Generated with:

```bash
rg -n "pragma: no cover" src/datamodel_code_generator/parser/base.py | wc -l
rg -n "pragma: no cover" src/datamodel_code_generator/parser/jsonschema.py | wc -l
rg -n "pragma: no cover" src/datamodel_code_generator/imports.py | wc -l
rg -n "pragma: no cover" src/datamodel_code_generator | wc -l
```

| Path | Remaining sites | Notes |
| --- | ---: | --- |
| `src/datamodel_code_generator/parser/base.py` | 54 | Defensive branches, error paths, and ordering-sensitive paths that should be moved verbatim unless first covered by characterization tests. |
| `src/datamodel_code_generator/parser/jsonschema.py` | 92 | Defensive branches, schema-version and input-shape error paths, and parser dispatch fallbacks. |
| `src/datamodel_code_generator/imports.py` | 0 | The dotted import removal branch is covered by `tests/test_imports.py`. |
| `src/datamodel_code_generator` | 375 | Repository-wide executable pragma total after the covered sites were removed. |

## Covered sites

The following previously excluded sites are now covered by characterization
tests and no longer carry coverage pragmas:

- `datamodel_code_generator.imports.Imports.remove` dotted-import branch.
  `tests/test_imports.py` pins public behavior for counter decrement,
  cleanup of the `None` import bucket, and `reference_paths` removal.
- `datamodel_code_generator.parser.base._find_base_classes`.
- `datamodel_code_generator.parser.base._find_field`, including the no-match
  return path.
- `datamodel_code_generator.parser.base._copy_data_types`, including reference,
  nested, and plain `DataType` copy arms.
- `datamodel_code_generator.parser.base.Parser.__override_required_field`,
  including reference-backed inherited fields, nested inherited fields, plain
  inherited fields, default preservation with
  `apply_default_values_for_required_fields=True`, and removal of unmatched
  placeholders.

## Refactoring guidance

- Treat remaining pragmas in `parser/base.py` and `parser/jsonschema.py` as
  review checkpoints before moving code.
- Keep defensive-unreachable and environment/version-gated paths verbatim unless
  a focused characterization test is added first.
- When a pragma is removed, add a test that observes stable public behavior or
  an internal invariant already used by the parser pipeline.
- When pragmas are added or removed, update this document's counts and covered
  site list; `parser/base.py` and `parser/jsonschema.py` are common places to
  recheck.
