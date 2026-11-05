"""CLI option metadata for documentation and browser integrations.

This module provides metadata for CLI options used in documentation generation
and the browser playground.
The argparse definitions in arguments.py remain the source of truth for CLI behavior.
This module only adds metadata that must be shared outside argparse, such as
categories, deprecation messages, and option relationships.

Synchronization between this module and argparse is verified by tests in
tests/cli_doc/test_cli_options_sync.py.
"""

from __future__ import annotations

import re
from argparse import BooleanOptionalAction
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from datamodel_code_generator._format_types import PythonVersion  # noqa: F401  # Preserve the established cli_options export.
from datamodel_code_generator._option_names import option_flag
from datamodel_code_generator._option_rules import IMPLIED_OPTIONS
from datamodel_code_generator._requirements import REQUIREMENTS
from datamodel_code_generator.deprecations import cli_deprecations
from datamodel_code_generator.enums import (
    AllExportsScope,
    DataModelType,
    InputFileType,
    NamingStrategy,
    ReuseScope,
    TargetPydanticVersion,
)
from datamodel_code_generator.python_literal import stable_repr

if TYPE_CHECKING:
    from argparse import Action, ArgumentParser


class OptionCategory(str, Enum):
    """Categories for organizing CLI options in documentation."""

    BASE = "Base Options"
    TYPING = "Typing Customization"
    FIELD = "Field Customization"
    MODEL = "Model Customization"
    TEMPLATE = "Template Customization"
    OPENAPI = "OpenAPI-only Options"
    GRAPHQL = "GraphQL-only Options"
    GENERAL = "General Options"
    TARGET = "Target Generation Options"


class OptionTopic(str, Enum):
    """Optional topics for linking CLI options to focused documentation areas."""

    MODEL_CUSTOMIZATION = "model-customization"
    TEMPLATE_CUSTOMIZATION = "template-customization"
    TYPING_CUSTOMIZATION = "typing-customization"
    OPENAPI = "openapi"


class OptionGroup(str, Enum):
    """Optional subgroups within CLI option topics."""

    MODEL_NAMING = "model-naming"
    MODEL_REUSE = "model-reuse"
    MODEL_SHAPE = "model-shape"
    ROOT_MODEL = "root-model"
    CUSTOM_TEMPLATES = "custom-templates"
    GENERATED_OUTPUT = "generated-output"
    IMPORTS = "imports"
    OUTPUT_FORMATTING = "output-formatting"
    COLLECTION_TYPES = "collection-types"
    TYPE_ALIAS = "type-alias"
    TYPE_MAPPING = "type-mapping"
    TYPE_SYNTAX = "type-syntax"
    OPENAPI_NAMING = "openapi-naming"
    OPENAPI_PATHS = "openapi-paths"
    OPENAPI_SCOPES = "openapi-scopes"
    READ_ONLY_WRITE_ONLY = "read-only-write-only"


@dataclass(frozen=True)
class CLIOptionRelation:
    """A documented relationship from one CLI option to another.

    ``when=None`` means the relation applies whenever the source option is enabled.
    """

    option: str
    value: Any = None
    when: Any = None
    message: str | None = None


@dataclass(frozen=True)
class CLIOptionMeta:
    """Shared metadata for a CLI option.

    This is NOT the argparse definition - it only contains metadata.
    The actual CLI behavior is defined in arguments.py.
    """

    name: str
    category: OptionCategory
    topic: OptionTopic | None = None
    group: OptionGroup | None = None
    since_version: str | None = None
    deprecated: bool = False
    deprecated_message: str | None = None
    implies: tuple[CLIOptionRelation, ...] = ()
    requires: tuple[CLIOptionRelation, ...] = ()
    conflicts: tuple[CLIOptionRelation, ...] = ()


