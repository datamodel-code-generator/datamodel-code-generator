"""Python helper configurations of the client protocol fixtures, relative to the working directory."""

from __future__ import annotations

from dataclasses import replace
from functools import reduce
from typing import TYPE_CHECKING

from datamodel_code_generator._client.protocols import (
    AdapterSignature,
    BodyHmacSignature,
    StandardWebhooksSignature,
    StripeStyleSignature,
    Binding,
    CacheHelper,
    CacheMutation,
    CountContinuation,
    CursorContinuation,
    EndCondition,
    EventDiscriminator,
    EventMapping,
    ImmediateResult,
    InlineResult,
    LinkContinuation,
    LiteralValue,
    NextUrlContinuation,
    NoResult,
    NoSignature,
    OperationResult,
    PaginationHelper,
    PollingHelper,
    PollInterval,
    ProtocolConfiguration,
    PublicKeySignature,
    QueuedOperation,
    QueueHelper,
    RemoteCancel,
    SourceValue,
    StreamCompletion,
    StreamHelper,
    StreamResume,
    WebhookHelper,
)
from datamodel_code_generator._codec_declarations import OperationRef, SchemaRef
from datamodel_code_generator._runtime.protocols.records import (
    BodySelector,
    BodyTarget,
    HeaderSelector,
    ParameterTarget,
    QuerystringTarget,
    StatusSelector,
)

if TYPE_CHECKING:
    from datamodel_code_generator._client.protocols import Source

USERS = "/paths/~1users/get"
USER = SchemaRef(pointer="/components/schemas/User")
DATA = BodySelector(pointer="/data")
JOB_ID = ParameterTarget(location="path", name="jobId")
RECORD = SchemaRef(pointer="/components/schemas/Record", document="./helpers-external.yaml")


DEEP = reduce(lambda inner, _: [inner], range(70), [])


def _job_id(source: Source) -> Binding:
    return Binding(target=JOB_ID, value=SourceValue(source=source, selector=BodySelector(pointer="/id")))


