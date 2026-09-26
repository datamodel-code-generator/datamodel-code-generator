# 🖥️ CLI Reference

This documentation is auto-generated from test cases.

🔍 **[Quick Reference](quick-reference.md)** - All options on one page for Ctrl+F search

## 📂 Categories

| Category | Options | Description |
|----------|---------|-------------|
| 📁 [Base Options](base-options.md) | 13 | Input/output configuration |
| 🔧 [Typing Customization](typing-customization.md) | 34 | Type annotation and import behavior |
| 🏷️ [Field Customization](field-customization.md) | 29 | Field naming and docstring behavior |
| 🏗️ [Model Customization](model-customization.md) | 44 | Model generation behavior |
| 🎨 [Template Customization](template-customization.md) | 27 | Output formatting and custom rendering |
| 📘 [OpenAPI-only Options](openapi-only-options.md) | 8 | OpenAPI-specific features |
| 📋 [GraphQL-only Options](graphql-only-options.md) | 1 |  |
| ⚙️ [General Options](general-options.md) | 24 | Utilities and meta options |
| 📝 [Utility Options](utility-options.md) | 19 | Help, version, debug options |

## 🎯 Focused Topics

Use these pages when you know the workflow area but not the exact option name.

| Topic | Options | Groups |
|-------|---------|--------|
| [Model Customization](topics/model-customization.md) | 23 | Model Naming, Model Reuse, Model Shape, Root Model |
| [Template Customization](topics/template-customization.md) | 21 | Custom Templates, Generated Output, Imports, Output Formatting |
| [Typing Customization](topics/typing-customization.md) | 23 | Imports, Collection Types, Type Alias, Type Mapping, Type Syntax |
| [OpenAPI](topics/openapi.md) | 7 | OpenAPI Naming, OpenAPI Paths, OpenAPI Scopes, Read Only Write Only |

## 🔗 Option Relationships

These links are generated from CLI option metadata and summarize options that imply, require, or conflict with other options.