OPTION_RELATION_KINDS = ("implies", "requires", "conflicts")
CLI_REFERENCE_ROOT = "cli-reference"
UTILITY_OPTIONS_DOC = "utility-options"
OPTION_TOPIC_ALLOWED_GROUPS: dict[OptionTopic, frozenset[OptionGroup]] = {
    OptionTopic.MODEL_CUSTOMIZATION: frozenset({
        OptionGroup.MODEL_NAMING,
        OptionGroup.MODEL_REUSE,
        OptionGroup.MODEL_SHAPE,
        OptionGroup.ROOT_MODEL,
    }),
    OptionTopic.TEMPLATE_CUSTOMIZATION: frozenset({
        OptionGroup.CUSTOM_TEMPLATES,
        OptionGroup.GENERATED_OUTPUT,
        OptionGroup.IMPORTS,
        OptionGroup.OUTPUT_FORMATTING,
    }),
    OptionTopic.TYPING_CUSTOMIZATION: frozenset({
        OptionGroup.COLLECTION_TYPES,
        OptionGroup.IMPORTS,
        OptionGroup.TYPE_ALIAS,
        OptionGroup.TYPE_MAPPING,
        OptionGroup.TYPE_SYNTAX,
    }),
    OptionTopic.OPENAPI: frozenset({
        OptionGroup.OPENAPI_NAMING,
        OptionGroup.OPENAPI_PATHS,
        OptionGroup.OPENAPI_SCOPES,
        OptionGroup.READ_ONLY_WRITE_ONLY,
    }),
}


# Options with manual documentation (not auto-generated from tests)
# These options have hand-written docs in docs/cli-reference/manual/
MANUAL_DOCS: frozenset[str] = frozenset({
    "--help",
    "--version",
    "--debug",
    "--profile",
    "--job",
    "--all-jobs",
    "--output-format",
    "--output-format-json-schema",
    "--no-color",
    "--generate-prompt",
    "--install-skill",
    "--skill-scope",
    "--overwrite-skill",
    "--list-deprecations",
    "--list-experimental",
})

# Backward compatibility alias
EXCLUDED_FROM_DOCS = MANUAL_DOCS

_CATEGORY_TOPIC = {
    OptionCategory.MODEL: OptionTopic.MODEL_CUSTOMIZATION,
    OptionCategory.TYPING: OptionTopic.TYPING_CUSTOMIZATION,
    OptionCategory.TEMPLATE: OptionTopic.TEMPLATE_CUSTOMIZATION,
    OptionCategory.OPENAPI: OptionTopic.OPENAPI,
}
_C = OptionCategory
_G = OptionGroup
_DEPRECATED_OPTIONS = cli_deprecations()


def _words(chunks: tuple[str, ...]) -> list[str]:
    """Split space-separated chunks of option names into one list."""
    return [word for chunk in chunks for word in chunk.split()]


def _opts(
    category: OptionCategory,
    *names: str,
    group: OptionGroup | None = None,
    topic: OptionTopic | None = None,
    **fields: Any,
) -> dict[str, CLIOptionMeta]:
    """Build the rows of the options in ``names`` that share a category, group and the other metadata.

    A group takes the topic of its category unless ``topic`` is given. An option is deprecated, with its message, when
    the deprecation registry has an entry for it.
    """
    if group and topic is None:
        topic = _CATEGORY_TOPIC[category]
    return {
        name: CLIOptionMeta(
            name,
            category,
            topic,
            group,
            deprecated=(message := _DEPRECATED_OPTIONS.get(name)) is not None,
            deprecated_message=message,
            **fields,
        )
        for name in _words(names)
    }


def _relations(names: str, **fields: Any) -> tuple[CLIOptionRelation, ...]:
    """Build one relation with the same ``fields`` to each option in the space-separated ``names``."""
    return tuple(CLIOptionRelation(name, **fields) for name in names.split())


def _implied(field: str) -> tuple[CLIOptionRelation, ...]:
    """Build the relation to the option that the option of ``field`` turns on (``IMPLIED_OPTIONS``)."""
    return _relations(option_flag(IMPLIED_OPTIONS[field]), value=True)


def _target_options(generate: str, value: str, *names: str) -> dict[str, CLIOptionMeta]:
    """Build the rows of the target options in ``names`` that only apply with ``generate value``."""
    return {
        name: CLIOptionMeta(
            name,
            OptionCategory.TARGET,
            requires=_relations(generate, message=f"`{name}` requires `{generate} {value}`."),
        )
        for name in _words(names)
    }


