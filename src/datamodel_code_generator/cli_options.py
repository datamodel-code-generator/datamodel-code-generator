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
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Any

from datamodel_code_generator._format_types import PythonVersion
from datamodel_code_generator.deprecations import deprecation_message
from datamodel_code_generator.enums import (
    AllExportsScope,
    DataModelType,
    NamingStrategy,
    ReuseScope,
    TargetPydanticVersion,
)


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
    "--generate-server",
    "--target-config",
    "--target-output",
    "--diagnostics-json",
    "--dependency-format",
})

# Backward compatibility alias
EXCLUDED_FROM_DOCS = MANUAL_DOCS

# Documentation metadata for CLI options
# Sync is verified by tests/cli_doc/test_cli_options_sync.py
CLI_OPTION_META: dict[str, CLIOptionMeta] = {
    # ==========================================================================
    # Base Options (Input/Output)
    # ==========================================================================
    "--input": CLIOptionMeta(name="--input", category=OptionCategory.BASE),
    "--output": CLIOptionMeta(name="--output", category=OptionCategory.BASE),
    "--emit-model-metadata": CLIOptionMeta(name="--emit-model-metadata", category=OptionCategory.BASE),
    "--http-backend": CLIOptionMeta(name="--http-backend", category=OptionCategory.BASE),
    "--url": CLIOptionMeta(name="--url", category=OptionCategory.BASE),
    "--input-model": CLIOptionMeta(name="--input-model", category=OptionCategory.BASE),
    "--input-model-ref-strategy": CLIOptionMeta(name="--input-model-ref-strategy", category=OptionCategory.BASE),
    "--input-file-type": CLIOptionMeta(name="--input-file-type", category=OptionCategory.BASE),
    "--encoding": CLIOptionMeta(name="--encoding", category=OptionCategory.BASE),
    "--schema-version": CLIOptionMeta(name="--schema-version", category=OptionCategory.BASE),
    "--schema-version-mode": CLIOptionMeta(name="--schema-version-mode", category=OptionCategory.BASE),
    "--external-ref-mapping": CLIOptionMeta(name="--external-ref-mapping", category=OptionCategory.BASE),
    "--preset": CLIOptionMeta(name="--preset", category=OptionCategory.BASE),
    # ==========================================================================
    # Model Customization
    # ==========================================================================
    "--output-model-type": CLIOptionMeta(
        name="--output-model-type",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_SHAPE,
        implies=(
            CLIOptionRelation(
                option="--use-annotated",
                value=True,
                when=DataModelType.MsgspecStruct.value,
            ),
        ),
    ),
    "--target-python-version": CLIOptionMeta(
        name="--target-python-version",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_SHAPE,
    ),
    "--target-pydantic-version": CLIOptionMeta(
        name="--target-pydantic-version",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_SHAPE,
    ),
    "--base-class": CLIOptionMeta(
        name="--base-class",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_SHAPE,
    ),
    "--base-class-map": CLIOptionMeta(
        name="--base-class-map",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_SHAPE,
    ),
    "--class-name": CLIOptionMeta(
        name="--class-name",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_NAMING,
    ),
    "--model-name-map": CLIOptionMeta(
        name="--model-name-map",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_NAMING,
    ),
    "--allow-leading-underscore-class-name": CLIOptionMeta(
        name="--allow-leading-underscore-class-name",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_NAMING,
    ),
    "--class-name-prefix": CLIOptionMeta(
        name="--class-name-prefix",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_NAMING,
    ),
    "--class-name-suffix": CLIOptionMeta(
        name="--class-name-suffix",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_NAMING,
    ),
    "--class-name-affix-scope": CLIOptionMeta(
        name="--class-name-affix-scope",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_NAMING,
    ),
    "--frozen-dataclasses": CLIOptionMeta(name="--frozen-dataclasses", category=OptionCategory.MODEL),
    "--keyword-only": CLIOptionMeta(
        name="--keyword-only",
        category=OptionCategory.MODEL,
        requires=(
            CLIOptionRelation(
                option="--target-python-version",
                value=f"{PythonVersion.PY_310.value}+",
                message=(
                    f"`--keyword-only` requires `--target-python-version` "
                    f"{PythonVersion.PY_310.value} or higher for dataclasses."
                ),
            ),
        ),
    ),
    "--reuse-model": CLIOptionMeta(
        name="--reuse-model",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_REUSE,
    ),
    "--reuse-scope": CLIOptionMeta(
        name="--reuse-scope",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_REUSE,
        requires=(
            CLIOptionRelation(
                option="--reuse-model",
                value=True,
                when=ReuseScope.Tree.value,
                message="`--reuse-scope=tree` has no effect without `--reuse-model`.",
            ),
        ),
    ),
    "--collapse-root-models": CLIOptionMeta(
        name="--collapse-root-models",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.ROOT_MODEL,
    ),
    "--no-collapse-root-models": CLIOptionMeta(
        name="--no-collapse-root-models",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.ROOT_MODEL,
    ),
    "--collapse-root-models-name-strategy": CLIOptionMeta(
        name="--collapse-root-models-name-strategy",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.ROOT_MODEL,
        requires=(
            CLIOptionRelation(
                option="--collapse-root-models",
                value=True,
                message="`--collapse-root-models-name-strategy` requires `--collapse-root-models`.",
            ),
        ),
    ),
    "--collapse-reuse-models": CLIOptionMeta(
        name="--collapse-reuse-models",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_REUSE,
    ),
    "--keep-model-order": CLIOptionMeta(name="--keep-model-order", category=OptionCategory.MODEL),
    "--allow-extra-fields": CLIOptionMeta(
        name="--allow-extra-fields",
        category=OptionCategory.MODEL,
        deprecated=True,
        deprecated_message=deprecation_message("cli.allow-extra-fields"),
        implies=(CLIOptionRelation(option="--extra-fields", value="allow"),),
    ),
    "--allow-population-by-field-name": CLIOptionMeta(
        name="--allow-population-by-field-name", category=OptionCategory.MODEL
    ),
    "--no-allow-population-by-field-name": CLIOptionMeta(
        name="--no-allow-population-by-field-name", category=OptionCategory.MODEL
    ),
    "--enable-faux-immutability": CLIOptionMeta(name="--enable-faux-immutability", category=OptionCategory.MODEL),
    "--use-subclass-enum": CLIOptionMeta(name="--use-subclass-enum", category=OptionCategory.MODEL),
    "--force-optional": CLIOptionMeta(name="--force-optional", category=OptionCategory.MODEL),
    "--strict-nullable": CLIOptionMeta(name="--strict-nullable", category=OptionCategory.MODEL),
    "--use-default": CLIOptionMeta(name="--use-default", category=OptionCategory.MODEL),
    "--use-default-kwarg": CLIOptionMeta(name="--use-default-kwarg", category=OptionCategory.MODEL),
    "--use-missing-sentinel": CLIOptionMeta(
        name="--use-missing-sentinel",
        category=OptionCategory.MODEL,
        implies=(
            CLIOptionRelation(
                option="--target-pydantic-version",
                value=TargetPydanticVersion.V2_12.value,
            ),
        ),
        requires=(
            CLIOptionRelation(
                option="--output-model-type",
                value=DataModelType.PydanticV2BaseModel.value,
                message=("`--use-missing-sentinel` is only supported for `--output-model-type pydantic_v2.BaseModel`."),
            ),
        ),
    ),
    "--strip-default-none": CLIOptionMeta(name="--strip-default-none", category=OptionCategory.MODEL),
    "--dataclass-arguments": CLIOptionMeta(name="--dataclass-arguments", category=OptionCategory.MODEL),
    "--use-frozen-field": CLIOptionMeta(
        name="--use-frozen-field",
        category=OptionCategory.MODEL,
        topic=OptionTopic.OPENAPI,
        group=OptionGroup.READ_ONLY_WRITE_ONLY,
    ),
    "--no-use-frozen-field": CLIOptionMeta(
        name="--no-use-frozen-field",
        category=OptionCategory.MODEL,
        topic=OptionTopic.OPENAPI,
        group=OptionGroup.READ_ONLY_WRITE_ONLY,
    ),
    "--use-default-factory-for-optional-nested-models": CLIOptionMeta(
        name="--use-default-factory-for-optional-nested-models", category=OptionCategory.MODEL
    ),
    "--union-mode": CLIOptionMeta(
        name="--union-mode",
        category=OptionCategory.MODEL,
        requires=(
            CLIOptionRelation(
                option="--output-model-type",
                value=DataModelType.PydanticV2BaseModel.value,
                message="`--union-mode` is only supported for `--output-model-type pydantic_v2.BaseModel`.",
            ),
        ),
    ),
    "--parent-scoped-naming": CLIOptionMeta(
        name="--parent-scoped-naming",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_NAMING,
        deprecated=True,
        deprecated_message=deprecation_message("cli.parent-scoped-naming"),
        implies=(CLIOptionRelation(option="--naming-strategy", value=NamingStrategy.ParentPrefixed.value),),
    ),
    "--naming-strategy": CLIOptionMeta(
        name="--naming-strategy",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_NAMING,
    ),
    "--duplicate-name-suffix": CLIOptionMeta(
        name="--duplicate-name-suffix",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_NAMING,
    ),
    "--use-one-literal-as-default": CLIOptionMeta(name="--use-one-literal-as-default", category=OptionCategory.MODEL),
    "--use-serialize-as-any": CLIOptionMeta(name="--use-serialize-as-any", category=OptionCategory.MODEL),
    "--skip-root-model": CLIOptionMeta(
        name="--skip-root-model",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.ROOT_MODEL,
    ),
    "--use-root-model-sequence-interface": CLIOptionMeta(
        name="--use-root-model-sequence-interface",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.ROOT_MODEL,
    ),
    "--use-generic-base-class": CLIOptionMeta(
        name="--use-generic-base-class",
        category=OptionCategory.MODEL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_SHAPE,
    ),
    "--model-extra-keys": CLIOptionMeta(name="--model-extra-keys", category=OptionCategory.MODEL),
    "--model-extra-keys-without-x-prefix": CLIOptionMeta(
        name="--model-extra-keys-without-x-prefix", category=OptionCategory.MODEL
    ),
    # ==========================================================================
    # Field Customization
    # ==========================================================================
    "--snake-case-field": CLIOptionMeta(name="--snake-case-field", category=OptionCategory.FIELD),
    "--no-snake-case-field": CLIOptionMeta(name="--no-snake-case-field", category=OptionCategory.FIELD),
    "--original-field-name-delimiter": CLIOptionMeta(
        name="--original-field-name-delimiter",
        category=OptionCategory.FIELD,
        requires=(
            CLIOptionRelation(
                option="--snake-case-field",
                value=True,
                message="`--original-field-name-delimiter` can not be used without `--snake-case-field`.",
            ),
        ),
    ),
    "--capitalize-enum-members": CLIOptionMeta(name="--capitalize-enum-members", category=OptionCategory.FIELD),
    "--special-field-name-prefix": CLIOptionMeta(name="--special-field-name-prefix", category=OptionCategory.FIELD),
    "--remove-special-field-name-prefix": CLIOptionMeta(
        name="--remove-special-field-name-prefix", category=OptionCategory.FIELD
    ),
    "--empty-enum-field-name": CLIOptionMeta(name="--empty-enum-field-name", category=OptionCategory.FIELD),
    "--set-default-enum-member": CLIOptionMeta(
        name="--set-default-enum-member",
        category=OptionCategory.FIELD,
        deprecated=True,
        deprecated_message=deprecation_message("cli.set-default-enum-member"),
    ),
    "--aliases": CLIOptionMeta(name="--aliases", category=OptionCategory.FIELD),
    "--serialization-aliases": CLIOptionMeta(name="--serialization-aliases", category=OptionCategory.FIELD),
    "--default-values": CLIOptionMeta(name="--default-values", category=OptionCategory.FIELD),
    "--deserialize-default-values": CLIOptionMeta(name="--deserialize-default-values", category=OptionCategory.FIELD),
    "--no-deserialize-default-values": CLIOptionMeta(
        name="--no-deserialize-default-values", category=OptionCategory.FIELD
    ),
    "--alias-generator": CLIOptionMeta(name="--alias-generator", category=OptionCategory.MODEL),
    "--no-alias": CLIOptionMeta(name="--no-alias", category=OptionCategory.FIELD),
    "--use-serialization-alias": CLIOptionMeta(name="--use-serialization-alias", category=OptionCategory.FIELD),
    "--use-title-as-name": CLIOptionMeta(name="--use-title-as-name", category=OptionCategory.FIELD),
    "--infer-union-variant-names": CLIOptionMeta(name="--infer-union-variant-names", category=OptionCategory.FIELD),
    "--use-schema-description": CLIOptionMeta(name="--use-schema-description", category=OptionCategory.FIELD),
    "--use-field-description": CLIOptionMeta(name="--use-field-description", category=OptionCategory.FIELD),
    "--use-field-description-example": CLIOptionMeta(
        name="--use-field-description-example", category=OptionCategory.FIELD
    ),
    "--use-attribute-docstrings": CLIOptionMeta(name="--use-attribute-docstrings", category=OptionCategory.FIELD),
    "--use-inline-field-description": CLIOptionMeta(
        name="--use-inline-field-description", category=OptionCategory.FIELD
    ),
    "--use-single-line-docstring": CLIOptionMeta(name="--use-single-line-docstring", category=OptionCategory.FIELD),
    "--field-constraints": CLIOptionMeta(name="--field-constraints", category=OptionCategory.FIELD),
    "--field-extra-keys": CLIOptionMeta(name="--field-extra-keys", category=OptionCategory.FIELD),
    "--field-extra-keys-without-x-prefix": CLIOptionMeta(
        name="--field-extra-keys-without-x-prefix", category=OptionCategory.FIELD
    ),
    "--field-include-all-keys": CLIOptionMeta(name="--field-include-all-keys", category=OptionCategory.FIELD),
    "--extra-fields": CLIOptionMeta(name="--extra-fields", category=OptionCategory.FIELD),
    "--use-enum-values-in-discriminator": CLIOptionMeta(
        name="--use-enum-values-in-discriminator", category=OptionCategory.FIELD
    ),
    "--field-type-collision-strategy": CLIOptionMeta(
        name="--field-type-collision-strategy", category=OptionCategory.FIELD
    ),
    # ==========================================================================
    # Typing Customization
    # ==========================================================================
    "--use-union-operator": CLIOptionMeta(
        name="--use-union-operator",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.TYPE_SYNTAX,
    ),
    "--no-use-union-operator": CLIOptionMeta(
        name="--no-use-union-operator",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.TYPE_SYNTAX,
    ),
    "--use-standard-collections": CLIOptionMeta(
        name="--use-standard-collections",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.COLLECTION_TYPES,
    ),
    "--no-use-standard-collections": CLIOptionMeta(
        name="--no-use-standard-collections",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.COLLECTION_TYPES,
    ),
    "--use-generic-container-types": CLIOptionMeta(
        name="--use-generic-container-types",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.COLLECTION_TYPES,
    ),
    "--use-annotated": CLIOptionMeta(
        name="--use-annotated",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.TYPE_SYNTAX,
        implies=(CLIOptionRelation(option="--field-constraints", value=True),),
    ),
    "--no-use-annotated": CLIOptionMeta(
        name="--no-use-annotated",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.TYPE_SYNTAX,
    ),
    "--use-type-alias": CLIOptionMeta(
        name="--use-type-alias",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.TYPE_ALIAS,
    ),
    "--use-type-alias-type": CLIOptionMeta(
        name="--use-type-alias-type",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.TYPE_ALIAS,
        implies=(CLIOptionRelation(option="--use-type-alias", value=True),),
    ),
    "--use-root-model-type-alias": CLIOptionMeta(
        name="--use-root-model-type-alias",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.TYPE_ALIAS,
    ),
    "--strict-types": CLIOptionMeta(
        name="--strict-types",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.TYPE_MAPPING,
    ),
    "--enum-field-as-literal": CLIOptionMeta(name="--enum-field-as-literal", category=OptionCategory.TYPING),
    "--enum-field-as-literal-map": CLIOptionMeta(name="--enum-field-as-literal-map", category=OptionCategory.TYPING),
    "--ignore-enum-constraints": CLIOptionMeta(name="--ignore-enum-constraints", category=OptionCategory.TYPING),
    "--disable-future-imports": CLIOptionMeta(
        name="--disable-future-imports",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.IMPORTS,
    ),
    "--use-pendulum": CLIOptionMeta(
        name="--use-pendulum",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.TYPE_MAPPING,
    ),
    "--use-standard-primitive-types": CLIOptionMeta(
        name="--use-standard-primitive-types",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.TYPE_MAPPING,
    ),
    "--no-use-standard-primitive-types": CLIOptionMeta(
        name="--no-use-standard-primitive-types",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.TYPE_MAPPING,
    ),
    "--use-object-type": CLIOptionMeta(
        name="--use-object-type",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.TYPE_MAPPING,
    ),
    "--use-type-checking-imports": CLIOptionMeta(
        name="--use-type-checking-imports",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.IMPORTS,
    ),
    "--no-use-type-checking-imports": CLIOptionMeta(
        name="--no-use-type-checking-imports",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.IMPORTS,
    ),
    "--generate-schema-validators": CLIOptionMeta(
        name="--generate-schema-validators", category=OptionCategory.TEMPLATE
    ),
    "--schema-validator-type": CLIOptionMeta(name="--schema-validator-type", category=OptionCategory.TEMPLATE),
    "--schema-validator-base-class-name": CLIOptionMeta(
        name="--schema-validator-base-class-name", category=OptionCategory.TEMPLATE
    ),
    "--output-datetime-class": CLIOptionMeta(
        name="--output-datetime-class",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.TYPE_MAPPING,
    ),
    "--output-date-class": CLIOptionMeta(
        name="--output-date-class",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.TYPE_MAPPING,
    ),
    "--use-decimal-for-multiple-of": CLIOptionMeta(
        name="--use-decimal-for-multiple-of",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.TYPE_MAPPING,
    ),
    "--use-non-positive-negative-number-constrained-types": CLIOptionMeta(
        name="--use-non-positive-negative-number-constrained-types", category=OptionCategory.TYPING
    ),
    "--use-unique-items-as-set": CLIOptionMeta(
        name="--use-unique-items-as-set",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.COLLECTION_TYPES,
    ),
    "--use-tuple-for-fixed-items": CLIOptionMeta(
        name="--use-tuple-for-fixed-items",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.COLLECTION_TYPES,
    ),
    "--use-tuple-for-fixed-length-arrays": CLIOptionMeta(
        name="--use-tuple-for-fixed-length-arrays",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.COLLECTION_TYPES,
    ),
    "--use-total-false-for-typed-dict": CLIOptionMeta(
        name="--use-total-false-for-typed-dict",
        category=OptionCategory.TYPING,
    ),
    "--use-closed-typed-dict": CLIOptionMeta(name="--use-closed-typed-dict", category=OptionCategory.TYPING),
    "--no-use-closed-typed-dict": CLIOptionMeta(name="--no-use-closed-typed-dict", category=OptionCategory.TYPING),
    "--import-overrides": CLIOptionMeta(
        name="--import-overrides",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.IMPORTS,
    ),
    "--type-mappings": CLIOptionMeta(
        name="--type-mappings",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.TYPE_MAPPING,
    ),
    "--type-overrides": CLIOptionMeta(
        name="--type-overrides",
        category=OptionCategory.TYPING,
        topic=OptionTopic.TYPING_CUSTOMIZATION,
        group=OptionGroup.TYPE_MAPPING,
    ),
    "--use-specialized-enum": CLIOptionMeta(
        name="--use-specialized-enum",
        category=OptionCategory.TYPING,
        requires=(
            CLIOptionRelation(
                option="--target-python-version",
                value=f"{PythonVersion.PY_311.value}+",
                message=(
                    f"`--use-specialized-enum` requires `--target-python-version` "
                    f"{PythonVersion.PY_311.value} or higher."
                ),
            ),
        ),
    ),
    "--no-use-specialized-enum": CLIOptionMeta(name="--no-use-specialized-enum", category=OptionCategory.TYPING),
    "--allof-merge-mode": CLIOptionMeta(name="--allof-merge-mode", category=OptionCategory.TYPING),
    "--allof-class-hierarchy": CLIOptionMeta(name="--allof-class-hierarchy", category=OptionCategory.TYPING),
    # ==========================================================================
    # Template Customization
    # ==========================================================================
    "--wrap-string-literal": CLIOptionMeta(
        name="--wrap-string-literal",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.OUTPUT_FORMATTING,
    ),
    "--custom-template-dir": CLIOptionMeta(
        name="--custom-template-dir",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.CUSTOM_TEMPLATES,
    ),
    "--extra-template-data": CLIOptionMeta(
        name="--extra-template-data",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.CUSTOM_TEMPLATES,
    ),
    "--validators": CLIOptionMeta(
        name="--validators",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.CUSTOM_TEMPLATES,
    ),
    "--custom-file-header": CLIOptionMeta(
        name="--custom-file-header",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.GENERATED_OUTPUT,
        conflicts=(
            CLIOptionRelation(
                option="--custom-file-header-path",
                message="`--custom-file-header` can not be used with `--custom-file-header-path`.",
            ),
        ),
    ),
    "--custom-file-header-path": CLIOptionMeta(
        name="--custom-file-header-path",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.GENERATED_OUTPUT,
        conflicts=(
            CLIOptionRelation(
                option="--custom-file-header",
                message="`--custom-file-header-path` can not be used with `--custom-file-header`.",
            ),
        ),
    ),
    "--custom-file-header-mode": CLIOptionMeta(
        name="--custom-file-header-mode",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.GENERATED_OUTPUT,
    ),
    "--additional-imports": CLIOptionMeta(
        name="--additional-imports",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.IMPORTS,
    ),
    "--class-decorators": CLIOptionMeta(
        name="--class-decorators",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.GENERATED_OUTPUT,
    ),
    "--use-double-quotes": CLIOptionMeta(
        name="--use-double-quotes",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.OUTPUT_FORMATTING,
    ),
    "--use-exact-imports": CLIOptionMeta(
        name="--use-exact-imports",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.IMPORTS,
    ),
    "--disable-appending-item-suffix": CLIOptionMeta(
        name="--disable-appending-item-suffix", category=OptionCategory.TEMPLATE
    ),
    "--no-treat-dot-as-module": CLIOptionMeta(name="--no-treat-dot-as-module", category=OptionCategory.TEMPLATE),
    "--strict-dotted-module-names": CLIOptionMeta(
        name="--strict-dotted-module-names",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.GENERATED_OUTPUT,
    ),
    "--no-strict-dotted-module-names": CLIOptionMeta(
        name="--no-strict-dotted-module-names",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.GENERATED_OUTPUT,
    ),
    "--disable-timestamp": CLIOptionMeta(
        name="--disable-timestamp",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.GENERATED_OUTPUT,
    ),
    "--enable-version-header": CLIOptionMeta(
        name="--enable-version-header",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.GENERATED_OUTPUT,
    ),
    "--enable-command-header": CLIOptionMeta(
        name="--enable-command-header",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.GENERATED_OUTPUT,
    ),
    "--enable-generated-header-marker": CLIOptionMeta(
        name="--enable-generated-header-marker",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.GENERATED_OUTPUT,
    ),
    "--formatters": CLIOptionMeta(
        name="--formatters",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.OUTPUT_FORMATTING,
    ),
    "--custom-formatters": CLIOptionMeta(
        name="--custom-formatters",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.OUTPUT_FORMATTING,
    ),
    "--custom-formatters-kwargs": CLIOptionMeta(
        name="--custom-formatters-kwargs",
        category=OptionCategory.TEMPLATE,
        topic=OptionTopic.TEMPLATE_CUSTOMIZATION,
        group=OptionGroup.OUTPUT_FORMATTING,
    ),
    # ==========================================================================
    # OpenAPI-only Options
    # ==========================================================================
    "--openapi-scopes": CLIOptionMeta(
        name="--openapi-scopes",
        category=OptionCategory.OPENAPI,
        topic=OptionTopic.OPENAPI,
        group=OptionGroup.OPENAPI_SCOPES,
    ),
    "--use-operation-id-as-name": CLIOptionMeta(
        name="--use-operation-id-as-name",
        category=OptionCategory.OPENAPI,
        topic=OptionTopic.OPENAPI,
        group=OptionGroup.OPENAPI_NAMING,
    ),
    "--use-status-code-in-response-name": CLIOptionMeta(
        name="--use-status-code-in-response-name",
        category=OptionCategory.OPENAPI,
        topic=OptionTopic.OPENAPI,
        group=OptionGroup.OPENAPI_NAMING,
    ),
    "--read-only-write-only-model-type": CLIOptionMeta(
        name="--read-only-write-only-model-type",
        category=OptionCategory.OPENAPI,
        topic=OptionTopic.OPENAPI,
        group=OptionGroup.READ_ONLY_WRITE_ONLY,
    ),
    "--include-path-parameters": CLIOptionMeta(
        name="--include-path-parameters",
        category=OptionCategory.OPENAPI,
        topic=OptionTopic.OPENAPI,
        group=OptionGroup.OPENAPI_PATHS,
    ),
    "--openapi-include-paths": CLIOptionMeta(
        name="--openapi-include-paths",
        category=OptionCategory.OPENAPI,
        topic=OptionTopic.OPENAPI,
        group=OptionGroup.OPENAPI_PATHS,
    ),
    "--openapi-include-info-version": CLIOptionMeta(
        name="--openapi-include-info-version", category=OptionCategory.OPENAPI
    ),
    "--validation": CLIOptionMeta(
        name="--validation",
        category=OptionCategory.OPENAPI,
        deprecated=True,
        deprecated_message=deprecation_message("cli.validation"),
    ),
    # ==========================================================================
    # GraphQL-only Options
    # ==========================================================================
    "--graphql-no-typename": CLIOptionMeta(name="--graphql-no-typename", category=OptionCategory.GRAPHQL),
    # ==========================================================================
    # General Options
    # ==========================================================================
    "--check": CLIOptionMeta(name="--check", category=OptionCategory.GENERAL),
    "--diff-against": CLIOptionMeta(
        name="--diff-against",
        category=OptionCategory.GENERAL,
        requires=(
            CLIOptionRelation(
                option="--input",
                message="`--diff-against` requires --input with the current local schema path.",
            ),
            CLIOptionRelation(
                option="--output",
                message="`--diff-against` requires --output to select file or directory output layout.",
            ),
        ),
        conflicts=(
            CLIOptionRelation(option="--check"),
            CLIOptionRelation(option="--watch"),
            CLIOptionRelation(option="--url"),
            CLIOptionRelation(option="--input-model"),
            CLIOptionRelation(option="--emit-model-metadata"),
            CLIOptionRelation(option="--fail-on-multi-module-stdout"),
            CLIOptionRelation(
                option="--job",
                message="`--diff-against` compares one profile or input and cannot run named jobs.",
            ),
            CLIOptionRelation(
                option="--all-jobs",
                message="`--diff-against` compares one profile or input and cannot run named jobs.",
            ),
        ),
    ),
    "--fail-on-multi-module-stdout": CLIOptionMeta(
        name="--fail-on-multi-module-stdout", category=OptionCategory.GENERAL
    ),
    "--allow-remote-refs": CLIOptionMeta(name="--allow-remote-refs", category=OptionCategory.GENERAL),
    "--no-allow-remote-refs": CLIOptionMeta(name="--no-allow-remote-refs", category=OptionCategory.GENERAL),
    "--strict-refs": CLIOptionMeta(name="--strict-refs", category=OptionCategory.GENERAL),
    "--allow-private-network": CLIOptionMeta(name="--allow-private-network", category=OptionCategory.GENERAL),
    "--no-allow-private-network": CLIOptionMeta(name="--no-allow-private-network", category=OptionCategory.GENERAL),
    "--http-headers": CLIOptionMeta(name="--http-headers", category=OptionCategory.GENERAL),
    "--http-local-ref-path": CLIOptionMeta(name="--http-local-ref-path", category=OptionCategory.GENERAL),
    "--http-ignore-tls": CLIOptionMeta(name="--http-ignore-tls", category=OptionCategory.GENERAL),
    "--http-query-parameters": CLIOptionMeta(name="--http-query-parameters", category=OptionCategory.GENERAL),
    "--http-timeout": CLIOptionMeta(name="--http-timeout", category=OptionCategory.GENERAL),
    "--lockfile": CLIOptionMeta(name="--lockfile", category=OptionCategory.GENERAL),
    "--update-lock": CLIOptionMeta(
        name="--update-lock",
        category=OptionCategory.GENERAL,
        conflicts=(CLIOptionRelation(option="--locked"),),
    ),
    "--locked": CLIOptionMeta(
        name="--locked",
        category=OptionCategory.GENERAL,
        conflicts=(CLIOptionRelation(option="--update-lock"),),
    ),
    "--ignore-pyproject": CLIOptionMeta(name="--ignore-pyproject", category=OptionCategory.GENERAL),
    "--generate-cli-command": CLIOptionMeta(name="--generate-cli-command", category=OptionCategory.GENERAL),
    "--generate-pyproject-config": CLIOptionMeta(name="--generate-pyproject-config", category=OptionCategory.GENERAL),
    "--shared-module-name": CLIOptionMeta(
        name="--shared-module-name",
        category=OptionCategory.GENERAL,
        topic=OptionTopic.MODEL_CUSTOMIZATION,
        group=OptionGroup.MODEL_REUSE,
    ),
    "--all-exports-scope": CLIOptionMeta(name="--all-exports-scope", category=OptionCategory.GENERAL),
    "--all-exports-collision-strategy": CLIOptionMeta(
        name="--all-exports-collision-strategy",
        category=OptionCategory.GENERAL,
        requires=(
            CLIOptionRelation(
                option="--all-exports-scope",
                value=AllExportsScope.Recursive.value,
                message="`--all-exports-collision-strategy` can only be used with `--all-exports-scope=recursive`.",
            ),
        ),
    ),
    "--module-split-mode": CLIOptionMeta(name="--module-split-mode", category=OptionCategory.GENERAL),
    "--disable-warnings": CLIOptionMeta(name="--disable-warnings", category=OptionCategory.GENERAL),
    "--watch": CLIOptionMeta(name="--watch", category=OptionCategory.GENERAL),
    "--watch-delay": CLIOptionMeta(name="--watch-delay", category=OptionCategory.GENERAL),
}


