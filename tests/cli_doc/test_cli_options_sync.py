"""Tests to ensure CLI_OPTION_META stays in sync with argparse.

These tests verify that:
1. All options in CLI_OPTION_META exist in argparse
2. All options in MANUAL_DOCS exist in argparse
3. There's no overlap between CLI_OPTION_META and MANUAL_DOCS
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import pytest

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from datamodel_code_generator import cli_options
from datamodel_code_generator.arguments import arg_parser as argument_parser
from datamodel_code_generator.cli_options import (
    CLI_OPTION_META,
    MANUAL_DOCS,
    OPTION_RELATION_KINDS,
    OPTION_TOPIC_ALLOWED_GROUPS,
    CLIOptionMeta,
    OptionCategory,
    OptionGroup,
    OptionTopic,
    _canonical_option_key,
    get_all_argparse_options,
    get_all_canonical_options,
    get_canonical_option,
    get_cli_doc_slug,
    get_cli_option_doc_name,
    get_cli_option_doc_path,
    get_option_meta,
    is_excluded_from_docs,
    is_manual_doc,
)
from scripts import build_cli_docs
from scripts.build_cli_docs import (
    CLIDocExample,
    CLIDocOption,
    _documented_related_option,
    _format_option_link,
    collect_topic_options,
    generate_option_section,
    generate_topic_index,
    generate_topic_page,
    scan_docs_for_cli_option_tags,
)

if TYPE_CHECKING:
    from collections.abc import Collection

_T = TypeVar("_T")


def _fail_if_not_equal(actual: _T, expected: _T, context: str) -> None:
    if actual != expected:
        pytest.fail(f"{context}: expected {expected!r}, got {actual!r}")


def _fail_if_missing(item: _T, collection: Collection[_T], context: str) -> None:
    if item not in collection:
        pytest.fail(f"{context}: expected {item!r} in {collection!r}")


def _fail_if_present(item: _T, collection: Collection[_T], context: str) -> None:
    if item in collection:
        pytest.fail(f"{context}: expected {item!r} not to be present in {collection!r}")


def test_get_canonical_option() -> None:
    """Test that get_canonical_option normalizes option aliases."""
    assert get_canonical_option("--help") == "--help"
    assert get_canonical_option("-h") == "--help"
    assert get_canonical_option("--input") == "--input"
    assert get_canonical_option("--unknown-option") == "--unknown-option"


def test_is_manual_doc() -> None:
    """Test that is_manual_doc detects manual documentation options."""
    assert is_manual_doc("--help") is True
    assert is_manual_doc("-h") is True
    assert is_manual_doc("--input") is False
    assert is_manual_doc("--unknown-option") is False


def test_is_excluded_from_docs() -> None:
    """Test that is_excluded_from_docs remains compatible with manual docs."""
    assert is_excluded_from_docs("--help") is True
    assert is_excluded_from_docs("-h") is True
    assert is_excluded_from_docs("--input") is False
    assert is_excluded_from_docs("--unknown-option") is False


def test_get_option_meta() -> None:
    """Test that get_option_meta returns explicit, canonical, and empty metadata."""
    assert get_option_meta("--use-annotated") is CLI_OPTION_META["--use-annotated"]
    assert get_option_meta("--treat-dot-as-module") is CLI_OPTION_META["--no-treat-dot-as-module"]
    assert get_option_meta("--help") is None
    assert get_option_meta("-h") is None
    assert get_option_meta("--unknown-option") is None


def test_get_option_meta_returns_default_for_known_argparse_option(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that get_option_meta auto-categorizes known argparse options without metadata."""
    option = "--future-option"

    def get_future_options() -> frozenset[str]:
        return frozenset({option})

    monkeypatch.setattr(cli_options, "get_all_canonical_options", get_future_options)

    assert get_option_meta(option) == CLIOptionMeta(name=option, category=OptionCategory.GENERAL)