# Documentation metadata for CLI options
# Sync is verified by tests/cli_doc/test_cli_options_sync.py
CLI_OPTION_META: dict[str, CLIOptionMeta] = {
    **_opts(
        _C.BASE,
        "--input --output --emit-model-metadata --http-backend --url --input-model --input-model-ref-strategy",
        "--input-file-type --encoding --schema-version --schema-version-mode",
        "--read-schema-version-per-document --no-read-schema-version-per-document",
        "--read-escaped-ref-pointers --no-read-escaped-ref-pointers",
        "--read-dynamic-anchors-in-document-order --no-read-dynamic-anchors-in-document-order",
        "--ignore-ref-siblings-by-schema-version --no-ignore-ref-siblings-by-schema-version",
        "--external-ref-mapping --preset",
    ),
    **_opts(
        _C.MODEL,
        "--output-model-type",
        group=_G.MODEL_SHAPE,
        implies=_relations("--use-annotated", value=True, when=DataModelType.MsgspecStruct.value),
    ),
    **_opts(
        _C.MODEL,
        "--target-python-version --target-pydantic-version --base-class --base-class-map",
        group=_G.MODEL_SHAPE,
    ),
    **_opts(
        _C.MODEL,
        "--class-name --model-name-map --allow-leading-underscore-class-name --class-name-prefix",
        "--class-name-suffix --class-name-affix-scope",
        group=_G.MODEL_NAMING,
    ),
    **_opts(_C.MODEL, "--frozen-dataclasses"),
    **_opts(
        _C.MODEL,
        "--keyword-only",
        requires=_relations(
            "--target-python-version",
            value=f"{REQUIREMENTS['python.kw_only_dataclass'].minimum}+",
            message=(
                f"`--keyword-only` requires `--target-python-version` "
                f"{REQUIREMENTS['python.kw_only_dataclass'].minimum} or higher for dataclasses."
            ),
        ),
    ),
    **_opts(_C.MODEL, "--reuse-model --no-reuse-model", group=_G.MODEL_REUSE),
    **_opts(
        _C.MODEL,
        "--reuse-scope",
        group=_G.MODEL_REUSE,
        requires=_relations(
            "--reuse-model",
            value=True,
            when=ReuseScope.Tree.value,
            message="`--reuse-scope=tree` has no effect without `--reuse-model`.",
        ),
    ),
    **_opts(_C.MODEL, "--collapse-root-models --no-collapse-root-models", group=_G.ROOT_MODEL),
    **_opts(
        _C.MODEL,
        "--collapse-root-models-name-strategy",
        group=_G.ROOT_MODEL,
        requires=_relations(
            "--collapse-root-models",
            value=True,
            message="`--collapse-root-models-name-strategy` requires `--collapse-root-models`.",
        ),
    ),
    **_opts(_C.MODEL, "--collapse-shared-parent-wrappers --no-collapse-shared-parent-wrappers", group=_G.ROOT_MODEL),
    **_opts(
        _C.MODEL,
        "--keep-constrained-root-models-in-every-container --no-keep-constrained-root-models-in-every-container",
        "--keep-enum-literal-aliases --no-keep-enum-literal-aliases",
        group=_G.ROOT_MODEL,
    ),
    **_opts(_C.MODEL, "--collapse-reuse-models --no-collapse-reuse-models", group=_G.MODEL_REUSE),
    **_opts(_C.MODEL, "--keep-model-order --order-constructor-fields-by"),
    **_opts(_C.MODEL, "--allow-extra-fields", implies=_relations("--extra-fields", value="allow")),
    **_opts(
        _C.MODEL,
        "--allow-population-by-field-name --no-allow-population-by-field-name --enable-faux-immutability",
        "--use-subclass-enum --no-use-subclass-enum --force-optional --strict-nullable --no-strict-nullable",
        "--omit-redundant-reference-null --no-omit-redundant-reference-null",
        "--keep-reference-null-on-merged-models --no-keep-reference-null-on-merged-models --use-default",
        "--use-default-kwarg --no-use-default-kwarg",
    ),
    **_opts(
        _C.MODEL,
        "--use-missing-sentinel",
        implies=_relations("--target-pydantic-version", value=TargetPydanticVersion.V2_12.value),
        requires=_relations(
            "--output-model-type",
            message=(
                "`--use-missing-sentinel` is only supported for `--output-model-type pydantic_v2.BaseModel` "
                "or `pydantic_v2.dataclass`."
            ),
        ),
    ),
    **_opts(
        _C.MODEL,
        "--strip-default-none --dataclass-arguments --use-msgspec-tagged-unions --tag-msgspec-by-payload-name",
        "--no-tag-msgspec-by-payload-name",
        "--use-msgspec-nullable-enum-alias --use-msgspec-meta-for-map-keys --no-use-msgspec-meta-for-map-keys",
    ),
    **_opts(
        _C.MODEL, "--use-frozen-field --no-use-frozen-field", group=_G.READ_ONLY_WRITE_ONLY, topic=OptionTopic.OPENAPI
    ),
    **_opts(_C.MODEL, "--use-default-factory-for-optional-nested-models"),
    **_opts(
        _C.MODEL,
        "--union-mode",
        requires=_relations(
            "--output-model-type",
            value=DataModelType.PydanticV2BaseModel.value,
            message="`--union-mode` is only supported for `--output-model-type pydantic_v2.BaseModel`.",
        ),
    ),
    **_opts(
        _C.MODEL,
        "--parent-scoped-naming",
        group=_G.MODEL_NAMING,
        implies=_relations("--naming-strategy", value=NamingStrategy.ParentPrefixed.value),
    ),
    **_opts(
        _C.MODEL,
        "--naming-strategy --duplicate-name-suffix --use-duplicate-name-suffix-for-every-collision",
        "--no-use-duplicate-name-suffix-for-every-collision",
        "--name-converted-payloads-by-message --no-name-converted-payloads-by-message",
        "--singularize-last-word-of-item-names --no-singularize-last-word-of-item-names",
        group=_G.MODEL_NAMING,
    ),
    **_opts(
        _C.MODEL,
        "--use-one-literal-as-default --use-serialize-as-any --use-serialize-as-any-for-every-subclass",
        "--no-use-serialize-as-any-for-every-subclass",
    ),
    **_opts(_C.MODEL, "--skip-root-model --use-root-model-sequence-interface", group=_G.ROOT_MODEL),
    **_opts(_C.MODEL, "--use-generic-base-class", group=_G.MODEL_SHAPE),
    **_opts(_C.MODEL, "--model-extra-keys --model-extra-keys-without-x-prefix"),
    **_opts(_C.FIELD, "--snake-case-field --no-snake-case-field"),
    **_opts(
        _C.FIELD,
        "--original-field-name-delimiter",
        requires=_relations(
            "--snake-case-field",
            value=True,
            message="`--original-field-name-delimiter` can not be used without `--snake-case-field`.",
        ),
    ),
    **_opts(
        _C.FIELD,
        "--capitalize-enum-members --capitalize-numbered-enum-members --no-capitalize-numbered-enum-members",
        "--special-field-name-prefix",
        "--remove-special-field-name-prefix --empty-enum-field-name",
    ),
    **_opts(_C.FIELD, "--set-default-enum-member"),
    **_opts(
        _C.FIELD,
        "--aliases --serialization-aliases --default-values --deserialize-default-values",
        "--no-deserialize-default-values",
    ),
    **_opts(_C.MODEL, "--alias-generator --pin-aliases-with-alias-generator --no-pin-aliases-with-alias-generator"),
    **_opts(
        _C.FIELD,
        "--no-alias --use-serialization-alias --use-title-as-name --no-use-title-as-name --infer-union-variant-names",
        "--use-tag-as-union-variant-name --name-allof-union-members-after-branches",
        "--no-name-allof-union-members-after-branches --use-schema-description --no-use-schema-description",
        "--use-field-description --no-use-field-description",
        "--use-field-description-example --no-use-field-description-example --use-attribute-docstrings",
        "--use-inline-field-description",
        "--use-single-line-docstring --no-use-single-line-docstring --field-constraints",
        "--omit-redundant-constraints --no-omit-redundant-constraints --read-length-keywords-by-type",
        "--no-read-length-keywords-by-type",
        "--read-msgspec-union-bounds-exactly --no-read-msgspec-union-bounds-exactly",
        "--use-site-models-for-constrained-refs --no-use-site-models-for-constrained-refs",
        "--use-one-model-per-ref-site --no-use-one-model-per-ref-site --field-extra-keys",
        "--field-extra-keys-without-x-prefix",
        "--field-include-all-keys --extra-fields --use-enum-values-in-discriminator",
        "--merge-shared-variant-tags --no-merge-shared-variant-tags",
        "--field-type-collision-strategy",
    ),
    **_opts(_C.TYPING, "--use-union-operator --no-use-union-operator", group=_G.TYPE_SYNTAX),
    **_opts(
        _C.TYPING,
        "--use-standard-collections --no-use-standard-collections --use-generic-container-types",
        group=_G.COLLECTION_TYPES,
    ),
    **_opts(_C.TYPING, "--use-annotated", group=_G.TYPE_SYNTAX, implies=_implied("use_annotated")),
    **_opts(_C.TYPING, "--no-use-annotated", group=_G.TYPE_SYNTAX),
    **_opts(_C.TYPING, "--use-type-alias", group=_G.TYPE_ALIAS),
    **_opts(_C.TYPING, "--use-type-alias-type", group=_G.TYPE_ALIAS, implies=_implied("use_type_alias_type")),
    **_opts(_C.TYPING, "--use-root-model-type-alias", group=_G.TYPE_ALIAS),
    **_opts(_C.TYPING, "--strict-types", group=_G.TYPE_MAPPING),
    **_opts(
        _C.TYPING,
        "--enum-field-as-literal --enum-field-as-literal-map --ignore-enum-constraints",
        "--use-single-value-enum-as-const --no-use-single-value-enum-as-const --read-const-beside-enum",
        "--no-read-const-beside-enum --filter-literal-values --no-filter-literal-values",
        "--intersect-allof-keywords --no-intersect-allof-keywords",
    ),
    **_opts(
        _C.TYPING,
        "--intersect-combined-schema-keywords",
        implies=_implied("intersect_combined_schema_keywords"),
    ),
    **_opts(_C.TYPING, "--no-intersect-combined-schema-keywords"),
    **_opts(_C.TYPING, "--distribute-allof-over-unions --no-distribute-allof-over-unions"),
    **_opts(
        _C.MODEL,
        "--read-nullable-beside-type-list --no-read-nullable-beside-type-list",
        "--keep-field-defaults-off-branch-roots --no-keep-field-defaults-off-branch-roots",
    ),
    **_opts(_C.TYPING, "--disable-future-imports", group=_G.IMPORTS),
    **_opts(
        _C.TYPING,
        "--use-pendulum --use-standard-primitive-types --no-use-standard-primitive-types --use-object-type",
        "--no-use-object-type",
        "--use-typed-nullable-map-values --use-typed-map-values-beside-other-types",
        "--no-use-typed-map-values-beside-other-types",
        group=_G.TYPE_MAPPING,
    ),
    **_opts(_C.TYPING, "--keep-enum-member-values --no-keep-enum-member-values"),
    **_opts(
        _C.TYPING,
        "--read-unevaluated-properties --no-read-unevaluated-properties --use-single-dict-for-object-maps",
        "--no-use-single-dict-for-object-maps",
        group=_G.TYPE_MAPPING,
    ),
    **_opts(_C.TEMPLATE, "--use-type-checking-imports --no-use-type-checking-imports", group=_G.IMPORTS),
    **_opts(
        _C.TEMPLATE,
        "--generate-schema-validators --check-pattern-maps-strictly --no-check-pattern-maps-strictly",
        "--schema-validator-type",
        "--schema-validator-base-class-name",
    ),
    **_opts(
        _C.TYPING,
        "--output-datetime-class --output-date-class --use-decimal-for-multiple-of",
        "--use-decimal-in-field-forms --no-use-decimal-in-field-forms",
        "--use-encoded-str-for-constrained-base64 --no-use-encoded-str-for-constrained-base64",
        group=_G.TYPE_MAPPING,
    ),
    **_opts(_C.TYPING, "--use-non-positive-negative-number-constrained-types"),
    **_opts(
        _C.TYPING,
        "--use-unique-items-as-set --no-use-unique-items-as-set --use-unique-items-as-set-by-value",
        "--no-use-unique-items-as-set-by-value --use-tuple-for-fixed-items",
        "--no-use-tuple-for-fixed-items --use-tuple-for-closed-arrays --no-use-tuple-for-closed-arrays",
        "--use-tuple-for-fixed-length-arrays",
        group=_G.COLLECTION_TYPES,
    ),
    **_opts(_C.TYPING, "--use-total-false-for-typed-dict --use-closed-typed-dict --no-use-closed-typed-dict"),
    **_opts(_C.TYPING, "--import-overrides", group=_G.IMPORTS),
    **_opts(_C.TYPING, "--type-mappings --type-overrides", group=_G.TYPE_MAPPING),
    **_opts(
        _C.TYPING,
        "--use-specialized-enum",
        requires=_relations(
            "--target-python-version",
            value=f"{REQUIREMENTS['python.strenum'].minimum}+",
            message=(
                f"`--use-specialized-enum` requires `--target-python-version` "
                f"{REQUIREMENTS['python.strenum'].minimum} or higher."
            ),
        ),
    ),
    **_opts(_C.TYPING, "--no-use-specialized-enum --allof-merge-mode --allof-class-hierarchy"),
    **_opts(_C.TEMPLATE, "--wrap-string-literal", group=_G.OUTPUT_FORMATTING),
    **_opts(_C.TEMPLATE, "--custom-template-dir --extra-template-data --validators", group=_G.CUSTOM_TEMPLATES),
    **_opts(
        _C.TEMPLATE,
        "--custom-file-header",
        group=_G.GENERATED_OUTPUT,
        conflicts=_relations(
            "--custom-file-header-path",
            message="`--custom-file-header` can not be used with `--custom-file-header-path`.",
        ),
    ),
    **_opts(
        _C.TEMPLATE,
        "--custom-file-header-path",
        group=_G.GENERATED_OUTPUT,
        conflicts=_relations(
            "--custom-file-header", message="`--custom-file-header-path` can not be used with `--custom-file-header`."
        ),
    ),
    **_opts(_C.TEMPLATE, "--custom-file-header-mode", group=_G.GENERATED_OUTPUT),
    **_opts(_C.TEMPLATE, "--additional-imports", group=_G.IMPORTS),
    **_opts(_C.TEMPLATE, "--class-decorators", group=_G.GENERATED_OUTPUT),
    **_opts(_C.TEMPLATE, "--use-double-quotes", group=_G.OUTPUT_FORMATTING),
    **_opts(
        _C.TEMPLATE,
        "--use-exact-imports --rename-submodules-hidden-by-classes --no-rename-submodules-hidden-by-classes",
        group=_G.IMPORTS,
    ),
    **_opts(_C.TEMPLATE, "--disable-appending-item-suffix --no-treat-dot-as-module"),
    **_opts(
        _C.TEMPLATE,
        "--strict-dotted-module-names --no-strict-dotted-module-names --disable-timestamp --no-disable-timestamp",
        "--enable-version-header --enable-command-header --enable-generated-header-marker",
        group=_G.GENERATED_OUTPUT,
    ),
    **_opts(
        _C.TEMPLATE,
        "--formatters --no-formatters --custom-formatters --custom-formatters-kwargs",
        group=_G.OUTPUT_FORMATTING,
    ),
    **_opts(_C.OPENAPI, "--openapi-scopes", group=_G.OPENAPI_SCOPES),
    **_opts(_C.OPENAPI, "--use-operation-id-as-name --use-status-code-in-response-name", group=_G.OPENAPI_NAMING),
    **_opts(
        _C.OPENAPI,
        "--read-only-write-only-model-type --dedupe-request-response-variants --no-dedupe-request-response-variants",
        group=_G.READ_ONLY_WRITE_ONLY,
    ),
    **_opts(
        _C.OPENAPI,
        "--include-path-parameters --openapi-include-paths --keep-first-character-of-path-keys",
        "--no-keep-first-character-of-path-keys",
        group=_G.OPENAPI_PATHS,
    ),
    **_opts(_C.OPENAPI, "--openapi-include-info-version"),
    **_opts(_C.OPENAPI, "--openapi-as-definitions", group=_G.OPENAPI_SCOPES),
    **_opts(_C.OPENAPI, "--validation"),
    **_opts(_C.GRAPHQL, "--graphql-no-typename"),
    **_opts(_C.GENERAL, "--check"),
    **_opts(
        _C.GENERAL,
        "--diff-against",
        requires=(
            CLIOptionRelation(
                "--input", message="`--diff-against` requires --input with the current local schema path."
            ),
            CLIOptionRelation(
                "--output", message="`--diff-against` requires --output to select file or directory output layout."
            ),
        ),
        conflicts=(
            *_relations("--check --watch --url --input-model --emit-model-metadata --fail-on-multi-module-stdout"),
            *_relations(
                "--job --all-jobs", message="`--diff-against` compares one profile or input and cannot run named jobs."
            ),
        ),
    ),
    **_opts(
        _C.GENERAL,
        "--fail-on-multi-module-stdout --allow-remote-refs --no-allow-remote-refs --strict-refs",
        "--allow-private-network --no-allow-private-network --http-headers --http-local-ref-path",
        "--http-ignore-tls --http-query-parameters --http-timeout --lockfile",
    ),
    **_opts(_C.GENERAL, "--update-lock", conflicts=_relations("--locked")),
    **_opts(_C.GENERAL, "--locked", conflicts=_relations("--update-lock")),
    **_opts(_C.GENERAL, "--ignore-pyproject --generate-cli-command --generate-pyproject-config --recommend-options"),
    **_opts(_C.GENERAL, "--shared-module-name", group=_G.MODEL_REUSE, topic=OptionTopic.MODEL_CUSTOMIZATION),
    **_opts(_C.GENERAL, "--all-exports-scope"),
    **_opts(
        _C.GENERAL,
        "--all-exports-collision-strategy",
        requires=_relations(
            "--all-exports-scope",
            value=AllExportsScope.Recursive.value,
            message="`--all-exports-collision-strategy` can only be used with `--all-exports-scope=recursive`.",
        ),
    ),
    **_opts(_C.GENERAL, "--module-split-mode --disable-warnings --watch --watch-delay"),
    **_opts(
        _C.TARGET,
        "--generate-server",
        requires=(
            CLIOptionRelation("--server-output", message="`--generate-server` requires `--server-output`."),
            CLIOptionRelation("--server-package", message="`--generate-server` requires `--server-package`."),
            CLIOptionRelation(
                "--server-model-package", message="`--generate-server` requires `--server-model-package`."
            ),
            CLIOptionRelation(
                "--input-file-type",
                value=InputFileType.OpenAPI.value,
                message="`--generate-server` requires `--input-file-type openapi`.",
            ),
        ),
        conflicts=_relations(
            "--generate-client", message="`--generate-server` can not be used with `--generate-client`."
        ),
    ),
    **_target_options(
        "--generate-server",
        "fastapi",
        "--server-output --server-package --server-model-package --server-layout --server-handler-mode",
        "--server-handler-modes --server-include-request --no-server-include-request --server-body-mode",
        "--server-body-modes --server-primary-responses --server-operation-names --server-router-names",
        "--server-parameter-names",
    ),
    **_opts(
        _C.TARGET,
        "--generate-client",
        requires=(
            CLIOptionRelation("--client-output", message="`--generate-client` requires `--client-output`."),
            CLIOptionRelation("--client-package", message="`--generate-client` requires `--client-package`."),
            CLIOptionRelation(
                "--client-model-package", message="`--generate-client` requires `--client-model-package`."
            ),
            CLIOptionRelation(
                "--input-file-type",
                value=InputFileType.OpenAPI.value,
                message="`--generate-client` requires `--input-file-type openapi`.",
            ),
        ),
        conflicts=_relations(
            "--generate-server", message="`--generate-client` can not be used with `--generate-server`."
        ),
    ),
    **_target_options(
        "--generate-client",
        "httpx2",
        "--client-output --client-package --client-model-package --client-signature-style --client-body-arguments",
        "--client-resource-names --client-operations --client-default-base-url --client-server-base-url",
        "--client-protocols",
    ),
}


