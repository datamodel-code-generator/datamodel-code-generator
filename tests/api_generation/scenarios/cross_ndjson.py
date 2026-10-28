"""Read the NDJSON records of the records server through its client's NDJSON helpers and plain operations.

The server answers with the bytes its records' media types declare, one JSON value a line, and the client's helpers
decode each line by their record schemas until the end of the body or a sentinel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from types import ModuleType

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "client/ndjson.yaml"
CLIENT = {"protocols": "client/protocols/ndjson.json"}
TOPICS = {
    "records": b'{"text":"a"}\n{"text":"b"}\n',
    "unterminated": b'{"text":"a"}\n{"text":"b"}',
    "tagged": b'{"type":"created","id":"1"}\n{"type":"moved","id":"1"}\n[DONE]\n',
    "failed": b'{"type":"created","id":"1"}\n{"type":"failed","message":"x"}\n',
}


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return services that answer each topic with its lines or a typed error."""

    def records(topic: str | None) -> object:
        return TOPICS[topic] if topic in TOPICS else server.HTTPResult(404, models.Problem(detail="no topic"))

    return {
        "records": service(server.services.RecordsService, record, stream_records=records),
        "search": service(
            server.services.SearchService,
            record,
            search=lambda body: f'{{"text":"{body.text}"}}\n[DONE]\n'.encode(),
        ),
        "status": service(server.services.StatusService, record, get_status=lambda: models.Record(text="up")),
    }


def calls(kit: Kit) -> Calls:
    """Return a stream of each helper, an unterminated last line, an error record, and the plain operations."""
    helpers, models = kit.api.protocols, kit.models
    return [
        ("records.all", lambda: helpers.records.all.open(topic="records")),
        ("records.all unterminated", lambda: helpers.records.all.open(topic="unterminated")),
        ("records.lenient unterminated", lambda: helpers.records.lenient.open(topic="unterminated")),
        ("records.tagged", lambda: helpers.records.tagged.open(topic="tagged")),
        ("records.tagged error record", lambda: helpers.records.tagged.open(topic="failed")),
        ("records.all missing topic", lambda: helpers.records.all.open(topic="missing")),
        ("search.all", lambda: helpers.search.all.open(body=models.SearchQuery(text="hi"))),
        ("stream_records", lambda: kit.api.records.stream_records(topic="records")),
        ("search", lambda: kit.api.search.search(body=models.SearchQuery(text="hi"))),
        ("get_status", kit.api.status.get_status),
    ]
