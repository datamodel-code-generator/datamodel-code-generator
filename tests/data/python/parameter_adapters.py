"""Parameter adapters that carry values as percent-encoded compact JSON or as given, for any generated package.

Each adapter reaches the public `model_codecs` module of the generated package whose bindings build it, so one
fixture serves every package a test generates.
"""

from __future__ import annotations

import importlib
import inspect
import json
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote, unquote

if TYPE_CHECKING:
    from types import ModuleType


def _caller() -> ModuleType:
    """Return the public `model_codecs` module of the generated package whose model bindings build an adapter."""
    frame = inspect.currentframe()
    while frame is not None:
        if (name := frame.f_globals.get("__name__", "")).endswith("._generated.model_bindings"):
            return importlib.import_module(f"{name.removesuffix('._generated.model_bindings')}.model_codecs")
        frame = frame.f_back
    msg = "A fixture adapter is built only by a generated package's model bindings"
    raise LookupError(msg)


def _key(name: bytes, location: str) -> bytes:
    """Return a raw parameter name as its location compares it: header names ignore case."""
    return name.lower() if location == "header" else name


class _Adapter:
    """Declare one parameter adapter's capabilities and find the raw value it decodes."""

    api_version: Literal[1] = 1

    def __init__(self, **declared: object) -> None:
        """Keep the capabilities the adapter declares, as the registration's record takes them, and its package."""
        self._declared = declared
        self._public = _caller()

    def capabilities(self, *, plan: object) -> object:  # noqa: ARG002
        """Report the declared capabilities as the calling package's record."""
        return self._public.ParameterCodecCapabilities(**self._declared)

    def _contribution(self, plan: object, value: bytes) -> object:
        """Return the value as the whole query, one unnamed path fragment, or one fragment named after the parameter."""
        public = self._public
        if plan.location == "querystring":
            return public.QueryStringContribution(raw_query=value)
        name = None if plan.location == "path" else plan.name.encode()
        return public.FragmentContribution(
            location=plan.location, ordered_fragments=(public.ParameterFragment(name=name, value=value),)
        )

    def _raw(self, raw: object, plan: object) -> bytes | None:
        """Return the raw value of the parameter, or None without one, refusing a repeated one."""
        if plan.location == "querystring":
            return raw.raw_query or None
        name = _key(plan.name.encode(), plan.location)
        values = [
            fragment.value
            for fragment in raw.fragments
            if fragment.name is None or _key(fragment.name, plan.location) == name
        ]
        if len(values) > 1:
            msg = f"The {plan.location} parameter {plan.name} appears more than once"
            raise self._public.ParameterEncodingError(msg)
        return values[0] if values else None


class JsonParameter(_Adapter):
    """Carry one parameter as compact JSON, percent-encoded once."""

    def encode_parameter(self, *, value: object, plan: object, context: object) -> object:  # noqa: ARG002
        """Return the value's JSON as the parameter's one contribution."""
        text = json.dumps(self._public.thaw_wire(value), separators=(",", ":"), ensure_ascii=False)
        return self._contribution(plan, quote(text, safe="").encode())

    def decode_parameter(self, *, raw: object, plan: object, context: object) -> object:  # noqa: ARG002
        """Return the value the parameter's JSON holds, or UNSET without one."""
        public = self._public
        if (value := self._raw(raw, plan)) is None:
            return public.UNSET
        try:
            return public.freeze_wire(json.loads(unquote(value.decode("ascii"), errors="strict")))
        except ValueError as error:
            msg = f"The {plan.location} parameter {plan.name} is not percent-encoded JSON"
            raise public.ParameterEncodingError(msg) from error


class Members(JsonParameter):
    """Send each member of a query object under its own name, as the builtin exploded form does, for refusal."""

    def encode_parameter(self, *, value: object, plan: object, context: object) -> object:  # noqa: ARG002
        """Return one fragment per member, named after the member instead of the parameter."""
        public = self._public
        fragments = tuple(
            public.ParameterFragment(name=quote(name, safe="").encode(), value=quote(str(item), safe="").encode())
            for name, item in public.thaw_wire(value).items()
        )
        return public.FragmentContribution(location=plan.location, ordered_fragments=fragments)


