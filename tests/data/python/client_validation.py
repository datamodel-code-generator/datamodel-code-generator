"""Call generated clients under each validation mode their package allows, and report what each mode checks."""

from __future__ import annotations

import importlib
import re
from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_runtime import Exchange, arecord, json_response, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import ModuleType

_BOUNDARY: Final = re.compile(r"dcg[0-9a-f]{32}")
_LARGE: Final = 2**40
_NAMED: Final = b'Content-Disposition: form-data; name="%s"'
_PHOTO: Final = (_NAMED % b"photo" + b'; filename="a.png"\r\nContent-Type: image/png', b"\x89PNG")


def _form(*parts: tuple[bytes, bytes]) -> bytes:
    return b"".join(b"--b1\r\n%s\r\n\r\n%s\r\n" % part for part in parts) + b"--b1--\r\n"


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


def _mutated(value: Any, **changes: object) -> Any:
    """Change a model value after construction, as a caller may, without the backend validating the change."""
    if isinstance(value, dict):
        value.update(changes)
    else:
        for name, change in changes.items():
            setattr(value, name, change)
    return value


class _Validation:
    """The modules of one generated package and the options of each validation mode."""

    def __init__(self, package: ModuleType) -> None:
        self.package = package
        self.models = importlib.import_module(f"{package.__name__}_models")
        self.options = importlib.import_module(f"{package.__name__}.options")
        self.bodies = importlib.import_module(f"{package.__name__}.bodies")
        self.bindings = importlib.import_module(f"{package.__name__}._generated.model_bindings")

    def call(self, **modes: object) -> Any:
        """Return call options that select validation modes."""
        return self.options.RequestOptions(validation=self.options.ValidationOptions(**modes))

    def bundles(self, lines: list[str], label: str) -> None:
        """Report whether any schema bundle was built, which only schema validation needs."""
        built = (
            self.bindings.request_bundle.cache_info().currsize,
            self.bindings.response_bundle.cache_info().currsize,
        )
        lines.append(f"  schema bundles {label}: request {built[0]} response {built[1]}")

    def pet(self, **fields: object) -> Any:
        return self.models.Pet(**{"id": 1, "name": "Mimi", **fields})

    def value(self, kind: str, value: object) -> Any:
        """Return a value of a generated parameter type, which a Pydantic model wraps and other backends alias."""
        declared = getattr(self.models, kind)
        return getattr(declared, "__value__", declared)(value)


def validation(package: ModuleType, lines: list[str]) -> None:
    """Send and read the same values under every allowed mode, and refuse modes and options the package does not allow."""
    context = _Validation(package)
    exchange = Exchange(lines)
    http = exchange.client()
    with package.Client(http_client=http) as api:
        _requests(context, api.default, exchange, lines)
        _responses(context, api.default, exchange, lines)
        _errors(context, api.default, exchange, lines)
        _parts(context, api.default, exchange, lines)
        _envelopes(context, api.default, exchange, lines)
        context.bundles(lines, "after schema calls")
        _layers(context, api, http, exchange, lines)
    http.close()
    run(lambda: _async_validation(context, lines))
    lines[:] = [_BOUNDARY.sub("<boundary>", line) for line in lines]