def _validate_option_topic_groups() -> None:
    """Validate CLI option topic/group invariants at import time."""
    for option, meta in CLI_OPTION_META.items():
        if meta.topic is None or meta.group is None:
            continue
        allowed_groups = OPTION_TOPIC_ALLOWED_GROUPS.get(meta.topic)
        if allowed_groups is None:  # pragma: no cover
            msg = f"{option}: topic {meta.topic.value!r} has no allowed groups"
            raise AssertionError(msg)
        if meta.group not in allowed_groups:  # pragma: no cover
            msg = f"{option}: group {meta.group.value!r} is not allowed for topic {meta.topic.value!r}"
            raise AssertionError(msg)


_validate_option_topic_groups()


def _canonical_option_key(option: str) -> tuple[int, str]:
    """Key function for determining canonical option.

    Canonical option is determined by:
    1. Longest option string (--help over -h)
    2. Lexicographically last if same length (for stability)

    This ensures deterministic canonical selection.
    """
    return (len(option), option)


@lru_cache(maxsize=1)
def _build_alias_map_from_argparse() -> dict[str, str]:
    """Build alias -> canonical map from argparse (the source of truth).

    The canonical option is the longest option string for each action.
    If multiple options have the same length, the lexicographically last one is chosen.
    """
    from datamodel_code_generator.arguments import arg_parser as argument_parser  # noqa: PLC0415

    alias_map: dict[str, str] = {}
    for action in argument_parser._actions:  # noqa: SLF001
        if not action.option_strings:
            continue  # pragma: no cover
        # Canonical = longest, then lexicographically last for stability
        canonical = max(action.option_strings, key=_canonical_option_key)
        for opt in action.option_strings:
            alias_map[opt] = canonical
    return alias_map


