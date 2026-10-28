# Template Customization

Tune generated file headers, imports, decorators, templates, and formatting.

Options are grouped from shared CLI metadata and link back to their generated reference sections.

## Groups

| Group | Options | Description |
|-------|---------|-------------|
| [Custom Templates](#custom-templates) | 3 | Custom templates and extra template data. |
| [Generated Output](#generated-output) | 9 | Generated file headers and reproducible output. |
| [Imports](#imports) | 4 | Generated imports and type-checking import behavior. |
| [Output Formatting](#output-formatting) | 5 | Formatter selection, quote style, and string wrapping. |

## Custom Templates {#custom-templates}

Custom templates and extra template data.

| Option | Description |
|--------|-------------|
| [`--custom-template-dir`](../template-customization.md#custom-template-dir) | Use custom Jinja2 templates for model generation. |
| [`--extra-template-data`](../template-customization.md#extra-template-data) | Pass custom template variables via inline JSON or a JSON file path. |
| [`--validators`](../template-customization.md#validators) | Add custom field validators to generated Pydantic v2 models. |

## Generated Output {#generated-output}

Generated file headers and reproducible output.

| Option | Description |
|--------|-------------|
| [`--class-decorators`](../template-customization.md#class-decorators) | Add custom decorators to generated model classes. |
| [`--custom-file-header`](../template-customization.md#custom-file-header) | Add custom header text to the generated file. |
| [`--custom-file-header-mode`](../template-customization.md#custom-file-header-mode) | Choose how a custom file header combines with generated provenance. |
| [`--custom-file-header-path`](../template-customization.md#custom-file-header-path) | Add custom header content from file to generated code. |
| [`--disable-timestamp`](../template-customization.md#disable-timestamp) | Disable timestamp in generated file header for reproducible output. |
| [`--enable-command-header`](../template-customization.md#enable-command-header) | Include command-line options in file header for reproducibility. |
| [`--enable-generated-header-marker`](../template-customization.md#enable-generated-header-marker) | Include the @generated marker in file header for generated-code tooling. |
| [`--enable-version-header`](../template-customization.md#enable-version-header) | Include tool version information in file header. |
| [`--strict-dotted-module-names`](../template-customization.md#strict-dotted-module-names) | Require canonical Python identifiers when inferring dotted module paths. |

## Imports {#imports}

Generated imports and type-checking import behavior.

| Option | Description |
|--------|-------------|
| [`--additional-imports`](../template-customization.md#additional-imports) | Add custom imports to generated output files. |
| [`--no-use-type-checking-imports`](../template-customization.md#no-use-type-checking-imports) | Keep generated model imports available at runtime when using Ruff fixes. |
| [`--use-exact-imports`](../template-customization.md#use-exact-imports) | Import exact types instead of modules. |
| [`--use-type-checking-imports`](../template-customization.md#use-type-checking-imports) | Allow Ruff to move typing-only imports into TYPE_CHECKING blocks. |

## Output Formatting {#output-formatting}

Formatter selection, quote style, and string wrapping.

| Option | Description |
|--------|-------------|
| [`--custom-formatters`](../template-customization.md#custom-formatters) | Apply custom Python code formatters to generated output. |
| [`--custom-formatters-kwargs`](../template-customization.md#custom-formatters-kwargs) | Pass custom arguments to custom formatters via inline JSON or a JSON file path. |
| [`--formatters`](../template-customization.md#formatters) | Specify code formatters to apply to generated output. |
| [`--use-double-quotes`](../template-customization.md#use-double-quotes) | Use double quotes for string literals in generated code. |
| [`--wrap-string-literal`](../template-customization.md#wrap-string-literal) | Wrap long string literals across multiple lines. |
