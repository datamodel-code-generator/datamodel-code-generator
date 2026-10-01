"""Exercise public body preparation failures and retained cleanup after interrupted partial opens."""

from __future__ import annotations

import asyncio
import importlib
import io
from typing import TYPE_CHECKING, Any

from tests.data.python.client_body_replay import _Attempt, _Chunks, _Factory, _File, _Replies
from tests.data.python.client_runtime import Exchange, arecord, record, run

if TYPE_CHECKING:
    from types import ModuleType


class _BrokenFile(_File):
    """Inject a positioning failure and an independent owned-close failure."""

    def __init__(self) -> None:
        super().__init__(b"body")
        self.primary = OSError("position failed")
        self.secondary = OSError("close failed")

    def tell(self) -> int:
        raise self.primary

    def close(self) -> None:
        super().close()
        raise self.secondary


class _Metadata(_Attempt):
    """Inject a factory result's length-property failure before any network send."""

    def __init__(self, *, failure: BaseException | None = None) -> None:
        super().__init__(failure=failure)
        self.primary = ValueError("length getter failed")

    @property
    def content_length(self) -> int | None:
        raise self.primary


class _Stopped(BaseException):
    """Inject an exact native interruption without stopping the test runner itself."""


class _ClosingFile(_File):
    """Close a real file before injecting its selected cleanup failure."""

    def __init__(self, failure: BaseException | None = None, *, broken: bool = False) -> None:
        super().__init__(b"body")
        self.failure = failure
        self.broken = broken

    def tell(self) -> int:
        if self.broken:
            msg = "capture failed before cleanup"
            raise OSError(msg)
        return super().tell()

    def close(self) -> None:
        super().close()
        if self.failure is not None:
            raise self.failure


class _SDKFile(_File):
    """Inject a caller-created SDK error into file metadata or its later rewind."""

    def __init__(self, failure: BaseException, *, armed: bool) -> None:
        super().__init__(b"body")
        self.failure = failure
        self.armed = armed
        self.failed_calls = 0

    def seek(self, offset: int, whence: int = 0, /) -> int:
        if self.armed:
            self.failed_calls += 1
            raise self.failure
        return super().seek(offset, whence)

    def on_event(self, event: Any) -> None:
        if event.name == "call_start":
            self.armed = True


class _Unsupported(_File):
    """Expose a real readable file whose position or end seek is unavailable."""

    def __init__(self, *, tell: bool) -> None:
        super().__init__(b"streamed")
        self.no_tell = tell

    def tell(self) -> int:
        if self.no_tell:
            msg = "position unavailable"
            raise io.UnsupportedOperation(msg)
        return super().tell()

    def seek(self, offset: int, whence: int = 0, /) -> int:
        if not self.no_tell:
            msg = "seek unavailable"
            raise io.UnsupportedOperation(msg)
        return super().seek(offset, whence)


class _Gated(_Attempt):
    """Hold owned attempt cleanup until the caller has left, then record its completion."""

    def __init__(self, *, length: int, fail: bool = False, metadata: bool = False) -> None:
        super().__init__(length=length)
        self.entered = asyncio.Event()
        self.proceed = asyncio.Event()
        self.finished = 0
        self.fail = fail
        self.late = OSError("late close failed")
        self.metadata = metadata

    @property
    def content_length(self) -> int | None:
        if self.metadata:
            msg = "gated metadata failed"
            raise ValueError(msg)
        return super().content_length

    async def aclose(self) -> None:
        self.closes += 1
        self.entered.set()
        await self.proceed.wait()
        self.finished += 1
        if self.fail:
            raise self.late


