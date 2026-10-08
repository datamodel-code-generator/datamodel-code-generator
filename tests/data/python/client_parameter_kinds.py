"""Send and read text values whose kinds the generated models' types give, in every backend."""

from __future__ import annotations

import importlib
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from tests.data.python.client_runtime import Exchange, argument, raw_response, record, request_body

if TYPE_CHECKING:
    from types import ModuleType

_LONG: Final = "12345678901234567890.123"
_ARGUMENTS: Final[tuple[tuple[str, object], ...]] = (
    ("amount", Decimal(_LONG)),
    ("amount", Decimal("1.50")),
    ("amounts", [Decimal(_LONG), Decimal("1.50")]),
    ("count", 12),
    ("flag", True),
    ("mixed", 5),
    ("mixed", "5"),
)
_WIRE_ARGUMENTS: Final[tuple[tuple[str, object], ...]] = (("step", 0.25), ("ratio", 2.2), ("level", 2))
_HEADERS: Final = (
    ("X-Amount", _LONG),
    ("X-Amount", "1.50"),
    ("X-Amounts", f"{_LONG},1.50"),
    ("X-Step", "12345678901234567890.12"),
    ("X-Ratio", "2.2"),
    ("X-Ratio", "0.5"),
    ("X-Level", "2"),
    ("X-Count", "12"),
    ("X-Flag", "true"),
    ("X-Mixed", "5"),
    ("X-Mixed", "abc"),
)
_FIELDS: Final = (
    ("amount", _LONG),
    ("amounts", "1.50"),
    ("amounts", "2"),
    ("step", "12345678901234567890.12"),
    ("level", "2"),
    ("count", "12"),
    ("flag", "true"),
    ("mixed", "5"),
    ("either", "7"),
)
_TEXTS: Final = (*_FIELDS[:-2], ("mixed", "abc"), ("either", "abc"))
_FORM: Final = "application/x-www-form-urlencoded"
_SAVED: Final = {"amount": _LONG, "amounts": ["1.50", "2"], "step": 0.25, "level": 2, "count": 12, "flag": True}


def _pairs(*fields: tuple[str, str]) -> bytes:
    return "&".join(f"{name}={text}" for name, text in fields).encode()


def _parts(*fields: tuple[str, str]) -> bytes:
    return b"".join(
        f'--b1\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{text}\r\n'.encode() for name, text in fields
    ) + b"--b1--\r\n"


def parameter_kinds(package: ModuleType, lines: list[str]) -> None:
    """Send query values and form fields, and read headers, form fields and parts, as each model type reads text."""
    types = importlib.import_module(f"{package.__name__}.types.values")
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        for name, value in _ARGUMENTS:
            exchange.respond(raw_response(204))
            record(
                lines,
                f"send {name} {value!r}",
                lambda name=name, value=value: api.values.get_values(**{name: value}),
            )
        for name, wire in _WIRE_ARGUMENTS:
            exchange.respond(raw_response(204))
            record(
                lines,
                f"send {name} of {wire!r}",
                lambda name=name, wire=wire: api.values.get_values(
                    **{name: argument(package, "getValues", "query", name, wire)}
                ),
            )
            exchange.responders.clear()
        for name, text in _HEADERS:
            exchange.respond(raw_response(204, **{name: text}))
            info = api.values.with_raw_response.get_values().info
            record(
                lines,
                f"read {name} {text!r}",
                lambda info=info, name=name: types.decode_get_values_header(info, name=name),
            )
        for mixed in (5, "5"):
            exchange.respond(raw_response(200, b"mixed=5&either=7", _FORM))
            record(
                lines,
                f"send fields with {mixed!r}",
                lambda mixed=mixed: api.values.save_values(
                    body=request_body(package, "saveValues", _FORM, {**_SAVED, "mixed": mixed, "either": mixed})
                ),
            )
        body = request_body(package, "saveValues", _FORM, {**_SAVED, "mixed": 5, "either": 5})
        for label, content, media in (
            ("read fields", _pairs(*_FIELDS), _FORM),
            ("read fields of text", _pairs(*_TEXTS), _FORM),
            ("read parts", _parts(*_FIELDS), "multipart/form-data; boundary=b1"),
            ("read parts of text", _parts(*_TEXTS[:-2], _FIELDS[-2], _TEXTS[-1]), "multipart/form-data; boundary=b1"),
            ("read parts of text in an untyped member", _parts(*_TEXTS[-2:]), "multipart/form-data; boundary=b1"),
        ):
            exchange.respond(raw_response(200, content, media))
            record(lines, label, lambda: api.values.save_values(body=body))
        exchange.responders.clear()
