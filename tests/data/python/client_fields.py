"""Call generated clients with a body's fields instead of the body, and report each request and binding refusal."""

from __future__ import annotations

import datetime
import importlib
from typing import TYPE_CHECKING, Any

from tests.data.python.client_hooks import Recorder
from tests.data.python.client_runtime import Exchange, arecord, json_response, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import ModuleType

_JSON = "application/json"
_FORM = "application/x-www-form-urlencoded"
_TEXT = "text/plain"
_CREATED = {"id": 1, "name": "Mimi"}


def _exchanged(exchange: Exchange, lines: list[str], label: str, call: Callable[[], object], answer: Any) -> None:
    """Record a call with an answer queued for it; a call refused before sending leaves the answer unused."""
    exchange.respond(answer)
    record(lines, label, call)
    exchange.responders.clear()


async def _aexchanged(
    exchange: Exchange, lines: list[str], label: str, call: Callable[[], Awaitable[object]], answer: Any
) -> None:
    exchange.respond(answer)
    await arecord(lines, label, call)
    exchange.responders.clear()


class _Fields:
    """The modules of one generated package, and the native values its fields take."""

    def __init__(self, package: ModuleType) -> None:
        self.package = package
        self.models = importlib.import_module(f"{package.__name__}_models")
        self.options = importlib.import_module(f"{package.__name__}.options")

    def pet_id(self, operation: str) -> Any:
        """Return the pet id of an operation's path, which a Pydantic model wraps and other backends alias."""
        declared = getattr(self.models, f"FieldPetsPetId{operation}PathPetIdParameter")
        return getattr(declared, "__value__", declared)(1)

    def tag(self, value: str) -> Any:
        """Return a query tag of the create operation, which a Pydantic model wraps and other backends alias."""
        declared = self.models.FieldPetsPostQueryTagParameter
        return getattr(declared, "__value__", declared)(value)

    def kind(self) -> Any:
        """Return the kind cat: its enum member, or the literal of a backend that spells the enum as literals."""
        return getattr(self.models.Kind, "cat", "cat")

    def no_owner(self) -> Any:
        """Return a null owner body: the root model of None that a Pydantic model wraps it in, or None."""
        declared = self.models.FieldOwnersPostRequest
        return declared(None) if hasattr(declared, "model_fields") else None

    def birth(self) -> object:
        """Return a birth date as the backend's field takes it: a date for Pydantic, its string otherwise."""
        return datetime.date(2020, 1, 2) if hasattr(self.models.NewPet, "__pydantic_fields__") else "2020-01-02"


def fields(package: ModuleType, lines: list[str]) -> None:
    """Send bodies given as fields through every view, and refuse each binding the methods cannot take."""
    context = _Fields(package)
    exchange = Exchange(lines)
    http = exchange.client()
    with package.Client(http_client=http) as api:
        _sent(context, api.default, exchange, lines)
        _refused(context, api.with_options(context.options.RequestOptions(hooks=(Recorder(lines, "hook"),))), exchange, lines)
        _optional(context, api.default, exchange, lines)
        _views(context, api.default, exchange, lines)
        _constructed(context, api.default, exchange, lines)
    http.close()
    run(lambda: _async_fields(context, lines))


