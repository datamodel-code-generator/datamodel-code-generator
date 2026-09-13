"""CI-only contracts for the generated-source formatter fast path."""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

import datamodel_code_generator._builtin_formatter as builtin_formatter
import datamodel_code_generator.config as config_module
from datamodel_code_generator.parser.base import Parser
from tests.conftest import assert_output

EXPECTED_PATH = Path(__file__).parents[1] / "data" / "expected" / "builtin_formatter"
PYTHON_DATA_PATH = Path(__file__).parents[1] / "data" / "python"


def _assigned_value(tree: ast.AST, attribute_name: str) -> ast.expr:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr == attribute_name
            for target in node.targets
        ):
            return node.value
    msg = f"Parser.__init__ no longer assigns self.{attribute_name}"  # pragma: no cover
    raise AssertionError(  # pragma: no cover
        msg
    )


def _attributes_on(node: ast.AST, object_name: str) -> set[str]:
    return {
        child.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name) and child.value.id == object_name
    }


def _generated_formatter_guarded_config_fields() -> set[str]:
    init_tree = ast.parse(textwrap.dedent(inspect.getsource(Parser.__init__)))
    parse_tree = ast.parse(textwrap.dedent(inspect.getsource(Parser._Parser__prepare_parse)))
    configured_types = _assigned_value(init_tree, "_configured_generation_types_are_builtin")
    standard_templates = _assigned_value(parse_tree, "_uses_standard_generation_templates")
    config_by_instance_attribute: dict[str, str] = {}
    for node in ast.walk(init_tree):
        match node:
            case ast.Assign(targets=targets, value=value):
                pass
            case ast.AnnAssign(target=target, value=value) if value is not None:
                targets = [target]
            case _:
                continue
        for target in targets:
            match target, value:
                case (
                    ast.Attribute(value=ast.Name(id="self"), attr=instance_attribute),
                    ast.Attribute(value=ast.Name(id="config"), attr=config_field)
                    | ast.Call(func=ast.Attribute(value=ast.Name(id="config"), attr=config_field)),
                ):
                    config_by_instance_attribute[instance_attribute] = config_field
    guarded_fields = (
        _attributes_on(configured_types, "config")
        | _attributes_on(standard_templates, "parser_config")
        | {
            child.attr
            for child in ast.walk(standard_templates)
            if isinstance(child, ast.Attribute)
            and isinstance(child.value, ast.NamedExpr)
            and isinstance(child.value.target, ast.Name)
            and child.value.target.id == "parser_config"
        }
    )
    guarded_fields.update(
        config_by_instance_attribute[attribute]
        for attribute in _attributes_on(configured_types, "self")
        if attribute in config_by_instance_attribute
    )
    config_field_names = set(config_module.GraphQLParserConfig.model_fields)
    guarded_fields.update(
        child.value
        for child in ast.walk(configured_types)
        if isinstance(child, ast.Constant) and isinstance(child.value, str) and child.value in config_field_names
    )
    return guarded_fields


def test_generated_formatter_parser_config_contract() -> None:
    """Require every new parser option to receive an explicit fast-path audit in CI."""
    guarded_fields = _generated_formatter_guarded_config_fields()
    config_types = sorted(
        (
            value
            for value in vars(config_module).values()
            if isinstance(value, type)
            and value.__module__ == config_module.__name__
            and issubclass(value, config_module.ParserConfig)
        ),
        key=lambda config_type: (len(config_type.mro()), config_type.__name__),
    )
    report: list[str] = []
    inherited_fields: set[str] = set()
    for config_type in config_types:
        report.append(f"[{config_type.__name__}]")
        for field_name in sorted(set(config_type.model_fields) - inherited_fields):
            classification = "guarded" if field_name in guarded_fields else "reviewed-safe"
            report.append(f"{classification} {field_name}")
        report.append("")
        inherited_fields.update(config_type.model_fields)

    assert_output(
        "\n".join(report),
        EXPECTED_PATH / "parser_config_contract.txt",
    )


def test_generated_formatter_matches_full_formatter_corpus() -> None:
    """Keep the fast helper byte-identical to the conventional formatter on generated source."""
    formatted_cases: list[str] = []
    corpus = (PYTHON_DATA_PATH / "builtin_formatter_generated_corpus.txt").read_text(encoding="utf-8")
    for case in corpus.split("\n\f\n"):
        name, source = case.split("\n", 1)
        full = builtin_formatter.apply_builtin_formatter(source)
        fast = builtin_formatter._try_apply_builtin_generated_formatter(
            source,
            line_length=builtin_formatter.DEFAULT_LINE_LENGTH,
            known_first_party=builtin_formatter.DEFAULT_KNOWN_FIRST_PARTY,
            wrap_string_literal=False,
            string_normalization=False,
            python_version=None,
        )
        if fast is None:  # pragma: no cover
            pytest.fail(f"Generated formatter corpus case unexpectedly fell back: {name}")
        if fast != full:  # pragma: no cover
            pytest.fail(f"Generated formatter corpus case differs from full formatter: {name}")
        formatted_cases.append(f"[{name}]\n{fast}")

    assert_output(
        "\n".join(formatted_cases),
        EXPECTED_PATH / "generated_corpus.txt",
    )
