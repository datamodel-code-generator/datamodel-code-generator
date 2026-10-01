"""Carry parameter wire values as percent-encoded JSON through a generated package's public API."""

from __future__ import annotations

import json
from typing import Literal
from urllib.parse import quote, unquote

from regression.model_codecs import (
    UNSET,
    FragmentContribution,
    ParameterCodecCapabilities,
    ParameterEncodingError,
    ParameterFragment,
    QueryStringContribution,
    freeze_wire,
    thaw_wire,
)


class JsonParameter:
    """Override builtin styles and supply the missing cookie array and content encodings."""

    api_version: Literal[1] = 1

    def capabilities(self, *, plan):
        return ParameterCodecCapabilities(
            locations=("path", "query", "header", "cookie", "querystring"),
            styles=("simple", "form"),
            explode_values=(False,),
            value_kinds=("array", "object"),
            media_types=("application/json", "application/x-www-form-urlencoded"),
            supports_empty_containers=True,
        )

    def encode_parameter(self, *, value, plan, context):
        text = json.dumps(thaw_wire(value), separators=(",", ":"))
        encoded = quote(text, safe="").encode()
        if plan.location == "querystring":
            return QueryStringContribution(raw_query=b"json=" + encoded)
        fragment = ParameterFragment(None if plan.location == "path" else plan.name.encode(), encoded)
        return FragmentContribution(location=plan.location, ordered_fragments=(fragment,))

    def decode_parameter(self, *, raw, plan, context):
        if plan.location == "querystring":
            values = [] if not raw.raw_query else [raw.raw_query.removeprefix(b"json=")]
        else:
            name = None if plan.location == "path" else plan.name.encode().lower()
            values = [part.value for part in raw.fragments if (part.name.lower() if part.name else None) == name]
        if not values:
            return UNSET
        if len(values) > 1:
            raise ParameterEncodingError("Duplicate JSON parameter")
        try:
            return freeze_wire(json.loads(unquote(values[0].decode("ascii"))))
        except ValueError as error:
            raise ParameterEncodingError("Invalid JSON parameter") from error


def json_parameter():
    return JsonParameter()
