"""Layer a call's headers and query as one rule, and refuse only the names of the fields a helper writes."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from tests.api_generation.support.client_runtime import Exchange, json_response, raw_response, record

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_PAGE = json_response(200, {"data": ["a"]})
_JOB = {"id": "j1", "state": "running"}
_EVENTS = raw_response(200, b'id: 1\ndata: "a"\n\n', "text/event-stream")


def _events(protocols: Any, options: object) -> list[object]:
    with protocols.items.live.open(options=options) as events:
        return list(events)


def _helper_calls(
    protocols: Any,
) -> tuple[tuple[str, tuple[str, ...], Callable[[object], object], tuple[Callable[..., Any], ...]], ...]:
    """Return each helper with the query names a call gives it, its call, and the responders of what it sends.

    The first name is that of a field the helper writes; the others name none.
    """
    return (
        ("pager", ("status", "filter"), lambda options: list(protocols.items.all.iterate(options=options)), (_PAGE,)),
        (
            "querystring member pager",
            ("page", "term"),
            lambda options: list(protocols.search.pages.iterate(options=options)),
            (_PAGE,),
        ),
        (
            "whole querystring pager",
            ("term", "other"),
            lambda options: list(protocols.search.fixed.iterate(options=options)),
            (_PAGE,),
        ),
        (
            "poller",
            ("status", "jobId"),
            lambda options: protocols.jobs.run.start(options=options).wait(),
            (json_response(202, _JOB), json_response(200, {**_JOB, "state": "done"})),
        ),
        (
            "upload",
            ("status", "size"),
            lambda options: protocols.files.upload.start(b"abc", options=options).run(),
            (json_response(201, _JOB), raw_response(204)),
        ),
        ("stream", ("status", "filter"), lambda options: _events(protocols, options), (_EVENTS,)),
    )


def call_layers(package: ModuleType, lines: list[str]) -> None:
    """Send a header parameter over the client's own header alike with or without default headers.

    A helper refuses an extra query that names a field it writes, a property of an exploded form object or a member of
    a form querystring included, and sends any other, such as a property its closed object lacks or a path parameter's.
    """
    options = importlib.import_module(f"{package.__name__}.options")
    exchange = Exchange(lines)
    with exchange.client() as native:
        for label, defaults in (("without default headers", None), ("beside a default header", {"X-Client": "c"})):
            with package.Client(http_client=native, default_headers=defaults) as api:
                exchange.respond(_PAGE)
                record(lines, f"header parameter {label}", lambda api=api: api.default.list_items(Accept_Encoding="br"))
        with package.Client(http_client=native) as api:
            for helper, names, call, responders in _helper_calls(api.protocols):
                for name in names:
                    request = options.RequestOptions(extra_query={name: "x"})
                    exchange.respond(*responders)
                    record(lines, f"{helper} extra query {name}", lambda call=call, request=request: call(request))
                    exchange.responders.clear()
            view = api.with_options(default_query={"status": "x"})
            record(lines, "pager default query status", lambda: list(view.protocols.items.all.iterate()))