| Source | Kind | Condition | Target | Note |
|--------|------|-----------|--------|------|
| [`--use-annotated`](typing-customization.md#use-annotated) | Implies | Always | [`--field-constraints`](field-customization.md#field-constraints) enabled | - |
| [`--use-specialized-enum`](typing-customization.md#use-specialized-enum) | Requires | Always | [`--target-python-version`](model-customization.md#target-python-version) = `3.11+` | `--use-specialized-enum` requires `--target-python-version` 3.11 or higher. |
| [`--use-type-alias-type`](typing-customization.md#use-type-alias-type) | Implies | Always | [`--use-type-alias`](typing-customization.md#use-type-alias) enabled | - |
| [`--original-field-name-delimiter`](field-customization.md#original-field-name-delimiter) | Requires | Always | [`--snake-case-field`](field-customization.md#snake-case-field) enabled | `--original-field-name-delimiter` can not be used without `--snake-case-field`. |
| [`--allow-extra-fields`](model-customization.md#allow-extra-fields) | Implies | Always | [`--extra-fields`](field-customization.md#extra-fields) = `allow` | - |
| [`--collapse-root-models-name-strategy`](model-customization.md#collapse-root-models-name-strategy) | Requires | Always | [`--collapse-root-models`](model-customization.md#collapse-root-models) enabled | `--collapse-root-models-name-strategy` requires `--collapse-root-models`. |
| [`--keyword-only`](model-customization.md#keyword-only) | Requires | Always | [`--target-python-version`](model-customization.md#target-python-version) = `3.10+` | `--keyword-only` requires `--target-python-version` 3.10 or higher for dataclasses. |
| [`--output-model-type`](model-customization.md#output-model-type) | Implies | `--output-model-type` = `msgspec.Struct` | [`--use-annotated`](typing-customization.md#use-annotated) enabled | - |
| [`--parent-scoped-naming`](model-customization.md#parent-scoped-naming) | Implies | Always | [`--naming-strategy`](model-customization.md#naming-strategy) = `parent-prefixed` | - |
| [`--reuse-scope`](model-customization.md#reuse-scope) | Requires | `--reuse-scope` = `tree` | [`--reuse-model`](model-customization.md#reuse-model) enabled | `--reuse-scope=tree` has no effect without `--reuse-model`. |
| [`--union-mode`](model-customization.md#union-mode) | Requires | Always | [`--output-model-type`](model-customization.md#output-model-type) = `pydantic_v2.BaseModel` | `--union-mode` is only supported for `--output-model-type pydantic_v2.BaseModel`. |
| [`--use-missing-sentinel`](model-customization.md#use-missing-sentinel) | Implies | Always | [`--target-pydantic-version`](model-customization.md#target-pydantic-version) = `2.12` | - |
| [`--use-missing-sentinel`](model-customization.md#use-missing-sentinel) | Requires | Always | [`--output-model-type`](model-customization.md#output-model-type) = `pydantic_v2.BaseModel` | `--use-missing-sentinel` is only supported for `--output-model-type pydantic_v2.BaseModel`. |
| [`--custom-file-header`](template-customization.md#custom-file-header) | Conflicts | Always | [`--custom-file-header-path`](template-customization.md#custom-file-header-path) | `--custom-file-header` can not be used with `--custom-file-header-path`. |
| [`--custom-file-header-path`](template-customization.md#custom-file-header-path) | Conflicts | Always | [`--custom-file-header`](template-customization.md#custom-file-header) | `--custom-file-header-path` can not be used with `--custom-file-header`. |
| [`--all-exports-collision-strategy`](general-options.md#all-exports-collision-strategy) | Requires | Always | [`--all-exports-scope`](general-options.md#all-exports-scope) = `recursive` | `--all-exports-collision-strategy` can only be used with `--all-exports-scope=recursive`. |
| [`--diff-against`](general-options.md#diff-against) | Requires | Always | [`--input`](base-options.md#input) | `--diff-against` requires --input with the current local schema path. |
| [`--diff-against`](general-options.md#diff-against) | Requires | Always | [`--output`](base-options.md#output) | `--diff-against` requires --output to select file or directory output layout. |
| [`--diff-against`](general-options.md#diff-against) | Conflicts | Always | [`--check`](general-options.md#check) | - |
| [`--diff-against`](general-options.md#diff-against) | Conflicts | Always | [`--watch`](general-options.md#watch) | - |
| [`--diff-against`](general-options.md#diff-against) | Conflicts | Always | [`--url`](base-options.md#url) | - |
| [`--diff-against`](general-options.md#diff-against) | Conflicts | Always | [`--input-model`](base-options.md#input-model) | - |
| [`--diff-against`](general-options.md#diff-against) | Conflicts | Always | [`--emit-model-metadata`](base-options.md#emit-model-metadata) | - |
| [`--diff-against`](general-options.md#diff-against) | Conflicts | Always | [`--fail-on-multi-module-stdout`](general-options.md#fail-on-multi-module-stdout) | - |
| [`--diff-against`](general-options.md#diff-against) | Conflicts | Always | `--job` | `--diff-against` compares one profile or input and cannot run named jobs. |
| [`--diff-against`](general-options.md#diff-against) | Conflicts | Always | `--all-jobs` | `--diff-against` compares one profile or input and cannot run named jobs. |
| [`--locked`](general-options.md#locked) | Conflicts | Always | [`--update-lock`](general-options.md#update-lock) | - |
| [`--update-lock`](general-options.md#update-lock) | Conflicts | Always | [`--locked`](general-options.md#locked) | - |

## All Options

**Jump to:** [A](#a) · [B](#b) · [C](#c) · [D](#d) · [E](#e) · [F](#f) · [G](#g) · [H](#h) · [I](#i) · [J](#j) · [K](#k) · [L](#l) · [M](#m) · [N](#n) · [O](#o) · [P](#p) · [R](#r) · [S](#s) · [T](#t) · [U](#u) · [V](#v) · [W](#w)


### A {#a}

- [`--additional-imports`](template-customization.md#additional-imports)
- [`--alias-generator`](model-customization.md#alias-generator)
- [`--aliases`](field-customization.md#aliases)
- [`--all-exports-collision-strategy`](general-options.md#all-exports-collision-strategy)
- [`--all-exports-scope`](general-options.md#all-exports-scope)
- [`--all-jobs`](utility-options.md#all-jobs)
- [`--allof-class-hierarchy`](typing-customization.md#allof-class-hierarchy)
- [`--allof-merge-mode`](typing-customization.md#allof-merge-mode)
- [`--allow-extra-fields`](model-customization.md#allow-extra-fields)
- [`--allow-leading-underscore-class-name`](model-customization.md#allow-leading-underscore-class-name)
- [`--allow-population-by-field-name`](model-customization.md#allow-population-by-field-name)
- [`--allow-private-network`](general-options.md#allow-private-network)
- [`--allow-remote-refs`](general-options.md#allow-remote-refs)

### B {#b}

- [`--base-class`](model-customization.md#base-class)
- [`--base-class-map`](model-customization.md#base-class-map)

### C {#c}

- [`--capitalize-enum-members`](field-customization.md#capitalize-enum-members)
- [`--check`](general-options.md#check)
- [`--class-decorators`](template-customization.md#class-decorators)
- [`--class-name`](model-customization.md#class-name)
- [`--class-name-affix-scope`](model-customization.md#class-name-affix-scope)
- [`--class-name-prefix`](model-customization.md#class-name-prefix)
- [`--class-name-suffix`](model-customization.md#class-name-suffix)
- [`--collapse-reuse-models`](model-customization.md#collapse-reuse-models)
- [`--collapse-root-models`](model-customization.md#collapse-root-models)
- [`--collapse-root-models-name-strategy`](model-customization.md#collapse-root-models-name-strategy)
- [`--custom-file-header`](template-customization.md#custom-file-header)
- [`--custom-file-header-mode`](template-customization.md#custom-file-header-mode)
- [`--custom-file-header-path`](template-customization.md#custom-file-header-path)
- [`--custom-formatters`](template-customization.md#custom-formatters)
- [`--custom-formatters-kwargs`](template-customization.md#custom-formatters-kwargs)
- [`--custom-template-dir`](template-customization.md#custom-template-dir)

### D {#d}

- [`--dataclass-arguments`](model-customization.md#dataclass-arguments)
- [`--debug`](utility-options.md#debug)
- [`--default-values`](field-customization.md#default-values)
- [`--deserialize-default-values`](field-customization.md#deserialize-default-values)
- [`--diagnostics-json`](utility-options.md#diagnostics-json)
- [`--diff-against`](general-options.md#diff-against)
- [`--disable-appending-item-suffix`](template-customization.md#disable-appending-item-suffix)
- [`--disable-future-imports`](typing-customization.md#disable-future-imports)
- [`--disable-timestamp`](template-customization.md#disable-timestamp)
- [`--disable-warnings`](general-options.md#disable-warnings)
- [`--duplicate-name-suffix`](model-customization.md#duplicate-name-suffix)

### E {#e}

- [`--emit-model-metadata`](base-options.md#emit-model-metadata)
- [`--empty-enum-field-name`](field-customization.md#empty-enum-field-name)
- [`--enable-command-header`](template-customization.md#enable-command-header)
- [`--enable-faux-immutability`](model-customization.md#enable-faux-immutability)
- [`--enable-generated-header-marker`](template-customization.md#enable-generated-header-marker)
- [`--enable-version-header`](template-customization.md#enable-version-header)
- [`--encoding`](base-options.md#encoding)
- [`--enum-field-as-literal`](typing-customization.md#enum-field-as-literal)
- [`--enum-field-as-literal-map`](typing-customization.md#enum-field-as-literal-map)
- [`--external-ref-mapping`](base-options.md#external-ref-mapping)
- [`--extra-fields`](field-customization.md#extra-fields)
- [`--extra-template-data`](template-customization.md#extra-template-data)

### F {#f}

- [`--fail-on-multi-module-stdout`](general-options.md#fail-on-multi-module-stdout)
- [`--field-constraints`](field-customization.md#field-constraints)
- [`--field-extra-keys`](field-customization.md#field-extra-keys)
- [`--field-extra-keys-without-x-prefix`](field-customization.md#field-extra-keys-without-x-prefix)
- [`--field-include-all-keys`](field-customization.md#field-include-all-keys)
- [`--field-type-collision-strategy`](field-customization.md#field-type-collision-strategy)
- [`--force-optional`](model-customization.md#force-optional)
- [`--formatters`](template-customization.md#formatters)
- [`--frozen-dataclasses`](model-customization.md#frozen-dataclasses)

### G {#g}

- [`--generate-cli-command`](general-options.md#generate-cli-command)
- [`--generate-prompt`](utility-options.md#generate-prompt)
- [`--generate-pyproject-config`](general-options.md#generate-pyproject-config)
- [`--generate-schema-validators`](template-customization.md#generate-schema-validators)
- [`--generate-server`](utility-options.md#generate-server)
- [`--graphql-no-typename`](graphql-only-options.md#graphql-no-typename)

### H {#h}

- [`--help`](utility-options.md#help)
- [`--http-backend`](base-options.md#http-backend)
- [`--http-headers`](general-options.md#http-headers)
- [`--http-ignore-tls`](general-options.md#http-ignore-tls)
- [`--http-local-ref-path`](general-options.md#http-local-ref-path)
- [`--http-query-parameters`](general-options.md#http-query-parameters)
- [`--http-timeout`](general-options.md#http-timeout)

### I {#i}

- [`--ignore-enum-constraints`](typing-customization.md#ignore-enum-constraints)
- [`--ignore-pyproject`](general-options.md#ignore-pyproject)
- [`--import-overrides`](typing-customization.md#import-overrides)
- [`--include-path-parameters`](openapi-only-options.md#include-path-parameters)
- [`--infer-union-variant-names`](field-customization.md#infer-union-variant-names)
- [`--input`](base-options.md#input)
- [`--input-file-type`](base-options.md#input-file-type)
- [`--input-model`](base-options.md#input-model)
- [`--input-model-ref-strategy`](base-options.md#input-model-ref-strategy)
- [`--install-skill`](utility-options.md#install-skill)

### J {#j}

- [`--job`](utility-options.md#job)

### K {#k}

- [`--keep-model-order`](model-customization.md#keep-model-order)
- [`--keyword-only`](model-customization.md#keyword-only)

### L {#l}

- [`--list-deprecations`](utility-options.md#list-deprecations)
- [`--list-experimental`](utility-options.md#list-experimental)
- [`--locked`](general-options.md#locked)
- [`--lockfile`](general-options.md#lockfile)

### M {#m}

- [`--model-extra-keys`](model-customization.md#model-extra-keys)
- [`--model-extra-keys-without-x-prefix`](model-customization.md#model-extra-keys-without-x-prefix)
- [`--model-name-map`](model-customization.md#model-name-map)
- [`--module-split-mode`](general-options.md#module-split-mode)

### N {#n}

- [`--naming-strategy`](model-customization.md#naming-strategy)
- [`--no-alias`](field-customization.md#no-alias)
- [`--no-color`](utility-options.md#no-color)
- [`--no-deserialize-default-values`](field-customization.md#no-deserialize-default-values)
- [`--no-treat-dot-as-module`](template-customization.md#no-treat-dot-as-module)
- [`--no-use-closed-typed-dict`](typing-customization.md#no-use-closed-typed-dict)
- [`--no-use-specialized-enum`](typing-customization.md#no-use-specialized-enum)
- [`--no-use-standard-collections`](typing-customization.md#no-use-standard-collections)
- [`--no-use-type-checking-imports`](template-customization.md#no-use-type-checking-imports)
- [`--no-use-union-operator`](typing-customization.md#no-use-union-operator)

### O {#o}

- [`--openapi-include-info-version`](openapi-only-options.md#openapi-include-info-version)
- [`--openapi-include-paths`](openapi-only-options.md#openapi-include-paths)
- [`--openapi-scopes`](openapi-only-options.md#openapi-scopes)
- [`--original-field-name-delimiter`](field-customization.md#original-field-name-delimiter)
- [`--output`](base-options.md#output)
- [`--output-date-class`](typing-customization.md#output-date-class)
- [`--output-datetime-class`](typing-customization.md#output-datetime-class)
- [`--output-format`](utility-options.md#output-format)
- [`--output-format-json-schema`](utility-options.md#output-format-json-schema)
- [`--output-model-type`](model-customization.md#output-model-type)
- [`--overwrite-skill`](utility-options.md#overwrite-skill)

### P {#p}

- [`--parent-scoped-naming`](model-customization.md#parent-scoped-naming)
- [`--preset`](base-options.md#preset)
- [`--profile`](utility-options.md#profile)

### R {#r}

- [`--read-only-write-only-model-type`](openapi-only-options.md#read-only-write-only-model-type)
- [`--remove-special-field-name-prefix`](field-customization.md#remove-special-field-name-prefix)
- [`--reuse-model`](model-customization.md#reuse-model)
- [`--reuse-scope`](model-customization.md#reuse-scope)

### S {#s}

- [`--schema-validator-base-class-name`](template-customization.md#schema-validator-base-class-name)
- [`--schema-validator-type`](template-customization.md#schema-validator-type)
- [`--schema-version`](base-options.md#schema-version)
- [`--schema-version-mode`](base-options.md#schema-version-mode)
- [`--serialization-aliases`](field-customization.md#serialization-aliases)
- [`--set-default-enum-member`](field-customization.md#set-default-enum-member)
- [`--shared-module-name`](general-options.md#shared-module-name)
- [`--skill-scope`](utility-options.md#skill-scope)
- [`--skip-root-model`](model-customization.md#skip-root-model)
- [`--snake-case-field`](field-customization.md#snake-case-field)
- [`--special-field-name-prefix`](field-customization.md#special-field-name-prefix)
- [`--strict-dotted-module-names`](template-customization.md#strict-dotted-module-names)
- [`--strict-nullable`](model-customization.md#strict-nullable)
- [`--strict-refs`](general-options.md#strict-refs)
- [`--strict-types`](typing-customization.md#strict-types)
- [`--strip-default-none`](model-customization.md#strip-default-none)

### T {#t}

- [`--target-config`](utility-options.md#target-config)
- [`--target-output`](utility-options.md#target-output)
- [`--target-pydantic-version`](model-customization.md#target-pydantic-version)
- [`--target-python-version`](model-customization.md#target-python-version)
- [`--treat-dot-as-module`](template-customization.md#treat-dot-as-module)
- [`--type-mappings`](typing-customization.md#type-mappings)
- [`--type-overrides`](typing-customization.md#type-overrides)

### U {#u}

- [`--union-mode`](model-customization.md#union-mode)
- [`--update-lock`](general-options.md#update-lock)
- [`--url`](base-options.md#url)
- [`--use-annotated`](typing-customization.md#use-annotated)
- [`--use-attribute-docstrings`](field-customization.md#use-attribute-docstrings)
- [`--use-closed-typed-dict`](typing-customization.md#use-closed-typed-dict)
- [`--use-decimal-for-multiple-of`](typing-customization.md#use-decimal-for-multiple-of)
- [`--use-default`](model-customization.md#use-default)
- [`--use-default-factory-for-optional-nested-models`](model-customization.md#use-default-factory-for-optional-nested-models)
- [`--use-default-kwarg`](model-customization.md#use-default-kwarg)
- [`--use-double-quotes`](template-customization.md#use-double-quotes)
- [`--use-enum-values-in-discriminator`](field-customization.md#use-enum-values-in-discriminator)
- [`--use-exact-imports`](template-customization.md#use-exact-imports)
- [`--use-field-description`](field-customization.md#use-field-description)
- [`--use-field-description-example`](field-customization.md#use-field-description-example)
- [`--use-frozen-field`](model-customization.md#use-frozen-field)
- [`--use-generic-base-class`](model-customization.md#use-generic-base-class)
- [`--use-generic-container-types`](typing-customization.md#use-generic-container-types)
- [`--use-inline-field-description`](field-customization.md#use-inline-field-description)
- [`--use-missing-sentinel`](model-customization.md#use-missing-sentinel)
- [`--use-non-positive-negative-number-constrained-types`](typing-customization.md#use-non-positive-negative-number-constrained-types)
- [`--use-object-type`](typing-customization.md#use-object-type)
- [`--use-one-literal-as-default`](model-customization.md#use-one-literal-as-default)
- [`--use-operation-id-as-name`](openapi-only-options.md#use-operation-id-as-name)
- [`--use-pendulum`](typing-customization.md#use-pendulum)
- [`--use-root-model-sequence-interface`](model-customization.md#use-root-model-sequence-interface)
- [`--use-root-model-type-alias`](typing-customization.md#use-root-model-type-alias)
- [`--use-schema-description`](field-customization.md#use-schema-description)
- [`--use-serialization-alias`](field-customization.md#use-serialization-alias)
- [`--use-serialize-as-any`](model-customization.md#use-serialize-as-any)
- [`--use-single-line-docstring`](field-customization.md#use-single-line-docstring)
- [`--use-specialized-enum`](typing-customization.md#use-specialized-enum)
- [`--use-standard-collections`](typing-customization.md#use-standard-collections)
- [`--use-standard-primitive-types`](typing-customization.md#use-standard-primitive-types)
- [`--use-status-code-in-response-name`](openapi-only-options.md#use-status-code-in-response-name)
- [`--use-subclass-enum`](model-customization.md#use-subclass-enum)
- [`--use-title-as-name`](field-customization.md#use-title-as-name)
- [`--use-total-false-for-typed-dict`](typing-customization.md#use-total-false-for-typed-dict)
- [`--use-tuple-for-fixed-items`](typing-customization.md#use-tuple-for-fixed-items)
- [`--use-tuple-for-fixed-length-arrays`](typing-customization.md#use-tuple-for-fixed-length-arrays)
- [`--use-type-alias`](typing-customization.md#use-type-alias)
- [`--use-type-alias-type`](typing-customization.md#use-type-alias-type)
- [`--use-type-checking-imports`](template-customization.md#use-type-checking-imports)
- [`--use-union-operator`](typing-customization.md#use-union-operator)
- [`--use-unique-items-as-set`](typing-customization.md#use-unique-items-as-set)

### V {#v}

- [`--validation`](openapi-only-options.md#validation)
- [`--validators`](template-customization.md#validators)
- [`--version`](utility-options.md#version)

### W {#w}

- [`--watch`](general-options.md#watch)
- [`--watch-delay`](general-options.md#watch-delay)
- [`--wrap-string-literal`](template-customization.md#wrap-string-literal)
