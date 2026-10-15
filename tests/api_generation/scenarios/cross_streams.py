"""Read the event streams of the streams server through its client's SSE helpers and plain operations.

The server sends each topic's events as the text its primary response declares or, for the typed topic, as a
Starlette streaming response in chunks that split events; the client's helpers decode them by their event schemas.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.responses import StreamingResponse

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "client/streams.yaml"
CLIENT = {"protocols": "client/protocols/streams.json"}
TOPICS = {
    "messages": 'data: {"text":"a"}\n\nid: 2\ndata: {"text":\ndata: "b"}\n\n',
    "tagged": (
        'data: {"type":"created","id":"1"}\n\n: comment\ndata: {"type":"failed","message":"x"}\n\ndata: [DONE]\n\n'
    ),
    "done": 'data: {"type":"created","id":"1"}\n\ndata: [DONE]\n\n',
}
TYPED = (
    "event: created\ndata: {",
    '"type":"created","id":"1"}\n\nevent: ',
    "other\ndata: 1\n\nevent: done\ndata: x\n\n",
)


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return services that answer each topic with its events, a typed error, or no events."""

    def chunks() -> Iterator[str]:
        yield from TYPED

    def events(topic: str | None, Last_Event_ID: str | None) -> object:  # noqa: N803
        del Last_Event_ID
        if topic == "typed":
            return StreamingResponse(chunks(), media_type="text/event-stream")
        if topic == "none":
            return server.HTTPResult(204, None)
        return TOPICS[topic] if topic in TOPICS else server.HTTPResult(404, models.Problem(detail="no topic"))

    return {
        "events": service(server.services.EventsService, record, stream_events=events),
        "feed": service(
            server.services.FeedService,
            record,
            stream_feed=lambda body: f'data: {{"text":"{body.topic}"}}\n\ndata: [DONE]\n\n',
        ),
        "status": service(server.services.StatusService, record, get_status=lambda: models.Message(text="up")),
    }


def calls(kit: Kit) -> Calls:
    """Return a stream of each helper, the plain operations, and topics without events or without a topic.

    A topic without events is answered with the declared 204, which ends a helper's stream whatever its completion.
    """
    helpers, models = kit.api.protocols, kit.models
    return [
        ("events.messages", lambda: helpers.events.messages.open(topic="messages", Last_Event_ID="1")),
        ("events.typed", lambda: helpers.events.typed.open(topic="typed")),
        ("events.tagged", lambda: helpers.events.tagged.open(topic="done")),
        ("events.tagged error event", lambda: helpers.events.tagged.open(topic="tagged")),
        ("events.messages missing topic", lambda: helpers.events.messages.open(topic="missing")),
        ("events.messages no events", lambda: helpers.events.messages.open(topic="none")),
        ("events.tagged no events", lambda: helpers.events.tagged.open(topic="none")),
        ("feed.all", lambda: helpers.feed.all.open(body=models.FeedQuery(topic="news"))),
        ("stream_events", lambda: kit.api.events.stream_events(topic="messages")),
        ("get_status", kit.api.status.get_status),
    ]
