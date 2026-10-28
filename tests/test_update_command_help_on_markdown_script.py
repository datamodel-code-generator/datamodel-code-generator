"""Tests for generated Markdown section updates."""

from __future__ import annotations

import pytest

from scripts import update_command_help_on_markdown


@pytest.mark.allow_direct_assert
def test_inject_recipe_quick_starts_replaces_ordered_markers() -> None:
    """Recipe replacement should ignore end-marker text before the begin marker."""
    markdown = (
        f"Earlier literal {update_command_help_on_markdown.RECIPE_END_MARK}\n"
        f"{update_command_help_on_markdown.RECIPE_START_MARK}\n"
        "stale\n"
        f"{update_command_help_on_markdown.RECIPE_END_MARK}\n"
        "After\n"
    )

    rendered = update_command_help_on_markdown.inject_recipe_quick_starts(
        markdown,
        cli_reference_root="cli-reference",
        cli_reference_extension=".md",
        cli_reference_index="cli-reference/index.md",
    )

    assert "stale" not in rendered
    assert rendered.startswith(f"Earlier literal {update_command_help_on_markdown.RECIPE_END_MARK}\n")
    assert rendered.count(update_command_help_on_markdown.RECIPE_START_MARK) == 1
    assert rendered.count(update_command_help_on_markdown.RECIPE_END_MARK) == 2
    assert "[`--input`](cli-reference/base-options.md#input)" in rendered