def _validate_option_topic_groups() -> None:
    """Validate CLI option topic/group invariants at import time."""
    for option, meta in CLI_OPTION_META.items():
        if meta.topic is None or meta.group is None:
            continue
        allowed_groups = OPTION_TOPIC_ALLOWED_GROUPS.get(meta.topic)
        if allowed_groups is None:  # pragma: no cover
            msg = f"{option}: topic {stable_repr(meta.topic.value)} has no allowed groups"
            raise AssertionError(msg)
        if meta.group not in allowed_groups:  # pragma: no cover
            msg = (
                f"{option}: group {stable_repr(meta.group.value)} is not allowed for topic "
                f"{stable_repr(meta.topic.value)}"
            )
            raise AssertionError(msg)


_validate_option_topic_groups()


def _canonical_option_key(option: str) -> tuple[int, str]:
    """Sort key of the canonical option: the longest option string (--help over -h), then the lexicographically last."""
    return (len(option), option)


@dataclass(frozen=True, slots=True)
class _ArgparseIndex:
    """The option strings of the command-line parser: each one's canonical option and siblings, actions by dest."""

    canonical: dict[str, str]
    siblings: dict[str, tuple[str, ...]]
    actions: dict[str, Action]


@lru_cache(maxsize=1)
def _argparse_index() -> _ArgparseIndex:
    """Index the command-line parser (the source of truth), when a caller first needs it.

    The canonical option is the longest option string of an action; among options of one length, the
    lexicographically last. A destination maps to the last action with option strings that stores it.
    """
    from datamodel_code_generator.arguments import arg_parser as argument_parser  # noqa: PLC0415

    index = _ArgparseIndex({}, {}, {})
    for action in argument_parser._actions:  # noqa: SLF001
        if not (options := tuple(action.option_strings)):
            continue  # pragma: no cover
        canonical = max(options, key=_canonical_option_key)
        for option in options:
            index.canonical[option] = canonical
            index.siblings[option] = options
        index.actions[action.dest] = action
    return index