def body_replay_faults(package: ModuleType, lines: list[str]) -> None:
    """Report preparation attribution, source cleanup, unsupported seeks, and cancellation ownership."""
    bodies, options = (importlib.import_module(f"{package.__name__}.{name}") for name in ("bodies", "options"))
    exchange = Exchange([])
    replies = _Replies(exchange)
    config = options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
    with exchange.client() as native, package.Client(http_client=native, options=config) as api:
        file = _BrokenFile()
        replies.reset(200)
        try:
            api.request_raw("PUT", "https://body.example.com/", body=bodies.FileBody(file, ownership="owned"))
        except Exception as error:  # noqa: BLE001
            lines.append(
                f"  file preparation {type(error).__name__}"
                f" cause={error.cause is file.primary}"
                f" secondary={error.secondary_errors == (file.secondary,)}"
                f" closes={file.closes}"
                f" sends={len(replies.requests)}"
            )
        attempt = _Metadata(failure=OSError("metadata close failed"))
        try:
            api.request_raw("PUT", "https://body.example.com/", body=bodies.BodyFactory(_Factory((attempt,))))
        except Exception as error:  # noqa: BLE001
            lines.append(
                f"  factory metadata {type(error).__name__}"
                f" cause={error.cause is attempt.primary}"
                f" secondary={[type(item).__name__ for item in error.secondary_errors]}"
                f" closes={attempt.closes}"
                f" sends={len(replies.requests)}"
            )
        for position in (False, True):
            file = _Unsupported(tell=position)
            replies.reset(200)
            record(
                lines,
                f"unsupported {'tell' if position else 'seek'}",
                lambda file=file: (
                    api.request_raw("PUT", "https://body.example.com/", body=bodies.FileBody(file)).body_bytes
                ),
            )
            replies.report(lines)
            file.close()
        file = _File(b"x")
        file.seek(2)
        replies.reset(503, 200)
        record(
            lines,
            "past EOF replays empty",
            lambda: api.request_raw("PUT", "https://body.example.com/", body=bodies.FileBody(file)).body_bytes,
        )
        replies.report(lines)
        lines.append(f"    offset={file.tell()} closes={file.closes}")
        file.close()
        _partial_capture(api, bodies, lines)
        _factory_checks(api, bodies, replies, lines)
        _metadata_categories(package, api, bodies, replies, lines)
        _changed_parts(api, bodies, options, replies, lines)
        _native_cleanup(api, bodies, replies, lines)
        _file_changes(api, bodies, replies, lines)
        _getter_errors(api, bodies, replies, lines)
        _direct_inputs(bodies, lines)
        _file_metadata_errors(package, api, bodies, options, replies, lines)
        _nested_file_factory(api, bodies, replies, lines)
    run(lambda: _async_faults(package, bodies, options, lines))


def _partial_capture(api: Any, bodies: ModuleType, lines: list[str]) -> None:
    first, broken = _BrokenFile(), _BrokenFile()
    first.tell = lambda: 0
    body = bodies.MultipartBody((
        bodies.FilePart("first", bodies.FileBody(first, ownership="owned")),
        bodies.FilePart("broken", bodies.FileBody(broken, ownership="owned")),
    ))
    try:
        api.request_raw("PUT", "https://body.example.com/", body=body)
    except Exception as error:  # noqa: BLE001
        lines.append(
            f"  partial capture {type(error).__name__}"
            f" secondary={[type(item).__name__ for item in error.secondary_errors]}"
            f" closes={(first.closes, broken.closes)}"
        )


def _factory_checks(api: Any, bodies: ModuleType, replies: _Replies, lines: list[str]) -> None:
    class Changing(bodies.BodyFactory):
        def __init__(self, factory: _Factory) -> None:
            super().__init__(factory)
            self.fingerprints = iter((b"first", b"first", b"changed"))

        @property
        def fingerprint(self) -> bytes:
            return next(self.fingerprints)

    first, second = _Attempt(length=7), _Attempt(length=8, failure=OSError("rejected close failed"))
    factory = _Factory((first, second))
    replies.reset(503, 200)
    try:
        api.request_raw("PUT", "https://body.example.com/", body=bodies.BodyFactory(factory))
    except Exception as error:  # noqa: BLE001
        lines.append(
            f"  changed length {type(error).__name__}"
            f" secondary={[type(item).__name__ for item in error.secondary_errors]}"
            f" closes={(first.closes, second.closes)}"
        )
    factory = _Factory()
    replies.reset(503, 200)
    record(
        lines,
        "changed fingerprint",
        lambda: api.request_raw("PUT", "https://body.example.com/", body=Changing(factory)).body_bytes,
    )
    lines.append(f"    sends={len(replies.requests)} closes={[item.closes for item in factory.attempts]}")


