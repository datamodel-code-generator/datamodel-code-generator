"""Literal and temporal helpers for XML Schema parsing."""

from __future__ import annotations

import contextlib
import datetime as datetime_module
import re
from decimal import Decimal
from math import isfinite

from datamodel_code_generator.imports import Import
from datamodel_code_generator.python_literal import (
    PythonRuntimeExpression,
    _safe_non_finite_float,
)

XML_DATE_PATTERN = re.compile(r"^(?P<date>-?\d{4,}-\d{2}-\d{2})(?:Z|[+-]\d{2}:\d{2})?$")
DAY_TIME_DURATION_PATTERN = re.compile(
    r"^(?P<sign>-)?P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)
XSD_WHITESPACE_CHARS = " \t\n\r"
IMPORT_DATETIME_MODULE = Import(import_="datetime", alias="datetime_module")


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
        case "INF" | "+INF":
            return _safe_non_finite_float(number) if source_safe_non_finite else number
        case "-INF":
            return _safe_non_finite_float(number) if source_safe_non_finite else number
        case "NaN":
            return _safe_non_finite_float(number) if source_safe_non_finite else number
    return number if isfinite(number) else None


def _safe_bool(value: str) -> bool | None:
    match value.strip(XSD_WHITESPACE_CHARS):
        case "true" | "1":
            return True
        case "false" | "0":
            return False
    return None


def _datetime_expression(suffix: str, *, prefix: str = "") -> _PythonExpression:
    return _PythonExpression(IMPORT_DATETIME_MODULE, prefix, suffix)


def _normalize_timezone(value: str) -> str:
    return f"{value[:-1]}+00:00" if value.endswith("Z") else value


def _safe_date_expression(value: str) -> _PythonExpression | None:
    value = value.strip(XSD_WHITESPACE_CHARS)
    date_match = XML_DATE_PATTERN.match(value)
    if date_match is None:
        return None
    date_value = date_match["date"]
    if value != date_value:
        return None
    with contextlib.suppress(ValueError):
        datetime_module.date.fromisoformat(date_value)
        return _datetime_expression(f".date.fromisoformat({date_value!r})")
    return None


def _safe_time_expression(value: str) -> _PythonExpression | None:
    value = value.strip(XSD_WHITESPACE_CHARS)
    normalized = _normalize_timezone(value)
    with contextlib.suppress(ValueError):
        datetime_module.time.fromisoformat(normalized)
        return _datetime_expression(f".time.fromisoformat({normalized!r})")
    return None


def _safe_datetime_expression(value: str) -> _PythonExpression | None:
    value = value.strip(XSD_WHITESPACE_CHARS)
    normalized = _normalize_timezone(value)
    with contextlib.suppress(ValueError):
        datetime_module.datetime.fromisoformat(normalized)
        return _datetime_expression(f".datetime.fromisoformat({normalized!r})")
    return None


def _safe_day_time_duration_expression(value: str) -> _PythonExpression | None:
    value = value.strip(XSD_WHITESPACE_CHARS)
    duration_match = DAY_TIME_DURATION_PATTERN.match(value)
    if duration_match is None:
        return None

    days = duration_match["days"]
    hours = duration_match["hours"]
    minutes = duration_match["minutes"]
    seconds = duration_match["seconds"]
    if not any((days, hours, minutes, seconds)):
        return None

    arguments: list[str] = []
    if days:
        arguments.append(f"days={int(days)}")
    if hours:
        arguments.append(f"hours={int(hours)}")
    if minutes:
        arguments.append(f"minutes={int(minutes)}")
    if seconds:
        seconds_in_microseconds = Decimal(seconds) * 1_000_000
        integral_microseconds = seconds_in_microseconds.to_integral_value()
        if seconds_in_microseconds != integral_microseconds:
            return None
        whole_seconds, microseconds = divmod(int(integral_microseconds), 1_000_000)
        if whole_seconds:
            arguments.append(f"seconds={whole_seconds}")
        if microseconds:
            arguments.append(f"microseconds={microseconds}")

    arguments_text = ", ".join(arguments) if arguments else "0"
    return _datetime_expression(
        f".timedelta({arguments_text})",
        prefix="-" if duration_match["sign"] else "",
    )