def get_canonical_option(option: str) -> str:
    """Normalize an option alias to its canonical form.

    Uses argparse definitions as the source of truth.

    Examples:
        >>> get_canonical_option("-h")
        '--help'
        >>> get_canonical_option("--help")
        '--help'
    """
    return _argparse_index().canonical.get(option, option)


def _actions_by_dest(parser: ArgumentParser | None) -> dict[str, Action]:
    """Map argparse destination names to the actions that have option strings."""
    if parser is None:
        return {}
    return {action.dest: action for action in parser._actions if action.option_strings}  # noqa: SLF001


def _no_option(action: Action) -> str | None:
    """Return the first --no- option string of an action, or None."""
    return next((option for option in action.option_strings if option.startswith("--no-")), None)


def _negative_option(dest: str) -> str | None:
    """Return the --no- flag the parser defines for a destination, or None.

    A boolean's is the --no- string of its action; a list option's is the flag that turns it off with an empty list.
    """
    index = _argparse_index()
    if isinstance(action := index.actions.get(dest), BooleanOptionalAction):
        return _no_option(action)
    return flag if (flag := option_flag(dest, negative=True)) in index.canonical else None


@lru_cache(maxsize=1)
def get_all_canonical_options() -> frozenset[str]:
    """Get all canonical options from argparse."""
    return frozenset(_argparse_index().canonical.values())