DISABLED = ProtocolConfiguration(
    helpers={
        "users.all": PaginationHelper(
            enabled=False,
            operation=USERS,
            items=DATA,
            item_schema=SchemaRef(pointer="/components/schemas/User", document="./helpers.yaml"),
            continuation=CursorContinuation(
                read=BodySelector(pointer="/next_cursor"),
                write=ParameterTarget(location="query", name="cursor"),
                end=(EndCondition(kind="missing"), EndCondition(kind="null")),
                empty_string="value",
            ),
            bindings=(
                Binding(
                    target=ParameterTarget(location="header", name="snapshot"),
                    value=SourceValue(source="initial", selector=HeaderSelector(name="Snapshot", occurrence="all")),
                ),
            ),
        ),
        "users.offsets": PaginationHelper(
            enabled=False,
            operation=OperationRef(pointer=USERS),
            items=DATA,
            item_schema=USER,
            continuation=CountContinuation(
                kind="offset",
                write=ParameterTarget(location="query", name="offset"),
                first=0,
                step="page_items_count",
                has_more=BodySelector(pointer="/has_more"),
            ),
        ),
        "users.pages": PaginationHelper(
            enabled=False,
            operation=OperationRef(pointer=USERS, document="./helpers.yaml"),
            items=DATA,
            item_schema=USER,
            continuation=CountContinuation(
                kind="page",
                write=ParameterTarget(location="query", name="page"),
                first=1,
                step=1,
                total=BodySelector(pointer="/total"),
            ),
        ),
        "users.follow": PaginationHelper(
            enabled=False,
            operation=USERS,
            items=DATA,
            item_schema=USER,
            continuation=NextUrlContinuation(
                read=BodySelector(pointer="/next_url"),
                end=[EndCondition(kind="missing"), EndCondition(kind="value", value="")],
                repeat_request_body=True,
            ),
        ),
        "users.linked": PaginationHelper(
            enabled=False,
            operation=USERS,
            items=DATA,
            item_schema=USER,
            continuation=LinkContinuation(header="Link"),
            bindings=(Binding(target=ParameterTarget(location="query", name="page"), value=LiteralValue(literal=1)),),
        ),
        "users.search": PaginationHelper(
            enabled=False,
            operation="/paths/~1users~1search/post",
            items=DATA,
            item_schema=USER,
            continuation=CursorContinuation(
                read=StatusSelector(),
                write=BodyTarget(pointer="/cursor"),
                end=(EndCondition(kind="value", value=204),),
                empty_string="end",
            ),
        ),
        "audit.all": PaginationHelper(
            enabled=False,
            operation="/paths/~1admin~1audit/get",
            items=DATA,
            item_schema=SchemaRef(pointer="/components/schemas/NamePage/properties/data/items"),
            continuation=LinkContinuation(header="Link", rel="more"),
        ),
        "jobs.run": PollingHelper(
            enabled=False,
            create="/paths/~1jobs/post",
            accepted_statuses=(202,),
            poll="/paths/~1jobs~1{jobId}/get",
            bindings=(_job_id("initial"),),
            state=BodySelector(pointer="/status"),
            pending=("queued", "running"),
            succeeded=("done",),
            failed=("failed",),
            cancelled=("cancelled",),
            result=InlineResult(selector=BodySelector(pointer=""), schema=SchemaRef(pointer="/components/schemas/Job")),
            interval=PollInterval(seconds=2, retry_after_header="Retry-After"),
            remote_cancel=RemoteCancel(operation="/paths/~1jobs~1{jobId}/delete", bindings=(_job_id("initial"),)),
            immediate_result=ImmediateResult(
                statuses=(200, 201),
                selector=BodySelector(pointer=""),
                schema=SchemaRef(pointer="/components/schemas/Job"),
            ),
            expires_at=BodySelector(pointer="/expires"),
        ),
        "jobs.fetch": PollingHelper(
            enabled=False,
            create="/paths/~1jobs/post",
            accepted_statuses=(202,),
            poll="/paths/~1jobs~1{jobId}/get",
            bindings=(_job_id("previous"),),
            state=StatusSelector(),
            pending=(202, 1.5, {"phase": [True, None]}),
            succeeded=(200,),
            result=OperationResult(operation="/paths/~1jobs~1{jobId}~1result/get", bindings=(_job_id("input"),)),
        ),
        "jobs.fire": PollingHelper(
            enabled=False,
            create="/paths/~1jobs/post",
            accepted_statuses=(202, 204),
            poll="/paths/~1jobs~1{jobId}/get",
            bindings=(),
            state=HeaderSelector(name="X-State"),
            pending=("queued",),
            succeeded=("done",),
            result=NoResult(),
        ),
        "events.watch": StreamHelper(
            kind="sse",
            enabled=False,
            operation="/paths/~1events/get",
            media="Text/Event-Stream",
            event_schema=EventMapping(
                discriminator=EventDiscriminator(from_="event_type"),
                mapping={
                    "created": SchemaRef(pointer="/components/schemas/UserEvent"),
                    "deleted": SchemaRef(pointer="/components/schemas/UserEvent"),
                },
            ),
            completion=StreamCompletion(kind="event_type", value="done"),
            unknown="raw",
            error_events={"error": SchemaRef(pointer="/components/schemas/StreamError")},
            resume=StreamResume(
                cursor="event_id",
                write=ParameterTarget(location="header", name="Last-Event-ID"),
                reopen_operation="/paths/~1events/get",
                delivery="at_least_once",
                reconnect_on=("transport_interruption", "incomplete_eof"),
            ),
        ),
        "records.watch": StreamHelper(
            kind="ndjson",
            enabled=False,
            operation="/paths/~1records/get",
            media="application/x-ndjson",
            event_schema=RECORD,
            completion=StreamCompletion(kind="sentinel", value="[DONE]"),
            final_line="allow_eof",
            resume=StreamResume(
                cursor=BodySelector(pointer="/id"),
                write=ParameterTarget(location="query", name="after"),
                reopen_operation="/paths/~1records/get",
                delivery="at_least_once",
                missing="inherit",
                null="clear",
                expires_at=HeaderSelector(name="X-Expires"),
            ),
        ),
        "records.typed": StreamHelper(
            kind="ndjson",
            enabled=False,
            operation="/paths/~1records/get",
            media="application/x-ndjson",
            event_schema=EventMapping(
                discriminator=EventDiscriminator(from_="body", pointer="/type"),
                mapping={"record": RECORD},
            ),
            completion=StreamCompletion(kind="eof"),
            final_line="require_newline",
        ),
    }
)