def get_canonical_option(option: str) -> str:
    """Normalize an option alias to its canonical form.

    Uses argparse definitions as the source of truth.

    Examples:
        >>> get_canonical_option("-h")
        '--help'
        >>> get_canonical_option("--help")
        '--help'
    """
    return _build_alias_map_from_argparse().get(option, option)


@lru_cache(maxsize=1)
def _build_option_string_groups_from_argparse() -> dict[str, tuple[str, ...]]:
    """Build option string -> sibling option strings map from argparse."""
    from datamodel_code_generator.arguments import arg_parser as argument_parser  # noqa: PLC0415

    groups: dict[str, tuple[str, ...]] = {}
    for action in argument_parser._actions:  # noqa: SLF001
        options = tuple(action.option_strings)
        for option in options:
            groups[option] = options
    return groups


@lru_cache(maxsize=1)
def get_all_canonical_options() -> frozenset[str]:
    """Get all canonical options from argparse."""
    return frozenset(_build_alias_map_from_argparse().values())


@lru_cache(maxsize=1)
def get_all_argparse_options() -> frozenset[str]:
    """Get all options from argparse (including aliases)."""
    return frozenset(_build_alias_map_from_argparse().keys())


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
    separate metadata, then falls back to the canonical option name.
    If the option is not explicitly registered, returns a default entry
    with General category (auto-categorization for new options).
    """
    if option in CLI_OPTION_META:
        return CLI_OPTION_META[option]
    canonical = get_canonical_option(option)
    if canonical in CLI_OPTION_META:
        return CLI_OPTION_META[canonical]
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
    """Return the option name used by generated CLI documentation anchors."""
    canonical = get_canonical_option(option)
    if canonical in MANUAL_DOCS:
        return canonical
    options = _build_option_string_groups_from_argparse().get(option, ())
    if option.startswith("--") and any(item.startswith("--no-") for item in options):
        return option
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