@lru_cache(maxsize=1)
def get_all_argparse_options() -> frozenset[str]:
    """Get all options from argparse (including aliases)."""
    return frozenset(_argparse_index().canonical.keys())


def is_manual_doc(option: str) -> bool:
    """Check if an option has manual documentation (not auto-generated)."""
    canonical = get_canonical_option(option)
    return canonical in MANUAL_DOCS


# Backward compatibility alias
def is_excluded_from_docs(option: str) -> bool:
    """Check if an option is excluded from auto-generated documentation.

    Deprecated: Use is_manual_doc() instead.
    """
    return is_manual_doc(option)


def get_option_meta(option: str) -> CLIOptionMeta | None:
    """Get documentation metadata for an option.

    Uses an exact match first so BooleanOptionalAction variants can carry
    separate metadata, then the alias of the same variant that has a section, then the canonical option name.
    If the option is not explicitly registered, returns a default entry
    with General category (auto-categorization for new options).
    """
    if option in CLI_OPTION_META:
        return CLI_OPTION_META[option]
    canonical = get_canonical_option(option)
    for name in (get_cli_option_doc_name(option), canonical):
        if name in CLI_OPTION_META:
            return CLI_OPTION_META[name]
    if canonical in get_all_canonical_options() and canonical not in EXCLUDED_FROM_DOCS:
        return CLIOptionMeta(name=canonical, category=OptionCategory.GENERAL)
    return None