def _sent(context: _Fields, pets: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send the fields a call gives, and only those, as the body of their media; a body is sent as before."""
    kind, owner = context.kind(), context.models.Owner(email="e")
    unset = context.options.UNSET
    for label, call in (
        ("create from its required fields", lambda: pets.create_pet(name="Mimi", kind=kind, media_type=_JSON)),
        (
            "create from every field, with a null tag",
            lambda: pets.create_pet(
                name="Mimi",
                kind=kind,
                pet_tag=None,
                birth_date=context.birth(),
                owner=owner,
                secret="s",
                media_type=_JSON,
            ),
        ),
        ("create from form fields", lambda: pets.create_pet(name="Mimi", pet_tag="t", media_type=_FORM)),
        ("create from a body", lambda: pets.create_pet(body=context.models.NewPet(name="Mimi", kind=kind), media_type=_JSON)),
        (
            "create from fields and an UNSET body",
            lambda: pets.create_pet(body=unset, name="Mimi", kind=kind, media_type=_JSON),
        ),
        (
            "create from a body and UNSET fields",
            lambda: pets.create_pet(
                body=context.models.NewPet(name="Mimi", kind=kind), name=unset, pet_tag=unset, media_type=_JSON
            ),
        ),
        (
            "create with a query tag and a body tag",
            lambda: pets.create_pet(tag=context.tag("q"), name="Mimi", pet_tag="b", media_type=_FORM),
        ),
    ):
        _exchanged(exchange, lines, label, call, json_response(201, _CREATED))
    for label, call in (
        ("log a visit giving nothing of a required body", lambda: pets.log_visit(pet_id=context.pet_id("VisitsPost"), media_type=_JSON)),
        ("log a visit with renamed options", lambda: pets.log_visit(pet_id=context.pet_id("VisitsPost"), visit_options=["a"], media_type=_JSON)),
        ("log a visit as text", lambda: pets.log_visit(pet_id=context.pet_id("VisitsPost"), body="n", media_type=_TEXT)),
        ("set an owner, which takes only a body", lambda: pets.set_owner(pet_id=context.pet_id("OwnerPut"), body=owner)),
        ("create an owner from fields of a body that may be null", lambda: pets.create_owner(email="e")),
        ("create a null owner", lambda: pets.create_owner(body=context.no_owner())),
    ):
        _exchanged(exchange, lines, label, call, raw_response(204))


def _refused(context: _Fields, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Refuse bindings before any hook or request: TypeError as Python binds, or the media's own errors."""
    pets = api.default
    kind = context.kind()
    body = context.models.NewPet(name="Mimi", kind=kind)
    for label, call in (
        ("a body and fields", lambda: pets.create_pet(body=body, name="Mimi", media_type=_JSON)),
        ("fields missing a required one", lambda: pets.create_pet(name="Mimi", media_type=_JSON)),
        ("fields missing every required one", lambda: pets.create_pet(pet_tag="t", media_type=_JSON)),
        ("a field of another media", lambda: pets.create_pet(name="Mimi", kind=kind, media_type=_FORM)),
        ("fields without a media type", lambda: pets.create_pet(name="Mimi", kind=kind)),
        ("nothing without a media type", lambda: pets.create_pet()),
        ("fields for text", lambda: pets.log_visit(pet_id=context.pet_id("VisitsPost"), note="n", media_type=_TEXT)),
        ("fields of an undeclared media", lambda: pets.log_visit(pet_id=context.pet_id("VisitsPost"), note="n", media_type="application/xml")),
        ("a body field of a body-only operation", lambda: pets.set_owner(pet_id=context.pet_id("OwnerPut"), email="e")),
        ("an owner giving nothing", lambda: pets.create_owner()),
        ("a null owner and fields", lambda: pets.create_owner(body=context.no_owner(), email="e")),
        (
            "a streamed body and fields",
            lambda: pets.with_streaming_response.create_pet(body=body, name="Mimi", media_type=_JSON),
        ),
    ):
        record(lines, label, call)
    _exchanged(
        exchange,
        lines,
        "fields the hooks observe",
        lambda: pets.create_pet(name="Mimi", kind=kind, media_type=_JSON),
        json_response(201, _CREATED),
    )


def _optional(context: _Fields, pets: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send nothing for an optional body the call omits, and never an empty object for a media type alone."""
    for label, call in (
        ("update giving nothing", lambda: pets.update_pet(pet_id=context.pet_id("Patch"))),
        ("update a name", lambda: pets.update_pet(pet_id=context.pet_id("Patch"), name="n")),
        ("update a null tag", lambda: pets.update_pet(pet_id=context.pet_id("Patch"), tag=None)),
        ("update naming only a media type", lambda: pets.update_pet(pet_id=context.pet_id("Patch"), media_type=_JSON)),
        ("log a visit naming only text", lambda: pets.log_visit(pet_id=context.pet_id("VisitsPost"), media_type=_TEXT)),
    ):
        _exchanged(exchange, lines, label, call, raw_response(204))


def _views(context: _Fields, pets: Any, exchange: Exchange, lines: list[str]) -> None:
    """Give fields through the metadata, raw, and streaming views, which bind them the same way."""
    kind = context.kind()
    _exchanged(
        exchange,
        lines,
        "create with its response",
        lambda: pets.with_response.create_pet(name="Mimi", kind=kind, media_type=_JSON),
        json_response(201, _CREATED),
    )
    _exchanged(
        exchange,
        lines,
        "create raw",
        lambda: pets.with_raw_response.create_pet(name="Mimi", kind=kind, media_type=_JSON).json(),
        json_response(201, _CREATED),
    )
    exchange.respond(json_response(201, _CREATED))
    with pets.with_streaming_response.create_pet(name="Mimi", kind=kind, media_type=_JSON) as streamed:
        record(lines, "create streamed", streamed.read)
    exchange.responders.clear()


def _constructed(context: _Fields, pets: Any, exchange: Exchange, lines: list[str]) -> None:
    """Construct a body from fields through its constructor, which is all that checks them."""
    kind = context.kind()
    _exchanged(
        exchange,
        lines,
        "create a name too long",
        lambda: pets.create_pet(name="far too long", kind=kind, media_type=_JSON),
        json_response(201, _CREATED),
    )
    _exchanged(
        exchange,
        lines,
        "create a numeric name",
        lambda: pets.create_pet(name=5, kind=kind, media_type=_JSON),
        json_response(201, _CREATED),
    )
    _exchanged(
        exchange,
        lines,
        "log a visit giving nothing",
        lambda: pets.log_visit(pet_id=context.pet_id("VisitsPost"), media_type=_JSON),
        raw_response(204),
    )


async def _async_fields(context: _Fields, lines: list[str]) -> None:
    exchange = Exchange(lines)
    http = exchange.async_client()
    kind = context.kind()
    body = context.models.NewPet(name="Mimi", kind=kind)
    async with context.package.AsyncClient(http_client=http) as api:
        pets = api.default
        await _aexchanged(
            exchange,
            lines,
            "async create from fields",
            lambda: pets.create_pet(name="Mimi", kind=kind, media_type=_JSON),
            json_response(201, _CREATED),
        )
        refused = pets.create_pet(body=body, name="Mimi", media_type=_JSON)
        lines.append("  async fields refused once awaited: made a coroutine")
        await arecord(lines, "async a body and fields", lambda: refused)
        async def raw() -> object:
            return await (await pets.with_raw_response.create_pet(name="Mimi", kind=kind, media_type=_JSON)).json()

        await _aexchanged(exchange, lines, "async create raw from fields", raw, json_response(201, _CREATED))
        record(
            lines,
            "async a streamed body and fields",
            lambda: pets.with_streaming_response.create_pet(body=body, name="Mimi", media_type=_JSON),
        )
        exchange.respond(json_response(201, _CREATED))
        async with pets.with_streaming_response.create_pet(name="Mimi", kind=kind, media_type=_JSON) as streamed:
            await arecord(lines, "async create streamed", streamed.read)
        exchange.responders.clear()
    await http.aclose()


def optional_models(package: ModuleType, lines: list[str]) -> None:
    """Let a call omit the fields that models generated to make every field optional construct without."""
    context = _Fields(package)
    exchange = Exchange(lines)
    http = exchange.client()
    kind = context.kind()
    with package.Client(http_client=http) as api:
        pets = api.default
        for label, call, answer in (
            ("create giving nothing", lambda: pets.create_pet(media_type=_JSON), json_response(201, _CREATED)),
            (
                "create without its kind",
                lambda: pets.create_pet(name="Mimi", media_type=_JSON),
                json_response(201, _CREATED),
            ),
            ("create an owner giving nothing", lambda: pets.create_owner(), raw_response(204)),
        ):
            _exchanged(exchange, lines, label, call, answer)
        _exchanged(
            exchange,
            lines,
            "create from its required fields",
            lambda: pets.create_pet(name="Mimi", kind=kind, media_type=_JSON),
            json_response(201, _CREATED),
        )
    http.close()