def _requests(context: _Validation, pets: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send what each request mode accepts: none serializes, native asks the backend, schema checks the wire."""
    _exchanged(
        exchange,
        lines,
        "create",
        lambda: pets.create_pet(body=context.pet(secret="s")),
        json_response(201, {"id": 1, "name": "Mimi"}),
    )
    context.bundles(lines, "after none and native calls")
    for mode in ("none", "native", "schema"):
        large = context.pet(age=_LARGE)
        _exchanged(
            exchange,
            lines,
            f"create an age beyond int32, request {mode}",
            lambda: pets.create_pet(body=large, options=context.call(request=mode)),
            json_response(201, {"id": 1, "name": "Mimi"}),
        )
        long = _mutated(context.pet(), name="far too long")
        _exchanged(
            exchange,
            lines,
            f"create a name mutated too long, request {mode}",
            lambda: pets.create_pet(body=long, options=context.call(request=mode)),
            json_response(201, {"id": 1, "name": "Mimi"}),
        )
        _exchanged(
            exchange,
            lines,
            f"list beyond an int32 limit, request {mode}",
            lambda: pets.list_pets(
                limit=context.value("FieldPetsGetQueryLimitParameter", _LARGE), options=context.call(request=mode)
            ),
            json_response(200, []),
        )
    _exchanged(
        exchange,
        lines,
        "create from a mapping, request native",
        lambda: pets.create_pet(body={"id": 1, "name": "Mimi"}, options=context.call(request="native")),
        json_response(201, {"id": 1, "name": "Mimi"}),
    )
    record(
        lines,
        "create from a mapping of a wrong type, request native",
        lambda: pets.create_pet(body={"id": 1, "name": 5}, options=context.call(request="native")),
    )


def _responses(context: _Validation, pets: Any, exchange: Exchange, lines: list[str]) -> None:
    """Read what each response mode accepts: native constructs the declared type, schema checks the wire first."""
    for label, payload in (
        ("a pet", {"id": 1, "name": "Mimi"}),
        ("an age beyond int32", {"id": 1, "name": "Mimi", "age": _LARGE}),
        ("a name too long", {"id": 1, "name": "far too long"}),
        ("a numeric string id", {"id": "1", "name": "Mimi"}),
        ("an undeclared member", {"id": 1, "name": "Mimi", "extra": True}),
        ("a write-only secret", {"id": 1, "name": "Mimi", "secret": "s"}),
        ("no name", {"id": 1}),
    ):
        for mode in ("native", "schema"):
            _exchanged(
                exchange,
                lines,
                f"list {label}, response {mode}",
                lambda: pets.list_pets(options=context.call(response=mode)),
                json_response(200, [payload]),
            )


def _errors(context: _Validation, pets: Any, exchange: Exchange, lines: list[str]) -> None:
    """Decode error payloads under the call's response mode, through ordinary, raw, and streaming calls."""
    for mode in ("native", "schema"):
        _exchanged(
            exchange,
            lines,
            f"list failing with a code below its minimum, response {mode}",
            lambda: pets.list_pets(options=context.call(response=mode)),
            json_response(404, {"code": 200}),
        )
        exchange.respond(json_response(404, {"code": 200}))
        raw = pets.with_raw_response.list_pets(options=context.call(response=mode))
        record(lines, f"raw list failing, response {mode}", raw.raise_for_status)
        exchange.respond(json_response(404, {"code": 200}))
        with pets.with_streaming_response.list_pets(options=context.call(response=mode)) as streamed:
            record(lines, f"streamed list failing, response {mode}", streamed.raise_for_status)


def _parts(context: _Validation, pets: Any, exchange: Exchange, lines: list[str]) -> None:
    """Read and send form-data members under each mode; a member its direction excludes never takes a part."""
    form = "multipart/form-data; boundary=b1"
    caption = (_NAMED % b"caption", b"far too long")
    for mode in ("native", "schema"):
        _exchanged(
            exchange,
            lines,
            f"card with a caption too long, response {mode}",
            lambda: [
                (part.name, part.value)
                for part in pets.get_card(
                    pet_id=context.value("FieldPetsPetIdCardGetPathPetIdParameter", 3),
                    options=context.call(response=mode),
                ).parts
            ],
            raw_response(200, _form(caption, _PHOTO), form),
        )
        _exchanged(
            exchange,
            lines,
            f"card with a write-only token, response {mode}",
            lambda: pets.get_card(
                pet_id=context.value("FieldPetsPetIdCardGetPathPetIdParameter", 3), options=context.call(response=mode)
            ),
            raw_response(200, _form(_PHOTO, (_NAMED % b"token", b"t")), form),
        )
    body, field, file = context.bodies.MultipartBody, context.bodies.FieldPart, context.bodies.FilePart
    for mode in ("none", "schema"):
        _exchanged(
            exchange,
            lines,
            f"put a caption too long, request {mode}",
            lambda: pets.put_card(
                pet_id=context.value("FieldPetsPetIdCardGetPathPetIdParameter", 3),
                body=body((field("caption", "far too long"), file("photo", b"\x89PNG"))),
                options=context.call(request=mode),
            ),
            raw_response(204),
        )
        record(
            lines,
            f"put a read-only stamp, request {mode}",
            lambda: pets.put_card(
                pet_id=context.value("FieldPetsPetIdCardGetPathPetIdParameter", 3),
                body=body((file("photo", b"\x89PNG"), field("stamp", "s"))),
                options=context.call(request=mode),
            ),
        )
        _exchanged(
            exchange,
            lines,
            f"put a read-only stamp left unset, request {mode}",
            lambda: pets.put_card(
                pet_id=context.value("FieldPetsPetIdCardGetPathPetIdParameter", 3),
                body=body((file("photo", b"\x89PNG"), field("stamp", context.options.UNSET))),
                options=context.call(request=mode),
            ),
            raw_response(204),
        )
    for mode in ("none", "native", "schema"):
        for label, headers in (
            ("counted below its minimum and traced out of its pattern", (("X-Count", "0"), ("X-Trace", "u"))),
            ("counted in words", (("X-Count", "many"),)),
            ("without its count", ()),
        ):
            _exchanged(
                exchange,
                lines,
                f"put a token {label}, request {mode}",
                lambda: pets.put_card(
                    pet_id=context.value("FieldPetsPetIdCardGetPathPetIdParameter", 3),
                    body=body((file("photo", b"\x89PNG"), field("token", "t", headers=headers))),
                    options=context.call(request=mode),
                ),
                raw_response(204),
            )


def _envelopes(context: _Validation, pets: Any, exchange: Exchange, lines: list[str]) -> None:
    """Read an envelope use as strictly under native responses as under schema ones, since it keeps its snapshot."""
    account = context.value("FieldAccountsAccountIdGetPathAccountIdParameter", 1)
    for label, payload in (
        ("an account", {"id": 1}),
        ("an account with an undeclared member", {"id": 1, "extra": True}),
    ):
        _exchanged(exchange, lines, label, lambda: pets.get_account(account_id=account), json_response(200, payload))


def _layers(context: _Validation, api: Any, http: Any, exchange: Exchange, lines: list[str]) -> None:
    """Merge modes per axis as call, view, client, then the generated default, and refuse modes the package lacks."""
    options = context.options
    validation = options.ValidationOptions
    strict = context.package.Client(
        http_client=http, options=options.ClientOptions(validation=validation(response="schema"))
    )
    view = strict.with_options(options.RequestOptions(validation=validation(request="schema")))
    payload = {"id": 1, "name": "Mimi", "age": _LARGE}
    _exchanged(
        exchange,
        lines,
        "list through a client reading schemas",
        strict.default.list_pets,
        json_response(200, [payload]),
    )
    _exchanged(
        exchange,
        lines,
        "list through it, response native",
        lambda: strict.default.list_pets(options=context.call(response="native")),
        json_response(200, [payload]),
    )
    record(
        lines, "create through its view sending schemas", lambda: view.default.create_pet(body=context.pet(age=_LARGE))
    )
    _exchanged(exchange, lines, "list through its view", view.default.list_pets, json_response(200, [payload]))
    for label, build in (
        ("view selecting native requests", lambda: type(api.with_options(context.call(request="native"))).__name__),
        ("request mode None", lambda: validation(request=None)),
        ("response mode none", lambda: validation(response="none")),
        ("validation of another type", lambda: options.RequestOptions(validation="schema")),
        ("unset", lambda: validation()),
    ):
        record(lines, label, build)
    _exchanged(
        exchange,
        lines,
        "call selecting native requests",
        lambda: api.default.list_pets(options=context.call(request="native")),
        json_response(200, []),
    )


async def _async_validation(context: _Validation, lines: list[str]) -> None:
    """Send and read with asyncio under the default modes and under schema ones."""
    exchange = Exchange(lines)
    http = exchange.async_client()
    async with context.package.AsyncClient(http_client=http) as api:
        pets = api.default
        large = context.pet(age=_LARGE)
        await _aexchanged(
            exchange,
            lines,
            "async create, request none",
            lambda: pets.create_pet(body=large),
            json_response(201, {"id": 1, "name": "Mimi", "age": _LARGE}),
        )
        await arecord(
            lines,
            "async create, request schema",
            lambda: pets.create_pet(body=large, options=context.call(request="schema")),
        )
        await _aexchanged(
            exchange,
            lines,
            "async list, response schema",
            lambda: pets.list_pets(options=context.call(response="schema")),
            json_response(200, [{"id": 1, "name": "Mimi", "age": _LARGE}]),
        )
        exchange.respond(json_response(404, {"code": 200}))
        raw = await pets.with_raw_response.list_pets()
        await arecord(lines, "async raw list failing, response native", raw.raise_for_status)
    await http.aclose()

