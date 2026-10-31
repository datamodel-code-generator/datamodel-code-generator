"""Literal and temporal helpers for XML Schema parsing."""

from __future__ import annotations

import contextlib
import datetime as datetime_module
import re
from decimal import Decimal
from math import isfinite

from datamodel_code_generator import imports
from datamodel_code_generator._python_syntax import call_text, keyword_argument
from datamodel_code_generator.python_literal import (
    PythonRuntimeExpression,
    _converted_float,
    _datetime_expression,
    _isoformat_expression,
)

XML_DATE_PATTERN = re.compile(r"^(?P<date>-?\d{4,}-\d{2}-\d{2})(?:Z|[+-]\d{2}:\d{2})?$")
DAY_TIME_DURATION_PATTERN = re.compile(
    r"^(?P<sign>-)?P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)
XSD_WHITESPACE_CHARS = " \t\n\r"
IMPORT_DATETIME_MODULE = imports.IMPORT_DATETIME_MODULE


# Compatibility aliases for existing parser integrations. New runtime expressions
# retain their import identity in the shared source-literal representation.
_PythonExpression = PythonRuntimeExpression


def _safe_float(value: str, *, source_safe_non_finite: bool = False) -> float | None:
    value = value.strip(XSD_WHITESPACE_CHARS)
    try:
        number = float(value)
    except ValueError:
        return None
    match value:
        case "INF" | "+INF" | "-INF" | "NaN":
            return _converted_float(number, source_safe_non_finite=source_safe_non_finite)
    return number if isfinite(number) else None


def _safe_bool(value: str) -> bool | None:
    match value.strip(XSD_WHITESPACE_CHARS):
        case "true" | "1":
            return True
        case "false" | "0":
            return False
    return None


def _normalize_timezone(value: str) -> str:
    return f"{value[:-1]}+00:00" if value.endswith("Z") else value


def _safe_isoformat_expression(kind: str, value: str) -> _PythonExpression | None:
    """Return the expression that builds ``kind`` from a value Python reads, with ``Z`` as ``+00:00``."""
    normalized = _normalize_timezone(value.strip(XSD_WHITESPACE_CHARS))
    with contextlib.suppress(ValueError):
        getattr(datetime_module, kind).fromisoformat(normalized)
        return _isoformat_expression(kind, normalized)
    return None


def _safe_date_expression(value: str) -> _PythonExpression | None:
    value = value.strip(XSD_WHITESPACE_CHARS)
    if (date_match := XML_DATE_PATTERN.match(value)) is None or value != date_match["date"]:
        return None
    return _safe_isoformat_expression("date", value)


def _safe_time_expression(value: str) -> _PythonExpression | None:
    return _safe_isoformat_expression("time", value)


def _safe_datetime_expression(value: str) -> _PythonExpression | None:
    return _safe_isoformat_expression("datetime", value)


def _safe_day_time_duration_expression(value: str) -> _PythonExpression | None:
    value = value.strip(XSD_WHITESPACE_CHARS)
    duration_match = DAY_TIME_DURATION_PATTERN.match(value)
    if duration_match is None:
        return None

    if not any(duration_match.group("days", "hours", "minutes", "seconds")):
        return None

    arguments = [
        keyword_argument(unit, str(int(amount)))
        for unit in ("days", "hours", "minutes")
        if (amount := duration_match[unit])
    ]
    if seconds := duration_match["seconds"]:
        seconds_in_microseconds = Decimal(seconds) * 1_000_000
        integral_microseconds = seconds_in_microseconds.to_integral_value()
        if seconds_in_microseconds != integral_microseconds:
            return None
        whole_seconds, microseconds = divmod(int(integral_microseconds), 1_000_000)
        if whole_seconds:
            arguments.append(f"seconds={whole_seconds}")
        if microseconds:
            arguments.append(f"microseconds={microseconds}")

    return _datetime_expression(
        call_text(".timedelta", arguments or ("0",)),
        prefix="-" if duration_match["sign"] else "",
    )
