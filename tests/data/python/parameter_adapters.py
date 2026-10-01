"""Parameter adapters that carry a value as one percent-encoded compact JSON fragment, for any generated package.

Each adapter reaches the public `model_codecs` module of the generated package that calls it, so one fixture
serves every package a test generates.
"""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote, unquote

if TYPE_CHECKING:
    from types import ModuleType


def _public(plan: object) -> ModuleType:
    """Return the public `model_codecs` module of the package whose runtime made the plan."""
    return importlib.import_module(f"{type(plan).__module__.partition('._runtime.')[0]}.model_codecs")


def _key(name: bytes, location: str) -> bytes:
    """Return a raw parameter name as its location compares it: header names ignore case."""
    return name.lower() if location == "header" else name


class JsonParameter:
    """Carry one parameter of one location as compact JSON, percent-encoded once."""

    api_version: Literal[1] = 1

    def __init__(self, location: str, style: str, explode: bool, kind: str, *, empty: bool = False) -> None:
        """Keep the one location, style, explode value, and wire kind the adapter declares."""
        self._declared = {
            "locations": (location,),
            "styles": (style,),
            "explode_values": (explode,),
            "value_kinds": (kind,),
            "supports_empty_containers": empty,
        }

    def capabilities(self, *, plan: object) -> object:
        """Report the declared capabilities as the calling package's record."""
        return _public(plan).ParameterCodecCapabilities(**self._declared)

    def encode_parameter(self, *, value: object, plan: object, context: object) -> object:  # noqa: ARG002
        """Return one fragment named after the parameter, or unnamed in a path, holding the value's JSON."""
        public = _public(plan)
        text = quote(json.dumps(public.thaw_wire(value), separators=(",", ":"), ensure_ascii=False), safe="")
        name = None if plan.location == "path" else plan.name.encode()
        fragment = public.ParameterFragment(name=name, value=text.encode())
        return public.FragmentContribution(location=plan.location, ordered_fragments=(fragment,))

    def decode_parameter(self, *, raw: object, plan: object, context: object) -> object:  # noqa: ARG002
        """Return the value of the one fragment named after the parameter, or UNSET without one."""
        public = _public(plan)
        name = _key(plan.name.encode(), plan.location)
        values = [
            fragment.value
            for fragment in raw.fragments
            if fragment.name is None or _key(fragment.name, plan.location) == name
        ]
        if not values:
            return public.UNSET
        if len(values) > 1:
            msg = f"The {plan.location} parameter {plan.name} appears more than once"
            raise public.ParameterEncodingError(msg)
        try:
            return public.freeze_wire(json.loads(unquote(values[0].decode("ascii"), errors="strict")))
        except ValueError as error:
            msg = f"The {plan.location} parameter {plan.name} is not percent-encoded JSON"
            raise public.ParameterEncodingError(msg) from error


def json_cookie() -> JsonParameter:
    """Carry a form cookie array without explosion, whose builtin form OpenAPI leaves undefined."""
    return JsonParameter("cookie", "form", False, "array", empty=True)


def json_query() -> JsonParameter:
    """Carry a nested deepObject query object, which the builtin deepObject form cannot express."""
    return JsonParameter("query", "deepObject", True, "object")


def json_header() -> JsonParameter:
    """Carry a header array that the builtin simple form could carry too."""
    return JsonParameter("header", "simple", False, "array")


def json_path() -> JsonParameter:
    """Carry a path object that the builtin simple form could carry too."""
    return JsonParameter("path", "simple", False, "object")
