# Model Customization

Choose model class shape, naming, reuse, and root-model behavior.

Options are grouped from shared CLI metadata and link back to their generated reference sections.

## Groups

| Group | Options | Description |
|-------|---------|-------------|
| [Model Naming](#model-naming) | 9 | Class names, suffixes, prefixes, and duplicate-name behavior. |
| [Model Reuse](#model-reuse) | 4 | Schema deduplication and shared generated modules. |
| [Model Shape](#model-shape) | 6 | Output model family and compatibility targets. |
| [Root Model](#root-model) | 5 | Root model creation, collapse, and alias behavior. |

## Model Naming {#model-naming}

Class names, suffixes, prefixes, and duplicate-name behavior.

| Option | Description |
|--------|-------------|
| [`--allow-leading-underscore-class-name`](../model-customization.md#allow-leading-underscore-class-name) | Allow an explicitly specified root class name to start with an underscore. |
| [`--class-name`](../model-customization.md#class-name) | Override the auto-generated class name with a custom name. |
| [`--class-name-affix-scope`](../model-customization.md#class-name-affix-scope) | Control which classes receive the prefix/suffix. |
| [`--class-name-prefix`](../model-customization.md#class-name-prefix) | Add a prefix to all generated class names. |
| [`--class-name-suffix`](../model-customization.md#class-name-suffix) | Add a suffix to all generated class names. |
| [`--duplicate-name-suffix`](../model-customization.md#duplicate-name-suffix) | Customize suffix for duplicate model names. |
| [`--model-name-map`](../model-customization.md#model-name-map) | Rename generated model classes from a JSON mapping. |
| [`--naming-strategy`](../model-customization.md#naming-strategy) | Use parent-prefixed naming strategy for duplicate model names. |
| [`--parent-scoped-naming`](../model-customization.md#parent-scoped-naming) | Namespace models by their parent scope to avoid naming conflicts. |

## Model Reuse {#model-reuse}

Schema deduplication and shared generated modules.

| Option | Description |
|--------|-------------|
| [`--collapse-reuse-models`](../model-customization.md#collapse-reuse-models) | Collapse duplicate models by replacing references instead of inheritance. |
| [`--reuse-model`](../model-customization.md#reuse-model) | Reuse identical model definitions instead of generating duplicates. |
| [`--reuse-scope`](../model-customization.md#reuse-scope) | Scope for model reuse detection (root or tree). |
| [`--shared-module-name`](../general-options.md#shared-module-name) | Customize the name of the shared module for deduplicated models. |

## Model Shape {#model-shape}

Output model family and compatibility targets.

| Option | Description |
|--------|-------------|
| [`--base-class`](../model-customization.md#base-class) | Specify a custom base class for generated models. |
| [`--base-class-map`](../model-customization.md#base-class-map) | Specify different base classes for specific models via JSON mapping. |
| [`--output-model-type`](../model-customization.md#output-model-type) | Select the output model type (Pydantic v2, Pydantic v2 dataclass, dataclasses, T... |
| [`--target-pydantic-version`](../model-customization.md#target-pydantic-version) | Target Pydantic version for generated code compatibility. |
| [`--target-python-version`](../model-customization.md#target-python-version) | Target Python version for generated code syntax and imports. |
| [`--use-generic-base-class`](../model-customization.md#use-generic-base-class) | Generate a shared base class with model configuration to avoid repetition (DRY). |

## Root Model {#root-model}

Root model creation, collapse, and alias behavior.

| Option | Description |
|--------|-------------|
| [`--collapse-root-models`](../model-customization.md#collapse-root-models) | Inline root model definitions instead of creating separate wrapper classes. |
| [`--collapse-root-models-name-strategy`](../model-customization.md#collapse-root-models-name-strategy) | Select which name to keep when collapsing root models with object references. |
| [`--collapse-shared-parent-wrappers`](../model-customization.md#collapse-shared-parent-wrappers) | Collapse a parent-strategy wrapper in every model that references it. |
| [`--skip-root-model`](../model-customization.md#skip-root-model) | Skip generation of root model when schema contains nested definitions. |
| [`--use-root-model-sequence-interface`](../model-customization.md#use-root-model-sequence-interface) | Make non-null sequence-like Pydantic v2 RootModel classes implement collections.... |