INVALID = ProtocolConfiguration(
    schema_version=2,  # ty: ignore[invalid-argument-type]
    helpers={
        1: PaginationHelper(  # ty: ignore[invalid-argument-type]
            operation=USERS, items=DATA, item_schema=USER, continuation=LinkContinuation(header="Link")
        ),
        "users.all": PaginationHelper(
            operation=5,  # ty: ignore[invalid-argument-type]
            items={"from": "body", "pointer": "/data"},  # ty: ignore[invalid-argument-type]
            item_schema="/components/schemas/User",  # ty: ignore[invalid-argument-type]
            continuation=CursorContinuation(
                read=object(),  # ty: ignore[invalid-argument-type]
                write=ParameterTarget(location="query", name="cursor"),
                end=(EndCondition(kind="value"),),
                empty_string="value",
            ),
            bindings=(Binding(target=JOB_ID, value=LiteralValue(literal={"at": object()})),),
            enabled=None,  # ty: ignore[invalid-argument-type]
        ),
        "users.pages": PaginationHelper(
            operation=USERS,
            items=DATA,
            item_schema=USER,
            continuation=CountContinuation(
                kind="page",
                write=ParameterTarget(location="query", name="page"),
                first=1,
                step="every",  # ty: ignore[invalid-argument-type]
                total=BodySelector(pointer="/total"),
            ),
            bindings=(
                Binding(target=QuerystringTarget(name="criteria", pointer="/page"), value=LiteralValue(literal=1)),
            ),
        ),
        "events.watch": StreamHelper(
            kind="sse",
            operation="/paths/~1events/get",
            media="text/event-stream",
            event_schema=EventMapping(
                discriminator=EventDiscriminator(from_="event_type"),
                mapping={"created": "/components/schemas/UserEvent"},  # ty: ignore[invalid-argument-type]
            ),
            completion=StreamCompletion(kind="eof"),
            error_events=[USER],  # ty: ignore[invalid-argument-type]
        ),
        "users.nested": PaginationHelper(
            operation=USERS,
            items=DATA,
            item_schema=SchemaRef(pointer="/components/\ud800"),
            continuation=NextUrlContinuation(
                read=BodySelector(pointer="/next_url"),
                end=[[EndCondition(kind="missing")]],  # ty: ignore[invalid-argument-type]
            ),
            bindings=(
                Binding(target=JOB_ID, value=LiteralValue(literal=DEEP)),
                Binding(target=JOB_ID, value=LiteralValue(literal=["\ud800"])),
            ),
        ),
        "jobs.run": object(),  # ty: ignore[invalid-argument-type]
        "hooks.hmac": WebhookHelper(event_schema=USER, signature=BodyHmacSignature(header="X-Signature", algorithm="ed25519")),
        "hooks.public": WebhookHelper(event_schema=USER, signature=PublicKeySignature(kind="hmac-sha256", header="X-Signature")),
    },
)

SHAPE = ProtocolConfiguration(helpers=[DISABLED])  # ty: ignore[invalid-argument-type]

STANDARD = StandardWebhooksSignature()
BODY_ONLY = BodyHmacSignature(header="X-Signature")
MESSAGE = SchemaRef(pointer="/components/schemas/Message")
PUSH = SchemaRef(pointer="/components/schemas/Push")
WEBHOOKS = ProtocolConfiguration(helpers={
    "standard.message": WebhookHelper(event_schema=MESSAGE, signature=STANDARD),
    "stripe.event": WebhookHelper(event_schema=MESSAGE, signature=StripeStyleSignature()),
    "github.push": WebhookHelper(event_schema=PUSH, signature=replace(BODY_ONLY, header="X-Hub-Signature-256", prefix="sha256=")),
    "rfc.sha256": WebhookHelper(event_schema=PUSH, signature=BODY_ONLY),
    "rfc.sha512": WebhookHelper(event_schema=PUSH, signature=replace(BODY_ONLY, algorithm="hmac-sha512")),
    "shapes.drawn": WebhookHelper(event_schema=SchemaRef(pointer="/components/schemas/Shape"), signature=BODY_ONLY),
    "callbacks.delivered": WebhookHelper(event_schema=SchemaRef(pointer="/components/schemas/Delivery"), signature=BODY_ONLY),
    "disabled.hook": WebhookHelper(enabled=False, event_schema=SchemaRef(pointer="/components/schemas/Unused"), signature=BODY_ONLY),
})
ED25519_BODY = PublicKeySignature(kind="ed25519", header="X-Signature")
PUBLIC_KEYS = ProtocolConfiguration(helpers={
    "rfc8032.ed25519": WebhookHelper(event_schema=PUSH, signature=ED25519_BODY),
    "wycheproof.rsa": WebhookHelper(event_schema=SchemaRef(pointer="/components/schemas/Count"), signature=replace(ED25519_BODY, kind="rsa-pss-sha256")),
    "github.push": WEBHOOKS.helpers["github.push"],
})