async def _async_faults(package: ModuleType, bodies: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange([])
    replies = _Replies(exchange)
    config = options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
    async with exchange.async_client() as native, package.AsyncClient(http_client=native, options=config) as api:
        file = _BrokenFile()
        body = bodies.AsyncFileBody(file, ownership="owned")
        replies.reset(200)
        try:
            await api.request_raw("PUT", "https://body.example.com/", body=body)
        except Exception as error:  # noqa: BLE001
            lines.append(
                f"  async file preparation {type(error).__name__}"
                f" cause={error.cause is file.primary}"
                f" secondary={error.secondary_errors == (file.secondary,)}"
                f" closes={file.closes}"
                f" sends={len(replies.requests)}"
            )
        await body.aclose()
        attempt = _Metadata(failure=OSError("metadata close failed"))
        try:
            await api.request_raw(
                "PUT", "https://body.example.com/", body=bodies.AsyncBodyFactory(_Factory((attempt,)).async_call)
            )
        except Exception as error:  # noqa: BLE001
            lines.append(
                f"  async factory metadata {type(error).__name__}"
                f" cause={error.cause is attempt.primary}"
                f" secondary={[type(item).__name__ for item in error.secondary_errors]}"
                f" closes={attempt.closes}"
                f" sends={len(replies.requests)}"
            )
        file = _File(b"x")
        file.seek(2)
        body = bodies.AsyncFileBody(file)
        replies.reset(503, 200)
        result = await api.request_raw("PUT", "https://body.example.com/", body=body)
        lines.append(f"  async past EOF replays empty = {result.body_bytes!r}")
        replies.report(lines)
        lines.append(f"    offset={file.tell()} closes={file.closes}")
        await body.aclose()
        file.close()
        await _async_partial_capture(api, bodies, lines)
        await _async_metadata_categories(package, api, bodies, replies, lines)
        await _async_changed_parts(api, bodies, options, replies, lines)
        await _async_native_cleanup(api, bodies, replies, lines)
        await _async_file_changes(api, bodies, replies, lines)
        await _async_direct_inputs(bodies, lines)
        await _async_file_metadata_errors(package, api, bodies, options, replies, lines)
    for mode in ("native", "deadline", "cancel_token"):
        for kind in ("length", "metadata", "multipart"):
            await _cancel_partial(package, bodies, options, lines, mode=mode, kind=kind)


async def _async_partial_capture(api: Any, bodies: ModuleType, lines: list[str]) -> None:
    first, broken = _BrokenFile(), _BrokenFile()
    first.tell = lambda: 0
    a, b = bodies.AsyncFileBody(first, ownership="owned"), bodies.AsyncFileBody(broken, ownership="owned")
    body = bodies.AsyncMultipartBody((bodies.FilePart("first", a), bodies.FilePart("broken", b)))
    try:
        await api.request_raw("PUT", "https://body.example.com/", body=body)
    except Exception as error:  # noqa: BLE001
        lines.append(
            f"  async partial capture {type(error).__name__}"
            f" secondary={[type(item).__name__ for item in error.secondary_errors]}"
            f" closes={(first.closes, broken.closes)}"
        )
    await a.aclose()
    await b.aclose()


async def _cancel_partial(
    package: ModuleType, bodies: ModuleType, options: ModuleType, lines: list[str], *, mode: str, kind: str
) -> None:
    exchange = Exchange([])
    replies = _Replies(exchange)
    attempt = _Gated(length=8, fail=True, metadata=kind == "metadata")
    token = options.CancelToken()
    config = options.ClientOptions(
        total_timeout=1.0 if mode == "deadline" else None,
        cleanup_timeout=0.005 if mode == "native" else 0.2,
        retry=options.RetryOptions(initial_delay=0),
    )
    failures: list[BaseException] = []
    async with exchange.async_client() as native:
        api = package.AsyncClient(http_client=native, options=config)
        if kind == "multipart":

            async def broken(_context: object) -> _Attempt:  # noqa: RUF029
                msg = "second factory failed"
                raise ValueError(msg)

            body = bodies.AsyncMultipartBody((
                bodies.FilePart("first", bodies.AsyncBodyFactory(_Factory((attempt,)).async_call)),
                bodies.FilePart("second", bodies.AsyncBodyFactory(broken)),
            ))
            replies.reset(200)
        elif kind == "length":
            body = bodies.AsyncBodyFactory(_Factory((_Attempt(length=7), attempt)).async_call)
            replies.reset(503, 200)
        else:
            body = bodies.AsyncBodyFactory(_Factory((attempt,)).async_call)
            replies.reset(200)

        async def call() -> None:
            try:
                await api.request_raw(
                    "PUT",
                    "https://body.example.com/",
                    body=body,
                    options=options.RequestOptions(
                        cancel_token=token, cleanup_timeout=2.0 if mode == "deadline" else config.cleanup_timeout
                    ),
                )
            except BaseException as error:  # noqa: BLE001
                failures.append(error)

        caller = asyncio.create_task(call())
        label = f"{mode} {kind} cleanup"
        try:
            await asyncio.wait_for(attempt.entered.wait(), 5)
            if mode == "native":
                caller.cancel("partial cleanup cancellation")
                await asyncio.sleep(0)
                caller.cancel("repeated cleanup cancellation")
            elif mode == "cancel_token":
                token.cancel()
            await asyncio.wait_for(caller, 5)
            lines.append(
                f"  {label} before release"
                f" closes={attempt.closes} finished={attempt.finished}"
                f" sends={len(replies.requests)}"
                f" failure={type(failures[0]).__name__}"
            )
            await arecord(lines, f"{label} drain pending", api.aclose)
            attempt.proceed.set()
            await api.aclose()

            failure = failures[0]
            secondary = getattr(failure, "secondary_errors", ())
            related = (*secondary, *(() if (cause := getattr(failure, "cause", None)) is None else (cause,)))
            observed = (
                *related,
                *(item for primary in related for item in getattr(primary, "secondary_errors", ())),
            )
            retained = (
                None
                if isinstance(failure, asyncio.CancelledError)
                else any(item is attempt.late or getattr(item, "cause", None) is attempt.late for item in observed)
            )
            lines.extend((
                (
                    f"    retained_late={retained}"
                    f" no_cause_cycle={all(getattr(item, 'cause', None) is not failure for item in observed)}"
                ),
                (
                    f"    released"
                    f" closes={attempt.closes} finished={attempt.finished}"
                    f" args={failure.args}"
                    f" notes={tuple(getattr(failure, '__notes__', ()))}"
                    f" cause={type(getattr(failure, 'cause', None)).__name__}"
                    f" secondary={[type(item).__name__ for item in getattr(failure, 'secondary_errors', ())]}"
                ),
            ))
        finally:
            attempt.proceed.set()
            await asyncio.wait_for(caller, 5)
            await api.aclose()


def _metadata_categories(
    package: ModuleType, api: Any, bodies: ModuleType, replies: _Replies, lines: list[str]
) -> None:
    errors = importlib.import_module(f"{package.__name__}.errors")
    attempt = _Metadata()
    attempt.primary = errors.TransportError(phase="read", delivery_state=errors.DeliveryState.MAYBE_SENT)
    factory = _Factory((attempt,))
    replies.reset(200)
    try:
        api.request_raw("PUT", "https://body.example.com/", body=bodies.BodyFactory(factory))
    except Exception as error:  # noqa: BLE001
        lines.append(
            f"  SDK metadata error {type(error).__name__} cause={error.cause is attempt.primary}"
            f" callbacks={len(factory.contexts)} closes={attempt.closes} sends={len(replies.requests)}"
        )
    first = _Attempt(failure=OSError("partial attempt close failed"))

    def broken(_context: object) -> _Attempt:
        msg = "partial factory failed"
        raise ValueError(msg)

    parts = bodies.MultipartBody((
        bodies.FilePart("first", bodies.BodyFactory(_Factory((first,)))),
        bodies.FilePart("second", bodies.BodyFactory(broken)),
    ))
    try:
        api.request_raw("PUT", "https://body.example.com/", body=parts)
    except Exception as error:  # noqa: BLE001
        lines.append(
            f"  partial open {type(error).__name__}"
            f" secondary={[type(item).__name__ for item in error.secondary_errors]}"
            f" closes={first.closes} sends={len(replies.requests)}"
        )


async def _async_metadata_categories(
    package: ModuleType, api: Any, bodies: ModuleType, replies: _Replies, lines: list[str]
) -> None:
    errors = importlib.import_module(f"{package.__name__}.errors")
    attempt = _Metadata()
    attempt.primary = errors.TransportError(phase="read", delivery_state=errors.DeliveryState.MAYBE_SENT)
    factory = _Factory((attempt,))
    replies.reset(200)
    try:
        await api.request_raw("PUT", "https://body.example.com/", body=bodies.AsyncBodyFactory(factory.async_call))
    except Exception as error:  # noqa: BLE001
        lines.append(
            f"  async SDK metadata error {type(error).__name__} cause={error.cause is attempt.primary}"
            f" callbacks={len(factory.contexts)} closes={attempt.closes} sends={len(replies.requests)}"
        )
    for closing in (False, True):
        attempt = _Metadata(failure=OSError("direct close failed") if closing else None)
        factory = bodies.AsyncBodyFactory(_Factory((attempt,)).async_call)
        context = bodies.BodyAttemptContext(call_id="direct", attempt_index=0, hop_index=0, remaining_timeout=None)
        try:
            await factory(context)
        except Exception as error:  # noqa: BLE001
            lines.append(
                f"  direct async metadata {type(error).__name__} cause={error.cause is attempt.primary}"
                f" secondary={[type(item).__name__ for item in error.secondary_errors]} closes={attempt.closes}"
            )


class _Switch:
    """Abnormally replace explicit parts after capture, so omitted and new resources both need ownership."""

    def __init__(self, original: tuple[Any, ...], replacement: tuple[Any, ...]) -> None:
        self.parts = original
        self.replacement = replacement

    def on_event(self, event: Any) -> None:
        if event.name == "call_start":
            self.parts = self.replacement


def _changed_parts(api: Any, bodies: ModuleType, options: ModuleType, replies: _Replies, lines: list[str]) -> None:
    class Changed(bodies.MultipartBody):
        @property
        def parts(self) -> tuple[Any, ...]:
            return switch.parts

    for fail in (False, True):
        original = _BrokenFile() if fail else _File(b"original")
        if fail:
            original.tell = lambda: 0
        changed = _File(b"changed")
        replacement = (bodies.FilePart("new", bodies.FileBody(changed, ownership="owned")),)
        if fail:
            spent = bodies.StreamBody(iter((b"spent",)))
            spent(bodies.BodyAttemptContext(call_id="spent", attempt_index=0, hop_index=0, remaining_timeout=None))
            replacement += (bodies.FilePart("spent", spent),)
        switch = _Switch((bodies.FilePart("old", bodies.FileBody(original, ownership="owned")),), replacement)
        replies.reset(200)
        try:
            value = api.request_raw(
                "PUT", "https://body.example.com/", body=Changed(()), options=options.RequestOptions(hooks=(switch,))
            )
        except Exception as error:  # noqa: BLE001
            lines.append(
                f"  changed parts failed {type(error).__name__}"
                f" secondary={[type(item).__name__ for item in error.secondary_errors]}"
            )
        else:
            lines.append(f"  changed parts = {value.body_bytes!r}")
        lines.append(f"    closes={(original.closes, changed.closes)} sends={len(replies.requests)}")


async def _async_changed_parts(
    api: Any, bodies: ModuleType, options: ModuleType, replies: _Replies, lines: list[str]
) -> None:
    class Changed(bodies.AsyncMultipartBody):
        @property
        def parts(self) -> tuple[Any, ...]:
            return switch.parts

    for fail in (False, True):
        original = _BrokenFile() if fail else _File(b"original")
        if fail:
            original.tell = lambda: 0
        changed = _File(b"changed")
        first, second = (
            bodies.AsyncFileBody(original, ownership="owned"),
            bodies.AsyncFileBody(changed, ownership="owned"),
        )
        chunks = _Chunks()
        stream = bodies.AsyncStreamBody(chunks, ownership="owned")
        if fail:
            spent = await stream(
                bodies.BodyAttemptContext(call_id="spent", attempt_index=0, hop_index=0, remaining_timeout=None)
            )
            await spent.aclose()
        replacement = (bodies.FilePart("new", second), bodies.FilePart("stream", stream))
        switch = _Switch((bodies.FilePart("old", first),), replacement)
        replies.reset(200)
        try:
            value = await api.request_raw(
                "PUT", "https://body.example.com/", body=Changed(()), options=options.RequestOptions(hooks=(switch,))
            )
        except Exception as error:  # noqa: BLE001
            lines.append(
                f"  async changed parts failed {type(error).__name__}"
                f" secondary={[type(item).__name__ for item in error.secondary_errors]}"
            )
        else:
            lines.append(f"  async changed parts = {value.body_bytes!r}")
        lines.append(f"    closes={(original.closes, changed.closes, chunks.closes)} sends={len(replies.requests)}")
        await first.aclose()
        await second.aclose()


def _native_cleanup(api: Any, bodies: ModuleType, replies: _Replies, lines: list[str]) -> None:
    for partial in (False, True):
        native = _Stopped("native attempt close")
        attempts = (_Attempt(failure=OSError("first close failed")), _Attempt(failure=native), _Attempt())
        parts = tuple(
            bodies.FilePart(str(index), bodies.BodyFactory(_Factory((attempt,))))
            for index, attempt in enumerate(attempts)
        )
        if partial:

            def broken(_context: object) -> _Attempt:
                msg = "last factory failed"
                raise ValueError(msg)

            parts += (bodies.FilePart("broken", bodies.BodyFactory(broken)),)
        replies.reset(200)
        try:
            api.request_raw("PUT", "https://body.example.com/", body=bodies.MultipartBody(parts))
        except BaseException as error:  # noqa: BLE001
            lines.append(
                f"  native {'partial' if partial else 'final'} attempt cleanup"
                f" identity={error is native} closes={tuple(attempt.closes for attempt in attempts)}"
                f" sends={len(replies.requests)} notes={tuple(getattr(error, '__notes__', ()))}"
            )
    for partial in (False, True):
        native = _Stopped("native source close")
        files = (_ClosingFile(OSError("first close failed")), _ClosingFile(), _ClosingFile(native, broken=partial))
        parts = tuple(
            bodies.FilePart(str(index), bodies.FileBody(file, ownership="owned")) for index, file in enumerate(files)
        )
        replies.reset(200)
        try:
            api.request_raw("PUT", "https://body.example.com/", body=bodies.MultipartBody(parts))
        except BaseException as error:  # noqa: BLE001
            lines.append(
                f"  native {'capture' if partial else 'final'} source cleanup"
                f" identity={error is native} closes={tuple(file.closes for file in files)}"
                f" sends={len(replies.requests)} notes={tuple(getattr(error, '__notes__', ()))}"
            )
    native = _Stopped("native metadata close")
    attempt = _Metadata(failure=native)
    replies.reset(200)
    try:
        api.request_raw("PUT", "https://body.example.com/", body=bodies.BodyFactory(_Factory((attempt,))))
    except BaseException as error:  # noqa: BLE001
        lines.append(
            f"  native metadata cleanup identity={error is native}"
            f" closes={attempt.closes} sends={len(replies.requests)}"
        )


async def _async_native_cleanup(api: Any, bodies: ModuleType, replies: _Replies, lines: list[str]) -> None:
    for partial in (False, True):
        native = asyncio.CancelledError("native attempt close")
        attempts = (_Attempt(failure=OSError("first close failed")), _Attempt(failure=native), _Attempt())
        parts = tuple(
            bodies.FilePart(str(index), bodies.AsyncBodyFactory(_Factory((attempt,)).async_call))
            for index, attempt in enumerate(attempts)
        )
        if partial:

            async def broken(_context: object) -> _Attempt:
                await asyncio.sleep(0)
                msg = "last factory failed"
                raise ValueError(msg)

            parts += (bodies.FilePart("broken", bodies.AsyncBodyFactory(broken)),)
        replies.reset(200)
        try:
            await api.request_raw("PUT", "https://body.example.com/", body=bodies.AsyncMultipartBody(parts))
        except BaseException as error:  # noqa: BLE001
            lines.append(
                f"  async native {'partial' if partial else 'final'} attempt cleanup"
                f" identity={error is native} closes={tuple(attempt.closes for attempt in attempts)}"
                f" sends={len(replies.requests)} notes={tuple(getattr(error, '__notes__', ()))}"
            )
    for partial in (False, True):
        native = asyncio.CancelledError("native source close")
        files = (_ClosingFile(OSError("first close failed")), _ClosingFile(), _ClosingFile(native, broken=partial))
        inputs = tuple(bodies.AsyncFileBody(file, ownership="owned") for file in files)
        parts = tuple(bodies.FilePart(str(index), body) for index, body in enumerate(inputs))
        replies.reset(200)
        try:
            await api.request_raw("PUT", "https://body.example.com/", body=bodies.AsyncMultipartBody(parts))
        except BaseException as error:  # noqa: BLE001
            lines.append(
                f"  async native {'capture' if partial else 'final'} source cleanup"
                f" identity={error is native} closes={tuple(file.closes for file in files)}"
                f" sends={len(replies.requests)} notes={tuple(getattr(error, '__notes__', ()))}"
            )
        for body in inputs:
            await body.aclose()
    native = asyncio.CancelledError("native metadata close")
    attempt = _Metadata(failure=native)
    replies.reset(200)
    try:
        await api.request_raw(
            "PUT", "https://body.example.com/", body=bodies.AsyncBodyFactory(_Factory((attempt,)).async_call)
        )
    except BaseException as error:  # noqa: BLE001
        lines.append(
            f"  async native metadata cleanup identity={error is native}"
            f" closes={attempt.closes} sends={len(replies.requests)}"
        )


def _file_changes(api: Any, bodies: ModuleType, replies: _Replies, lines: list[str]) -> None:
    for fails in (False, True):
        file = _File(b"initial")

        def broken_seek(_offset: int, _whence: int = 0, /) -> int:
            msg = "replay seek failed"
            raise OSError(msg)

        def change(file: _File = file, *, fails: bool = fails) -> None:
            if fails:
                file.seek = broken_seek
            else:
                file.truncate(3)

        replies.reset(503, 200, change=change)
        record(
            lines,
            f"file replay {'seek' if fails else 'length'} changed",
            lambda file=file: api.request_raw("PUT", "https://body.example.com/", body=bodies.FileBody(file)),
        )
        lines.append(f"    sends={len(replies.requests)} borrowed_open={not file.closed}")
        file.close()
    replies.reset(200)
    record(
        lines,
        "owned stream without close",
        lambda: (
            api.request_raw(
                "PUT", "https://body.example.com/", body=bodies.StreamBody((b"tuple",), ownership="owned")
            ).body_bytes
        ),
    )
    replies.report(lines)


async def _async_file_changes(api: Any, bodies: ModuleType, replies: _Replies, lines: list[str]) -> None:
    class Chunks:
        async def __aiter__(self) -> Any:
            yield b"iterable"

    for fails in (False, True):
        file = _File(b"initial")
        body = bodies.AsyncFileBody(file)

        def broken_seek(_offset: int, _whence: int = 0, /) -> int:
            msg = "replay seek failed"
            raise OSError(msg)

        def change(file: _File = file, *, fails: bool = fails) -> None:
            if fails:
                file.seek = broken_seek
            else:
                file.truncate(3)

        replies.reset(503, 200, change=change)
        await arecord(
            lines,
            f"async file replay {'seek' if fails else 'length'} changed",
            lambda body=body: api.request_raw("PUT", "https://body.example.com/", body=body),
        )
        lines.append(f"    sends={len(replies.requests)} borrowed_open={not file.closed}")
        await body.aclose()
        file.close()
    replies.reset(200)
    result = await api.request_raw(
        "PUT", "https://body.example.com/", body=bodies.AsyncStreamBody(Chunks(), ownership="owned")
    )
    lines.extend((
        f"  async owned stream without close = {result.body_bytes!r}",
        f"    sends={len(replies.requests)} payload={replies.requests[0][3]!r}",
    ))


def _getter_errors(api: Any, bodies: ModuleType, replies: _Replies, lines: list[str]) -> None:
    class Declared(bodies.BodyFactory):
        @property
        def content_length(self) -> int | None:
            msg = "factory declaration failed"
            raise ValueError(msg)

    class Changed(bodies.BodyFactory):
        def __init__(self, factory: _Factory) -> None:
            super().__init__(factory)
            self.calls = 0

        @property
        def fingerprint(self) -> bytes:
            self.calls += 1
            if self.calls > 1:
                msg = "factory fingerprint failed"
                raise ValueError(msg)
            return b"entry"

    for constructor in (Declared, Changed):
        factory = _Factory()
        replies.reset(200)
        record(
            lines,
            f"{constructor.__name__} getter failure",
            lambda constructor=constructor, factory=factory: api.request_raw(
                "PUT", "https://body.example.com/", body=constructor(factory)
            ),
        )
        lines.append(
            f"    callbacks={len(factory.contexts)} closes={[item.closes for item in factory.attempts]}"
            f" sends={len(replies.requests)}"
        )


def _direct_inputs(bodies: ModuleType, lines: list[str]) -> None:
    context = bodies.BodyAttemptContext(call_id="direct", attempt_index=0, hop_index=0, remaining_timeout=None)
    for primary in (OSError("direct seek failed"), _Stopped("direct seek stopped")):
        file = _BrokenFile()
        file.primary = primary
        body = bodies.FileBody(file)
        try:
            body(context)
        except BaseException as error:  # noqa: BLE001
            lines.append(
                f"  direct file preparation {type(error).__name__}"
                f" identity={error is primary} cause={getattr(error, 'cause', None) is primary}"
            )
        file.tell = lambda file=file: io.BytesIO.tell(file)
        attempt = body(context)
        lines.append(f"    claim released payload={b''.join(attempt.iter_bytes())!r}")
        attempt.close()
        io.BytesIO.close(file)
    file = _File(b"closed")
    file.close()
    record(lines, "direct closed file", lambda: bodies.FileBody(file)(context))
    chunks = _Chunks()
    attempt = bodies.StreamBody(chunks, ownership="owned")(context)
    payload = b"".join(attempt.iter_bytes())
    attempt.close()
    attempt.close()
    lines.append(f"  direct owned stream payload={payload!r} begins={chunks.begins} closes={chunks.closes}")

    class Typed(_Attempt):
        @property
        def content_type(self) -> str | None:
            msg = "attempt media type failed"
            raise ValueError(msg)

    raw = Typed()
    attempt = bodies.BodyFactory(_Factory((raw,)))(context)
    record(lines, "direct media type getter", lambda: attempt.content_type)
    attempt.close()
    lines.append(f"    closes={raw.closes}")


async def _async_direct_inputs(bodies: ModuleType, lines: list[str]) -> None:
    context = bodies.BodyAttemptContext(call_id="direct", attempt_index=0, hop_index=0, remaining_timeout=None)
    for primary in (OSError("direct seek failed"), _Stopped("direct seek stopped")):
        file = _BrokenFile()
        file.primary = primary
        body = bodies.AsyncFileBody(file, ownership="owned")
        try:
            await body(context)
        except BaseException as error:  # noqa: BLE001
            lines.append(
                f"  direct async file preparation {type(error).__name__}"
                f" identity={error is primary} cause={getattr(error, 'cause', None) is primary}"
                f" secondary={getattr(error, 'secondary_errors', ()) == (file.secondary,)} closes={file.closes}"
            )
        await body.aclose()
    file = _File(b"stopped")
    body = bodies.AsyncFileBody(file)
    await body.aclose()
    await arecord(lines, "direct stopped worker", lambda: body(context))
    file.close()
    chunks = _Chunks()
    attempt = await bodies.AsyncStreamBody(chunks, ownership="owned")(context)
    payload = b"".join([chunk async for chunk in attempt.aiter_bytes()])
    await attempt.aclose()
    await attempt.aclose()
    lines.append(f"  direct async owned stream payload={payload!r} begins={chunks.begins} closes={chunks.closes}")


def _file_metadata_errors(
    package: ModuleType, api: Any, bodies: ModuleType, options: ModuleType, replies: _Replies, lines: list[str]
) -> None:
    errors = importlib.import_module(f"{package.__name__}.errors")
    for entry in (False, True):
        primary = errors.PhaseTimeoutError(
            phase="read", effective_timeout=1, delivery_state=errors.DeliveryState.NOT_SENT
        )
        file = _SDKFile(primary, armed=entry)
        replies.reset(200)
        try:
            api.request_raw(
                "PUT",
                "https://body.example.com/",
                body=bodies.FileBody(file, ownership="owned"),
                options=options.RequestOptions(hooks=(file,)),
            )
        except Exception as error:  # noqa: BLE001
            lines.append(
                f"  SDK file {'capture' if entry else 'rewind'} {type(error).__name__} cause={error.cause is primary}"
                f" failed_calls={file.failed_calls} closes={file.closes} sends={len(replies.requests)}"
            )
    primary = errors.TransportError(phase="read", delivery_state=errors.DeliveryState.MAYBE_SENT)
    file = _SDKFile(primary, armed=True)
    context = bodies.BodyAttemptContext(call_id="direct", attempt_index=2, hop_index=1, remaining_timeout=None)
    try:
        bodies.FileBody(file)(context)
    except Exception as error:  # noqa: BLE001
        lines.append(
            f"  direct SDK file metadata {type(error).__name__} cause={error.cause is primary}"
            f" context={(error.attempt_index, error.hop_index)} failed_calls={file.failed_calls}"
        )
    file.close()


async def _async_file_metadata_errors(
    package: ModuleType, api: Any, bodies: ModuleType, options: ModuleType, replies: _Replies, lines: list[str]
) -> None:
    errors = importlib.import_module(f"{package.__name__}.errors")
    for entry in (False, True):
        primary = errors.PhaseTimeoutError(
            phase="read", effective_timeout=1, delivery_state=errors.DeliveryState.NOT_SENT
        )
        file = _SDKFile(primary, armed=entry)
        body = bodies.AsyncFileBody(file, ownership="owned")
        replies.reset(200)
        try:
            await api.request_raw(
                "PUT",
                "https://body.example.com/",
                body=body,
                options=options.RequestOptions(hooks=(file,)),
            )
        except Exception as error:  # noqa: BLE001
            lines.append(
                f"  async SDK file {'capture' if entry else 'rewind'} {type(error).__name__}"
                f" cause={error.cause is primary}"
                f" failed_calls={file.failed_calls} closes={file.closes} sends={len(replies.requests)}"
            )
        await body.aclose()


def _nested_file_factory(api: Any, bodies: ModuleType, replies: _Replies, lines: list[str]) -> None:
    for failed_close in (False, True):
        secondary = OSError("nested close failed") if failed_close else None
        file = _ClosingFile(secondary, broken=True)
        inner = bodies.FileBody(file, ownership="owned")
        outer = bodies.BodyFactory(inner)
        replies.reset(200)
        for repeated in (False, True):
            try:
                api.request_raw("PUT", "https://body.example.com/", body=outer)
            except Exception as error:  # noqa: BLE001, PERF203
                lines.append(
                    f"  nested owned file failed_close={failed_close} repeated={repeated} {type(error).__name__}"
                    f" inner={type(error.cause).__name__} closed={file.closed} closes={file.closes}"
                    f" secondary={getattr(error.cause, 'secondary_errors', ()) == (secondary,)}"
                    f" sends={len(replies.requests)}"
                )