def test_cli_doc_slug_matches_generated_anchor_format() -> None:
    """CLI documentation slugs should match generated page and anchor paths."""
    assert get_cli_doc_slug("--frozen-dataclasses") == "frozen-dataclasses"
    assert get_cli_doc_slug("Model Customization") == "model-customization"


def test_cli_option_doc_path_links_to_generated_cli_reference() -> None:
    """CLI option documentation paths should point at generated option sections."""
    assert get_cli_option_doc_path("--output-model-type") == "model-customization.md#output-model-type"
    assert (
        get_cli_option_doc_path("--output-model-type", root="/cli-reference", extension="/")
        == "/cli-reference/model-customization/#output-model-type"
    )
    assert get_cli_option_doc_path("-h", root="/cli-reference", extension="/") == "/cli-reference/utility-options/#help"
    assert get_cli_option_doc_path("--unknown-option") is None


def test_manual_doc_links_are_rebased_for_the_generated_utility_page() -> None:
    """Manual sibling and parent links remain valid when embedded one level higher."""
    generated = build_cli_docs.generate_manual_docs_section({
        "--job": "[Sibling](../general-options.md#diff-against) [Parent](../../pyproject_toml.md)"
    })

    _fail_if_missing(
        "[Sibling](general-options.md#diff-against)",
        generated,
        "manual sibling link",
    )
    _fail_if_missing("[Parent](../pyproject_toml.md)", generated, "manual parent link")
    _fail_if_present("](../general-options.md", generated, "unrebased manual sibling link")


def test_cli_option_doc_name_preserves_boolean_variants_and_canonicalizes_aliases() -> None:
    """Boolean option variants have distinct sections, but regular aliases share one section."""
    assert get_cli_option_doc_name("--treat-dot-as-module") == "--treat-dot-as-module"
    assert get_cli_option_doc_name("--no-treat-dot-as-module") == "--no-treat-dot-as-module"
    assert get_cli_option_doc_name("--capitalise-enum-members") == "--capitalize-enum-members"


def test_generate_option_section_uses_canonical_anchor_for_aliases() -> None:
    """Alias-keyed sections should link to the canonical generated anchor."""
    section = generate_option_section(
        "--capitalise-enum-members",
        CLIDocOption(
            option_name="--capitalise-enum-members",
            examples=[
                CLIDocExample(
                    node_id="tests/main/jsonschema/test_main_jsonschema.py::test_capitalise",
                    option_description="Capitalize enum member names to UPPER_CASE format.",
                    cli_args=["--capitalise-enum-members"],
                    is_primary=True,
                ),
            ],
        ),
    )

    _fail_if_missing(
        "## `--capitalise-enum-members` {#capitalize-enum-members}",
        section,
        "--capitalise-enum-members section anchor",
    )
    _fail_if_present(
        "{#capitalise-enum-members}",
        section,
        "--capitalise-enum-members raw alias anchor",
    )


def test_generate_option_section_preserves_an_explicit_input() -> None:
    """Usage commands do not prepend the generic input to an explicit schema path."""
    section = generate_option_section(
        "--diff-against",
        CLIDocOption(
            option_name="--diff-against",
            examples=[
                CLIDocExample(
                    node_id="tests/main/test_main_input_diff.py::test_diff",
                    option_description="Compare two generated inputs.",
                    cli_args=["--input", "new.yaml", "--diff-against", "old.yaml"],
                    is_primary=True,
                ),
            ],
        ),
    )

    _fail_if_missing(
        "datamodel-codegen --input new.yaml --diff-against old.yaml",
        section,
        "explicit input usage",
    )
    _fail_if_present("--input schema.json --input", section, "duplicate placeholder input")