INVOICE = SchemaRef(pointer="/components/schemas/Invoice")
CUSTOMER = SchemaRef(pointer="/components/schemas/Customer")
BY_TYPE = EventDiscriminator(from_="body", pointer="/type")
ADAPTERS = ProtocolConfiguration(
    helpers={
        "stripe.event": WebhookHelper(
            event_schema=EventMapping(
                discriminator=BY_TYPE,
                mapping={"invoice.paid": INVOICE, "invoice.updated": INVOICE, "customer.created": CUSTOMER},
            ),
            signature=AdapterSignature(timestamp="required", delivery_id="none"),
        ),
        "adapted.message": WebhookHelper(
            event_schema=MESSAGE, signature=AdapterSignature(timestamp="required", delivery_id="required")
        ),
        "adapted.plain": WebhookHelper(
            event_schema=MESSAGE,
            signature=AdapterSignature(timestamp="none", delivery_id="none"),
        ),
        "mapped.event": WebhookHelper(
            event_schema=EventMapping(
                discriminator=BY_TYPE, mapping={"invoice.paid": INVOICE, "customer.created": CUSTOMER}
            ),
            signature=BODY_ONLY,
        ),
        "unsigned.event": WebhookHelper(
            event_schema=EventMapping(
                discriminator=EventDiscriminator(from_="body", pointer="/meta/type"),
                mapping={"invoice.paid": INVOICE, "customer.created": CUSTOMER},
            ),
            signature=NoSignature(),
        ),
        "unsigned.message": WebhookHelper(
            event_schema=SchemaRef(pointer="/webhooks/message/post/requestBody/content/application~1json/schema"),
            signature=NoSignature(),
        ),
    }
)

USER_BY_ID = "/paths/~1users~1{userId}/get"
CACHING = ProtocolConfiguration(
    helpers={
        "users.profile": CacheHelper(
            operation=USER_BY_ID,
            validator="both",
            authenticated=False,
            vary_allowlist=("Accept-Language",),
            tags=("user:{userId}", "users"),
            mutations={
                "rename": CacheMutation(
                    operation=OperationRef(pointer="/paths/~1users~1{userId}/patch"),
                    invalidate_tags=("user:{userId}",),
                ),
                "remove": CacheMutation(
                    operation="/paths/~1users~1{userId}/delete", invalidate_tags=("user:{userId}", "users")
                ),
            },
        ),
        "users.dated": CacheHelper(
            operation=USER_BY_ID, validator="last_modified", authenticated=False, statuses=(200, 203)
        ),
        "users.listing": CacheHelper(
            operation=USERS,
            validator="etag",
            authenticated=False,
            vary_allowlist=("Accept-Language",),
            tags=("users",),
            mutations={"create": CacheMutation(operation="/paths/~1users/post", invalidate_tags=("users",))},
        ),
        "carts.current": CacheHelper(operation="/paths/~1carts~1current/get", validator="etag", authenticated=True),
        "secure.profile": CacheHelper(
            operation="/paths/~1secure~1users~1{userId}/get",
            validator="etag",
            authenticated=True,
            vary_allowlist=("X-Signature",),
        ),
    }
)

KEY = ParameterTarget(location="header", name="Idempotency-Key")
QUEUES = ProtocolConfiguration(
    helpers={
        "orders.outbox": QueueHelper(
            operations={
                "create_order": QueuedOperation(
                    operation=OperationRef(pointer="/paths/~1orders/post"),
                    side_effects=True,
                    key_binding=KEY,
                    dedupe_ttl=3600,
                ),
                "refresh": QueuedOperation(
                    operation=OperationRef(pointer="/paths/~1orders~1{orderId}/get"), side_effects=False
                ),
            }
        ),
        "account.offline": QueueHelper(
            operations={
                "fetch": QueuedOperation(operation=OperationRef(pointer="/paths/~1account/get"), side_effects=False),
                "rename": QueuedOperation(
                    operation=OperationRef(pointer="/paths/~1account/patch"),
                    side_effects=True,
                    key_binding=KEY,
                    dedupe_ttl=600.0,
                ),
            }
        ),
    }
)

RECORDS = {
    "disabled": DISABLED,
    "invalid": INVALID,
    "shape": SHAPE,
    "webhooks": WEBHOOKS,
    "public_keys": PUBLIC_KEYS,
    "adapters": ADAPTERS,
    "caching": CACHING,
    "queues": QUEUES,
}
