"""Tests for XML Schema parser helpers."""

from __future__ import annotations

from typing import Any

import pytest

from datamodel_code_generator.parser import _xmlschema_literals
from datamodel_code_generator.parser.xmlschema import (
    _XMLSCHEMA_LITERAL_REEXPORTS,
    DAY_TIME_DURATION_PATTERN,
    IMPORT_DATETIME_MODULE,
    XML_DATE_PATTERN,
    XSD_WHITESPACE_CHARS,
    XMLSchemaParser,
    _collect_python_expression_imports,
    _datetime_expression,
    _normalize_timezone,
    _PythonExpression,
    _safe_bool,
    _safe_date_expression,
    _safe_datetime_expression,
    _safe_day_time_duration_expression,
    _safe_float,
    _safe_time_expression,
)
from datamodel_code_generator.python_literal import runtime_expression_imports
from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, XML_SCHEMA_DATA_PATH


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize(
    ("reexported", "original"),
    [
        (_PythonExpression, _xmlschema_literals._PythonExpression),
        (_collect_python_expression_imports, runtime_expression_imports),
        (_safe_bool, _xmlschema_literals._safe_bool),
        (_safe_float, _xmlschema_literals._safe_float),
        (_safe_date_expression, _xmlschema_literals._safe_date_expression),
        (_safe_time_expression, _xmlschema_literals._safe_time_expression),
        (_safe_datetime_expression, _xmlschema_literals._safe_datetime_expression),
        (_safe_day_time_duration_expression, _xmlschema_literals._safe_day_time_duration_expression),
        (_datetime_expression, _xmlschema_literals._datetime_expression),
        (_normalize_timezone, _xmlschema_literals._normalize_timezone),
        (XML_DATE_PATTERN, _xmlschema_literals.XML_DATE_PATTERN),
        (DAY_TIME_DURATION_PATTERN, _xmlschema_literals.DAY_TIME_DURATION_PATTERN),
        (XSD_WHITESPACE_CHARS, _xmlschema_literals.XSD_WHITESPACE_CHARS),
        (IMPORT_DATETIME_MODULE, _xmlschema_literals.IMPORT_DATETIME_MODULE),
    ],
)
def test_xmlschema_literal_helpers_are_re_exported_by_identity(reexported: object, original: object) -> None:
    """Keep underscore helper imports stable through the xmlschema shim."""
    assert reexported is original


@pytest.mark.allow_direct_assert
def test_xmlschema_literal_reexport_contract_is_explicit() -> None:
    """Keep compatibility re-exports listed as an explicit module contract."""
    assert dict(_XMLSCHEMA_LITERAL_REEXPORTS) == {
        "DAY_TIME_DURATION_PATTERN": DAY_TIME_DURATION_PATTERN,
        "IMPORT_DATETIME_MODULE": IMPORT_DATETIME_MODULE,
        "XML_DATE_PATTERN": XML_DATE_PATTERN,
        "XSD_WHITESPACE_CHARS": XSD_WHITESPACE_CHARS,
        "_datetime_expression": _datetime_expression,
        "_normalize_timezone": _normalize_timezone,
        "_collect_python_expression_imports": _collect_python_expression_imports,
    }


def test_xmlschema_parser_config_none_renders_non_finite_enum() -> None:
    """Generate source-safe non-finite enum values through the config-free parser path."""
    parser = XMLSchemaParser(XML_SCHEMA_DATA_PATH / "non_finite_enum.xsd", config=None)

    assert_output(
        f"{parser.parse(format_=False)}\n",
        DATA_PATH / "expected/parser/xmlschema/config_none_non_finite_enum.py",
    )