def get_cli_doc_slug(text: str) -> str:
    """Convert CLI documentation text to the generated page/anchor slug."""
    slug = text.lstrip("-").lower()
    slug = re.sub(r"[^a-z0-9-]", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    return slug or "unknown"


def get_cli_option_doc_name(option: str) -> str:
    """Return the option name used by generated CLI documentation anchors.

    The --name and --no-name forms of a flag have a section each; the aliases of a form share the section of the
    canonical one of them.
    """
    canonical = get_canonical_option(option)
    if canonical in MANUAL_DOCS:
        return canonical
    options = _argparse_index().siblings.get(option, ())
    if option.startswith("--") and any(item.startswith("--no-") for item in options):
        negative = option.startswith("--no-")
        return max((item for item in options if item.startswith("--no-") is negative), key=_canonical_option_key)
    return canonical


def get_cli_option_doc_path(option: str, *, root: str = "", extension: str = ".md") -> str | None:
    """Return the generated CLI documentation path for an option."""
    doc_option = get_cli_option_doc_name(option)
    if doc_option in MANUAL_DOCS:
        page = UTILITY_OPTIONS_DOC
    elif meta := get_option_meta(doc_option):
        page = get_cli_doc_slug(meta.category.value)
    else:
        return None

    prefix = f"{root.rstrip('/')}/" if root else ""
    return f"{prefix}{page}{extension}#{get_cli_doc_slug(doc_option)}"
