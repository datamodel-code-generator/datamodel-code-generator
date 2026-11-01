"""The command header must redact whatever spelling argparse accepted."""

from __future__ import annotations

import pytest

from datamodel_code_generator.__main__ import _redact_command_args


@pytest.mark.parametrize(
    "args",
    [
        ["--http-headers", "Authorization: Bearer secret"],
        ["--http-header", "Authorization: Bearer secret"],
        ["--http-headers=Authorization: Bearer secret"],
        ["--http-header=Authorization: Bearer secret"],
        ["--http-query-parameters", "api_key=secret"],
        ["--http-query-param", "api_key=secret"],
        ["--http-query-parameter=api_key=secret"],
        ["--ur", "https://user:pass@example.com/schema.json"],
        ["--ur=https://user:pass@example.com/schema.json"],
    ],
)
def test_an_accepted_spelling_of_a_sensitive_option_is_redacted(args: list[str]) -> None:
    """argparse takes any unambiguous prefix, so redaction cannot match on the typed spelling alone."""
    redacted = _redact_command_args(args)

    assert "secret" not in " ".join(redacted)
    assert "user:pass" not in " ".join(redacted)


def test_options_after_a_sensitive_value_are_kept() -> None:
    """Only the value of the sensitive option is replaced."""
    assert _redact_command_args(
        ["--http-header", "Authorization: Bearer secret", "--encoding", "utf-8"]
    ) == ["--http-header", "<redacted>", "--encoding", "utf-8"]


@pytest.mark.parametrize(
    "args",
    [
        ["--encoding", "utf-8"],
        ["--enc", "utf-8"],
        ["--input", "schema.json", "--output", "model.py"],
    ],
)
def test_harmless_arguments_are_untouched(args: list[str]) -> None:
    assert _redact_command_args(args) == args