class Brackets(JsonParameter):
    """Send each member of a deepObject query object as `name[member]`, as the builtin deepObject form does."""

    def encode_parameter(self, *, value: object, plan: object, context: object) -> object:  # noqa: ARG002
        """Return one fragment per member, named after the parameter and the member."""
        public = self._public
        fragments = tuple(
            public.ParameterFragment(
                name=quote(f"{plan.name}[{name}]", safe="").encode(), value=quote(str(item), safe="").encode()
            )
            for name, item in public.thaw_wire(value).items()
        )
        return public.FragmentContribution(location=plan.location, ordered_fragments=fragments)


class RawText(_Adapter):
    """Carry one string parameter's UTF-8 bytes as given, which the runtime checks at the HTTP boundary."""

    def encode_parameter(self, *, value: object, plan: object, context: object) -> object:  # noqa: ARG002
        """Return the string's bytes as the parameter's one contribution."""
        return self._contribution(plan, value.encode())

    def decode_parameter(self, *, raw: object, plan: object, context: object) -> object:  # noqa: ARG002
        """Return the parameter's bytes as text, without decoding any percent-encoding, or UNSET without them."""
        public = self._public
        if (value := self._raw(raw, plan)) is None:
            return public.UNSET
        try:
            return value.decode()
        except ValueError as error:
            msg = f"The {plan.location} parameter {plan.name} is not UTF-8"
            raise public.ParameterEncodingError(msg) from error


def json_cookie() -> JsonParameter:
    """Carry a form cookie array without explosion, whose builtin form OpenAPI leaves undefined."""
    return JsonParameter(
        locations=("cookie",),
        styles=("form",),
        explode_values=(False,),
        value_kinds=("array",),
        supports_empty_containers=True,
    )


def json_query() -> JsonParameter:
    """Carry a nested deepObject query object, which the builtin deepObject form cannot express."""
    return JsonParameter(locations=("query",), styles=("deepObject",), explode_values=(True,), value_kinds=("object",))


def json_options() -> JsonParameter:
    """Carry a form query object whose builtin expansion would take the name of another query parameter."""
    return JsonParameter(locations=("query",), styles=("form",), explode_values=(True,), value_kinds=("object",))


def brackets() -> Brackets:
    """Send a deepObject query object's members under the parameter's bracketed names, which the runtime accepts."""
    return Brackets(locations=("query",), styles=("deepObject",), explode_values=(True,), value_kinds=("object",))


def members() -> Members:
    """Send a form query object's members under their own names, which the runtime refuses."""
    return Members(locations=("query",), styles=("form",), explode_values=(True,), value_kinds=("object",))


def json_header() -> JsonParameter:
    """Carry a header array, exploded or not, that the builtin simple form could carry too."""
    return JsonParameter(
        locations=("header",), styles=("simple",), explode_values=(False, True), value_kinds=("array",)
    )


def json_path() -> JsonParameter:
    """Carry a path object that the builtin simple form could carry too."""
    return JsonParameter(locations=("path",), styles=("simple",), explode_values=(False,), value_kinds=("object",))


json_path_v2 = json_path


def json_querystring() -> JsonParameter:
    """Carry a JSON querystring, percent-encoded once as the whole query."""
    return JsonParameter(
        locations=("querystring",),
        styles=(),
        explode_values=(),
        value_kinds=("object",),
        media_types=("application/json",),
    )


def raw_path() -> RawText:
    """Carry a path string as given, dot segments included, for the runtime to refuse."""
    return RawText(locations=("path",), styles=("simple",), explode_values=(False,), value_kinds=("string",))


def raw_header() -> RawText:
    """Carry a header string as given, padding and non-ASCII text included, for the runtime to refuse."""
    return RawText(locations=("header",), styles=("simple",), explode_values=(False,), value_kinds=("string",))