@pytest.mark.parametrize(
    ("input_schema", "usage"),
    [
        (
            "generation_platform/client/cli/options.yaml",
            "datamodel-codegen --input options.yaml --input-file-type openapi --client-body-arguments both",
        ),
        (None, "datamodel-codegen --input schema.json --input-file-type openapi --client-body-arguments both"),
    ],
)
def test_generate_option_section_names_the_example_input(input_schema: str | None, usage: str) -> None:
    """Usage commands name the input file the example renders, or the generic schema without one."""
    section = generate_option_section(
        "--client-body-arguments",
        CLIDocOption(
            option_name="--client-body-arguments",
            examples=[
                CLIDocExample(
                    node_id="tests/api_generation/test_client_cli.py::test_client_cli_options",
                    option_description="Choose how request bodies become method arguments.",
                    input_schema=input_schema,
                    cli_args=["--input-file-type", "openapi", "--client-body-arguments", "both"],
                    is_primary=True,
                ),
            ],
        ),
    )

    _fail_if_missing(usage, section, "example input usage")


def test_documented_related_option_prefers_existing_generated_section() -> None:
    """Related links should target generated sections, not argparse's longest alias."""
    documented_options = frozenset({
        "--collapse-root-models",
        "--no-use-union-operator",
        "--snake-case-field",
    })

    _fail_if_not_equal(
        _documented_related_option("--collapse-root-models", documented_options),
        "--collapse-root-models",
        "--collapse-root-models related option target",
    )
    _fail_if_not_equal(
        _documented_related_option("--snake-case-field", documented_options),
        "--snake-case-field",
        "--snake-case-field related option target",
    )
    _fail_if_not_equal(
        _documented_related_option("--use-union-operator", documented_options),
        "--no-use-union-operator",
        "--use-union-operator related option target",
    )


def test_related_page_tags_prefer_existing_generated_section() -> None:
    """Related page tags should keep links on generated positive BooleanOptionalAction sections."""
    documented_options = frozenset({
        "--collapse-root-models",
        "--disable-warnings",
        "--reuse-model",
        "--reuse-scope",
        "--shared-module-name",
        "--use-type-alias",
    })

    option_related_pages = scan_docs_for_cli_option_tags(documented_options)

    _fail_if_missing(
        ("model-reuse.md", "Model Reuse and Deduplication"),
        option_related_pages["--collapse-root-models"],
        "--collapse-root-models related page",
    )
    _fail_if_present(
        "--no-collapse-root-models",
        option_related_pages,
        "--no-collapse-root-models related page key",
    )


def test_format_option_link_uses_current_category_anchor() -> None:
    """Recipe links should stay on-page when the option belongs to the current category."""
    documented_options = frozenset({"--input", "--target-python-version", "--unknown-option"})

    assert _format_option_link("--input", documented_options, current_category=OptionCategory.BASE) == (
        "[`--input`](#input)"
    )
    assert (
        _format_option_link(
            "--target-python-version",
            documented_options,
            current_category=OptionCategory.TYPING,
        )
        == "[`--target-python-version`](model-customization.md#target-python-version)"
    )
    assert _format_option_link("--unknown-option", documented_options) == "`--unknown-option`"


def test_related_option_links_use_current_category_anchor() -> None:
    """Related option links should stay on-page when the option belongs to the current category."""
    option = build_cli_docs.CLIDocOption(
        option_name="--input",
        examples=[
            build_cli_docs.CLIDocExample(
                node_id="tests/test_cli_doc.py::test_input",
                option_description="Specify the input schema file path.",
                cli_args=["--input", "schema.json"],
                related_options=["--input-file-type", "--target-python-version"],
                is_primary=True,
            )
        ],
    )

    section = build_cli_docs.generate_option_section(
        "--input",
        option,
        documented_options=frozenset({"--input", "--input-file-type", "--target-python-version"}),
    )

    _fail_if_missing(
        "**Related:** [`--input-file-type`](#input-file-type), "
        "[`--target-python-version`](model-customization.md#target-python-version)",
        section,
        "--input related option links",
    )


def test_category_recipe_options_have_registered_metadata() -> None:
    """Generated recipes should not point at stale CLI option names."""
    for category, recipes in build_cli_docs.CATEGORY_RECIPES.items():
        assert isinstance(category, OptionCategory)
        assert recipes
        for recipe in recipes:
            assert recipe.options
            for option in recipe.options:
                assert get_option_meta(option) is not None