def test_xmlschema_parser_config_none_resolves_non_finite_enum_default() -> None:
    """Resolve a non-finite default to its enum member through the parser path."""
    parser = XMLSchemaParser(
        XML_SCHEMA_DATA_PATH / "non_finite_inline_enum_default.xsd",
        config=None,
        set_default_enum_member=True,
    )

    assert_output(
        f"{parser.parse(format_=False)}\n",
        DATA_PATH / "expected/parser/xmlschema/config_none_non_finite_inline_enum_default.py",
    )


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize("value", ["inf", "+inf", "-inf", "nan"])
def test_safe_float_rejects_python_only_non_finite_literals(value: str) -> None:
    """Reject non-finite spellings outside the XML Schema lexical space."""
    assert _safe_float(value) is None


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" INF ", float("inf")),
        ("\t+INF\n", float("inf")),
        ("\r-INF\t", float("-inf")),
        (" 1.5 ", 1.5),
    ],
)
def test_safe_float_strips_xml_schema_whitespace(value: str, expected: float) -> None:
    """Strip XML Schema whitespace before parsing supported float literals."""
    assert _safe_float(value) == expected


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" true ", True),
        ("\t1\n", True),
        ("\rfalse\t", False),
        ("\n0 ", False),
    ],
)
def test_safe_bool_collapses_xml_schema_whitespace(value: str, expected: bool) -> None:
    """Apply XML Schema whiteSpace=collapse before parsing boolean literals."""
    assert _safe_bool(value) is expected


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize("value", ["True", "False", " true false "])
def test_safe_bool_rejects_values_outside_xml_schema_lexical_space(value: str) -> None:
    """Reject Python-only spellings and invalid collapsed boolean values."""
    assert _safe_bool(value) is None


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize(
    ("parse", "value"),
    [
        (_safe_date_expression, "not-a-date"),
        (_safe_date_expression, "2026-02-31"),
        (_safe_date_expression, "2026-06-04+09:00"),
        (_safe_time_expression, "25:00:00"),
        (_safe_datetime_expression, "2026-02-31T00:00:00"),
        (_safe_day_time_duration_expression, "1D"),
        (_safe_day_time_duration_expression, "P"),
        (_safe_day_time_duration_expression, "PT0.0000001S"),
    ],
)
def test_temporal_expression_helpers_reject_invalid_lexical_values(parse: Any, value: str) -> None:
    """Reject temporal defaults outside the supported XML Schema lexical space."""
    assert parse(value) is None


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize(
    ("parse", "value", "expected"),
    [
        (_safe_date_expression, "2026-06-04", "datetime_module.date.fromisoformat('2026-06-04')"),
        (_safe_time_expression, "14:30:00", "datetime_module.time.fromisoformat('14:30:00')"),
        (_safe_time_expression, "14:30:00Z", "datetime_module.time.fromisoformat('14:30:00+00:00')"),
        (
            _safe_datetime_expression,
            "2026-06-04T14:30:00",
            "datetime_module.datetime.fromisoformat('2026-06-04T14:30:00')",
        ),
        (
            _safe_datetime_expression,
            "2026-06-04T14:30:00Z",
            "datetime_module.datetime.fromisoformat('2026-06-04T14:30:00+00:00')",
        ),
    ],
)
def test_temporal_expression_helpers_parse_supported_lexical_values(parse: Any, value: str, expected: str) -> None:
    """Parse supported XML Schema temporal lexical values."""
    expression = parse(value)

    assert expression is not None
    assert repr(expression) == expected


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize(
    ("parse", "value", "expected"),
    [
        (_safe_date_expression, " 2026-06-04 ", "datetime_module.date.fromisoformat('2026-06-04')"),
        (_safe_time_expression, "\t14:30:00Z\n", "datetime_module.time.fromisoformat('14:30:00+00:00')"),
        (
            _safe_datetime_expression,
            "\r2026-06-04T14:30:00Z\t",
            "datetime_module.datetime.fromisoformat('2026-06-04T14:30:00+00:00')",
        ),
        (_safe_day_time_duration_expression, " PT1S ", "datetime_module.timedelta(seconds=1)"),
    ],
)
def test_temporal_expression_helpers_strip_xml_schema_whitespace(parse: Any, value: str, expected: str) -> None:
    """Strip XML Schema whitespace before parsing supported temporal literals."""
    expression = parse(value)

    assert expression is not None
    assert repr(expression) == expected


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("P1D", "datetime_module.timedelta(days=1)"),
        ("PT2H", "datetime_module.timedelta(hours=2)"),
        ("PT3M", "datetime_module.timedelta(minutes=3)"),
        ("PT4S", "datetime_module.timedelta(seconds=4)"),
        ("PT0.5S", "datetime_module.timedelta(microseconds=500000)"),
        ("-P1D", "-datetime_module.timedelta(days=1)"),
        (
            "P1DT2H3M4.5S",
            "datetime_module.timedelta(days=1, hours=2, minutes=3, seconds=4, microseconds=500000)",
        ),
    ],
)
def test_safe_day_time_duration_expression_parses_supported_components(value: str, expected: str) -> None:
    """Parse XML Schema dayTimeDuration components into datetime.timedelta expressions."""
    expression = _safe_day_time_duration_expression(value)

    assert expression is not None
    assert repr(expression) == expected


@pytest.mark.allow_direct_assert
def test_collect_python_expression_imports_from_dict_values() -> None:
    """Collect imports from Python expressions nested in mapping defaults."""
    expression = _safe_date_expression("2026-06-04")

    assert expression is not None
    assert _collect_python_expression_imports({"eventDate": expression}) == expression.imports