def test_homepage_recipe_quick_starts_use_curated_category_metadata() -> None:
    """README/docs-home recipes should stay bounded while linking to generated option docs."""
    section = build_cli_docs.generate_homepage_recipe_quick_starts(
        cli_reference_root="cli-reference",
        cli_reference_extension=".md",
        cli_reference_index="cli-reference/index.md",
    )

    recipe_lines = [line for line in section.splitlines() if line.startswith("- **")]

    assert section.startswith("### CLI option quick starts")
    assert len(recipe_lines) == len(build_cli_docs.HOMEPAGE_RECIPE_CATEGORIES)
    assert "- **Generate a local schema file:**" in section
    assert "[`--input`](cli-reference/base-options.md#input)" in section
    assert "[`--target-python-version`](cli-reference/model-customization.md#target-python-version)" in section
    assert "Trim GraphQL metadata fields" not in section


def test_homepage_recipe_quick_starts_support_readme_links() -> None:
    """README recipe links should target published extensionless docs URLs."""
    section = build_cli_docs.generate_homepage_recipe_quick_starts(
        cli_reference_root="https://datamodel-code-generator.koxudaxi.dev/cli-reference",
        cli_reference_extension="/",
        cli_reference_index="https://datamodel-code-generator.koxudaxi.dev/cli-reference/",
    )

    assert "[`--input`](https://datamodel-code-generator.koxudaxi.dev/cli-reference/base-options/#input)" in section
    assert "cli-reference/index.md" not in section


def test_category_recipes_render_before_option_details(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recipe sections should sit between the category option table and detailed option sections."""
    monkeypatch.setattr(
        build_cli_docs,
        "CATEGORY_RECIPES",
        {
            OptionCategory.BASE: (
                build_cli_docs.CategoryRecipe(
                    title="Read a schema",
                    description="Choose the schema and destination.",
                    options=("--input",),
                ),
            )
        },
    )
    option = build_cli_docs.CLIDocOption(
        option_name="--input",
        examples=[
            build_cli_docs.CLIDocExample(
                node_id="tests/test_cli_doc.py::test_input",
                option_description="Specify the input schema file path.",
                cli_args=["--input", "schema.json"],
                is_primary=True,
            )
        ],
    )

    page = build_cli_docs.generate_category_page(
        OptionCategory.BASE,
        {"--input": option},
        documented_options=frozenset({"--input"}),
    )

    assert "Recipes" in page
    assert page.index("Recipes") < page.index("## `--input`")
    assert "**Options:** [`--input`](#input)" in page


def test_category_page_omits_recipes_without_category_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Categories without recipe data should keep the previous option-table-to-details shape."""
    monkeypatch.setattr(build_cli_docs, "CATEGORY_RECIPES", {})
    option = build_cli_docs.CLIDocOption(
        option_name="--input",
        examples=[
            build_cli_docs.CLIDocExample(
                node_id="tests/test_cli_doc.py::test_input",
                option_description="Specify the input schema file path.",
                cli_args=["--input", "schema.json"],
                is_primary=True,
            )
        ],
    )

    page = build_cli_docs.generate_category_page(
        OptionCategory.BASE,
        {"--input": option},
        documented_options=frozenset({"--input"}),
    )

    assert "Recipes" not in page
    assert page.index("---") < page.index("## `--input`")


def test_topic_pages_group_documented_options_by_metadata() -> None:
    """Focused topic pages should group documented options by topic metadata."""
    options = {
        "--class-name": CLIDocOption(
            option_name="--class-name",
            examples=[
                CLIDocExample(
                    node_id="tests/cli_doc/test_cli_options_sync.py::test_class_name",
                    option_description="Set class name.",
                    cli_args=["--class-name", "Pet"],
                    is_primary=True,
                ),
            ],
        ),
        "--output-model-type": CLIDocOption(
            option_name="--output-model-type",
            examples=[
                CLIDocExample(
                    node_id="tests/cli_doc/test_cli_options_sync.py::test_output_model_type",
                    option_description="Set output model type.",
                    cli_args=["--output-model-type", "pydantic_v2.BaseModel"],
                    is_primary=True,
                ),
            ],
        ),
    }

    topic_options = collect_topic_options({OptionCategory.MODEL: options})
    page = generate_topic_page(
        OptionTopic.MODEL_CUSTOMIZATION,
        topic_options[OptionTopic.MODEL_CUSTOMIZATION],
    )

    _fail_if_missing("## Model Naming {#model-naming}", page, "model naming group")
    _fail_if_missing("## Model Shape {#model-shape}", page, "model shape group")
    _fail_if_missing("[`--class-name`](../model-customization.md#class-name)", page, "class name topic link")
    _fail_if_missing(
        "[`--output-model-type`](../model-customization.md#output-model-type)",
        page,
        "output model type topic link",
    )


def test_topic_index_lists_generated_topic_pages() -> None:
    """CLI reference index should link to generated focused topic pages."""
    option = CLIDocOption(
        option_name="--use-union-operator",
        examples=[
            CLIDocExample(
                node_id="tests/cli_doc/test_cli_options_sync.py::test_use_union_operator",
                option_description="Use union operator.",
                cli_args=["--use-union-operator"],
                is_primary=True,
            ),
        ],
    )

    index = generate_topic_index(collect_topic_options({OptionCategory.TYPING: {"--use-union-operator": option}}))

    _fail_if_missing(
        "[Typing Customization](topics/typing-customization.md)",
        index,
        "typing topic index link",
    )
    _fail_if_missing("Type Syntax", index, "typing topic group label")


def test_topic_page_escapes_markdown_table_descriptions() -> None:
    """Focused topic tables should not turn type syntax into Markdown links."""
    option = CLIDocOption(
        option_name="--no-use-union-operator",
        examples=[
            CLIDocExample(
                node_id="tests/cli_doc/test_cli_options_sync.py::test_no_use_union_operator",
                option_description="Use Union[X, Y] / Optional[X] instead of X | Y union operator.",
                cli_args=["--no-use-union-operator"],
                is_primary=True,
            ),
        ],
    )

    topic_options = collect_topic_options({OptionCategory.TYPING: {"--no-use-union-operator": option}})
    page = generate_topic_page(
        OptionTopic.TYPING_CUSTOMIZATION,
        topic_options[OptionTopic.TYPING_CUSTOMIZATION],
    )

    _fail_if_missing(r"Union\[X, Y\] / Optional\[X\] instead of X \| Y", page, "escaped type syntax")


def test_cli_docs_preserve_injected_doc_example_sections() -> None:
    """CLI docs regeneration should not delete docs examples injected by the examples builder."""
    generated = (
        "# CLI Reference\n\n"
        "## `--input-model` {#input-model}\n\n"
        '??? example "Examples"\n\n'
        "    **Output:**\n\n"
        "---\n\n"
        "## `--other` {#other}\n\n"
    )
    existing = (
        "# CLI Reference\n\n"
        "## `--input-model` {#input-model}\n\n"
        "<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: cli-reference.base-options.input-model.example -->\n"
        '??? example "Examples"\n\n'
        "    **Command:**\n"
        "    ```bash\n"
        "    datamodel-codegen --input-model tests.data:User\n"
        "    ```\n"
        "<!-- END AUTO-GENERATED DOC EXAMPLE: cli-reference.base-options.input-model.example -->\n\n"
        "---\n\n"
        "## `--other` {#other}\n\n"
    )

    preserved = build_cli_docs.preserve_existing_doc_example_sections(generated, existing)

    _fail_if_missing(
        "<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: cli-reference.base-options.input-model.example -->",
        preserved,
        "preserved docs example begin marker",
    )
    _fail_if_missing("datamodel-codegen --input-model tests.data:User", preserved, "preserved docs example content")
    _fail_if_present("    **Output:**\n\n---", preserved, "replaced generated fallback example")


def test_manual_docs_aggregate_rewrites_sibling_links_to_local_anchors() -> None:
    """Keep manual fragments cross-page-safe while aggregating them onto the utility page."""
    generated = build_cli_docs.generate_manual_docs_section({
        "--all-jobs": "## `--all-jobs` {#all-jobs}\n\n[`--job`](job.md#job)\n",
        "--job": "## `--job` {#job}\n\n[`--all-jobs`](all-jobs.md#all-jobs)\n",
    })

    _fail_if_missing("[`--job`](#job)", generated, "aggregated job link")
    _fail_if_missing("[`--all-jobs`](#all-jobs)", generated, "aggregated all-jobs link")
    _fail_if_present("job.md#job", generated, "manual sibling path")
    _fail_if_present("all-jobs.md#all-jobs", generated, "manual sibling path")


def test_option_section_renders_implies_and_requires_metadata() -> None:
    """Generated option docs should include relationship metadata from CLIOptionMeta."""
    section = generate_option_section(
        "--use-missing-sentinel",
        CLIDocOption(
            option_name="--use-missing-sentinel",
            examples=[
                CLIDocExample(
                    node_id="tests/cli_doc/test_cli_options_sync.py::test_use_missing_sentinel",
                    option_description="Use missing sentinel.",
                    cli_args=["--use-missing-sentinel"],
                    is_primary=True,
                ),
            ],
        ),
        documented_options=frozenset({
            "--output-model-type",
            "--target-pydantic-version",
            "--use-missing-sentinel",
        }),
    )

    assert "**Option relationships:**" in section
    assert (
        "- **Implies:** [`--target-pydantic-version`](model-customization.md#target-pydantic-version) = `2.12`"
        in section
    )
    assert (
        "- **Requires:** [`--output-model-type`](model-customization.md#output-model-type) - "
        "`--use-missing-sentinel` is only supported for `--output-model-type pydantic_v2.BaseModel` "
        "or `pydantic_v2.dataclass`." in section
    )


def test_option_section_renders_conditional_requires_metadata() -> None:
    """Relationship metadata should include source option value conditions."""
    section = generate_option_section(
        "--reuse-scope",
        CLIDocOption(
            option_name="--reuse-scope",
            examples=[
                CLIDocExample(
                    node_id="tests/cli_doc/test_cli_options_sync.py::test_reuse_scope",
                    option_description="Scope reuse.",
                    cli_args=["--reuse-scope", "tree"],
                    is_primary=True,
                ),
            ],
        ),
        documented_options=frozenset({"--reuse-model", "--reuse-scope"}),
    )

    assert (
        "- **Requires:** When `--reuse-scope=tree`, "
        "[`--reuse-model`](model-customization.md#reuse-model) enabled - "
        "`--reuse-scope=tree` has no effect without `--reuse-model`." in section
    )


def test_option_section_renders_conflicts_metadata() -> None:
    """Generated option docs should include conflict metadata from CLIOptionMeta."""
    section = generate_option_section(
        "--custom-file-header",
        CLIDocOption(
            option_name="--custom-file-header",
            examples=[
                CLIDocExample(
                    node_id="tests/cli_doc/test_cli_options_sync.py::test_custom_file_header",
                    option_description="Custom header.",
                    cli_args=["--custom-file-header", "# Header"],
                    is_primary=True,
                ),
            ],
        ),
        documented_options=frozenset({"--custom-file-header", "--custom-file-header-path"}),
    )

    assert (
        "- **Conflicts:** [`--custom-file-header-path`](template-customization.md#custom-file-header-path) - "
        "`--custom-file-header` can not be used with `--custom-file-header-path`." in section
    )


def test_relationship_summary_lists_metadata_with_links() -> None:
    """CLI index relationship summary should list cross-option metadata."""
    option = CLIDocOption(option_name="--use-missing-sentinel")
    summary = build_cli_docs.generate_relationship_summary(
        {OptionCategory.MODEL: {"--use-missing-sentinel": option}},
        documented_options=frozenset({
            "--output-model-type",
            "--target-pydantic-version",
            "--use-missing-sentinel",
        }),
    )

    _fail_if_missing("## 🔗 Option Relationships", summary, "relationship summary heading")
    _fail_if_missing(
        "| [`--use-missing-sentinel`](model-customization.md#use-missing-sentinel) "
        "| Implies | Always | "
        "[`--target-pydantic-version`](model-customization.md#target-pydantic-version) = `2.12` | - |",
        summary,
        "--use-missing-sentinel implies row",
    )
    _fail_if_missing(
        "`--use-missing-sentinel` is only supported for `--output-model-type pydantic_v2.BaseModel` "
        "or `pydantic_v2.dataclass`.",
        summary,
        "--use-missing-sentinel requires note",
    )


def test_relationship_summary_omits_empty_metadata() -> None:
    """CLI index relationship summary should not render without relationships."""
    option = CLIDocOption(option_name="--input")
    _fail_if_not_equal(
        build_cli_docs.generate_relationship_summary(
            {OptionCategory.BASE: {"--input": option}},
            documented_options=frozenset({"--input"}),
        ),
        "",
        "empty relationship summary",
    )


class TestCLIOptionMetaSync:
    """Synchronization tests for CLI_OPTION_META."""

    def test_all_registered_options_exist_in_argparse(self) -> None:
        """Verify that all options in CLI_OPTION_META exist in argparse."""
        # Use all argparse options (including aliases) because CLI_OPTION_META
        # may contain both --use-* and --no-use-* variants for BooleanOptionalAction
        argparse_options = get_all_argparse_options()
        registered = set(CLI_OPTION_META.keys())

        orphan = registered - argparse_options
        if orphan:
            pytest.fail(
                "Options in CLI_OPTION_META but not in argparse:\n"
                + "\n".join(f"  - {opt}" for opt in sorted(orphan))
                + "\n\nRemove them from CLI_OPTION_META or add them to arguments.py."
            )

    def test_manual_doc_options_exist_in_argparse(self) -> None:
        """Verify that all options in MANUAL_DOCS exist in argparse."""
        argparse_options = get_all_canonical_options()

        orphan = MANUAL_DOCS - argparse_options
        if orphan:
            pytest.fail(
                "Options in MANUAL_DOCS but not in argparse:\n"
                + "\n".join(f"  - {opt}" for opt in sorted(orphan))
                + "\n\nRemove them from MANUAL_DOCS or add them to arguments.py."
            )

    def test_no_overlap_between_meta_and_manual(self) -> None:
        """Verify that CLI_OPTION_META and MANUAL_DOCS don't overlap."""
        overlap = set(CLI_OPTION_META.keys()) & MANUAL_DOCS
        if overlap:
            pytest.fail(
                "Options in both CLI_OPTION_META and MANUAL_DOCS:\n"
                + "\n".join(f"  - {opt}" for opt in sorted(overlap))
                + "\n\nAn option should be in one or the other, not both."
            )

    def test_meta_names_match_keys(self) -> None:
        """Verify that CLIOptionMeta.name matches the dict key."""
        mismatches = []
        for key, meta in CLI_OPTION_META.items():
            if key != meta.name:
                mismatches.append(f"  Key '{key}' != meta.name '{meta.name}'")

        if mismatches:
            pytest.fail("CLIOptionMeta.name mismatches:\n" + "\n".join(mismatches))

    def test_option_relations_reference_argparse_options(self) -> None:
        """Verify that option relation metadata points at real argparse options."""
        argparse_options = get_all_argparse_options()
        missing = [
            f"  {source} {relation_kind} {relation.option}"
            for source, meta in CLI_OPTION_META.items()
            for relation_kind in OPTION_RELATION_KINDS
            for relation in getattr(meta, relation_kind)
            if relation.option not in argparse_options
        ]

        if missing:
            pytest.fail(
                "CLI option relation targets missing from argparse:\n"
                + "\n".join(sorted(missing))
                + "\n\nRemove the relation or add the target option to arguments.py."
            )

    def test_option_topic_and_group_are_known_and_non_empty(self) -> None:
        """Verify that optional topic/group metadata uses known non-empty values."""
        for attr, enum_type in (("topic", OptionTopic), ("group", OptionGroup)):
            for option, meta in CLI_OPTION_META.items():
                if (value := getattr(meta, attr)) is None:
                    continue
                if not isinstance(value, enum_type):
                    pytest.fail(f"{option} {attr} must be an {enum_type.__name__}, got {value!r}")
                if not value.value:
                    pytest.fail(f"{option} {attr} value must not be empty")

    def test_option_topic_groups_are_allowed(self) -> None:
        """Verify that topic metadata only uses groups allowed for that topic."""
        for option, meta in CLI_OPTION_META.items():
            if (topic := meta.topic) is None:
                continue
            if (group := meta.group) is None:
                pytest.fail(f"{option} topic {topic.value!r} must also set a group")
            if (allowed_groups := OPTION_TOPIC_ALLOWED_GROUPS.get(topic)) is None:
                pytest.fail(f"{option} topic has no allowed groups: {topic.value!r}")
            _fail_if_missing(
                group,
                allowed_groups,
                f"{option} group for topic {topic.value!r}",
            )

    def test_focused_topics_nav_matches_option_topics(self) -> None:
        """Verify that focused topic docs stay linked from the site nav."""
        root = Path(__file__).resolve().parents[2]
        with (root / "zensical.toml").open("rb") as f:
            nav = tomllib.load(f)["project"]["nav"]

        cli_reference_nav = next(value for item in nav for title, value in item.items() if title == "CLI Reference")
        focused_topics_nav = next(
            value for item in cli_reference_nav for title, value in item.items() if title == "Focused Topics"
        )
        expected_nav = [
            {build_cli_docs._topic_title(topic): f"cli-reference/topics/{topic.value}.md"}
            for topic in build_cli_docs.OPTION_TOPIC_ORDER
        ]

        _fail_if_not_equal(focused_topics_nav, expected_nav, "Focused Topics nav")

    def test_all_argparse_options_are_documented_or_excluded(self) -> None:
        """Verify that all argparse options are either documented or explicitly excluded.

        This test fails when a new CLI option is added to arguments.py
        but not added to CLI_OPTION_META or MANUAL_DOCS.
        """
        argparse_options = get_all_canonical_options()
        documented = set(CLI_OPTION_META.keys())
        manual = MANUAL_DOCS
        covered = documented | manual
        missing = argparse_options - covered

        if missing:
            pytest.fail(
                "CLI options in argparse but not in CLI_OPTION_META or MANUAL_DOCS:\n"
                + "\n".join(f"  - {opt}" for opt in sorted(missing))
                + "\n\nAdd entries to CLI_OPTION_META in cli_options.py, "
                "or add to MANUAL_DOCS if they should have manual documentation."
            )

    def test_canonical_option_determination_is_stable(self) -> None:
        """Verify that canonical option determination is deterministic.

        The canonical option should be the longest option string for each action.
        If multiple options have the same length, the lexicographically last one
        should be chosen for stability.
        """
        for action in argument_parser._actions:
            if not action.option_strings:
                continue

            sorted_opts = sorted(action.option_strings, key=_canonical_option_key)
            canonical = sorted_opts[-1]

            re_sorted = sorted(action.option_strings, key=_canonical_option_key)
            assert sorted_opts == re_sorted, f"Canonical determination is not stable for {action.option_strings}"
            assert canonical == re_sorted[-1], f"Canonical mismatch for {action.option_strings}"
