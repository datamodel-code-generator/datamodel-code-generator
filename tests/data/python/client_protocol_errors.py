"""Report the public errors of a generated package with helpers: their hierarchy, fields, and safe messages."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from types import ModuleType

_SECRET: Final = "private-protocol-marker"


def protocol_errors(package: ModuleType, lines: list[str]) -> None:
    """Inspect every exported error class, then the helper errors' fields and messages."""
    errors, protocols, responses = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("errors", "protocols", "responses")
    )
    for name in errors.__all__:
        value = getattr(errors, name)
        if isinstance(value, type) and issubclass(value, BaseException):
            chain = [item.__name__ for item in value.__mro__ if issubclass(item, errors.SDKError)]
            lines.append(f"  {name}: {' < '.join(chain)}")
        else:
            lines.append(f"  {name}: {value}")
    secret = _SECRET
    operation = protocols.OperationRef(pointer=f"/paths/{secret}/get", document=f"https://example.com/{secret}")
    info = responses.ResponseInfo(
        status_code=200,
        headers=responses.HeadersView((("x-private", secret),)),
        call_id="call-1",
        elapsed=0.25,
        content_type="application/json",
    )
    cause = RuntimeError(secret)
    context = {"helper_id": secret, "operation": operation, "operation_id": "protocol", "info": info, "cause": cause}
    for error in (
        errors.ProtocolDataError(
            reason="operation_failed", location=protocols.BodySelector(pointer=f"/{secret}"), data={secret: 1}, **context
        ),
        errors.SessionLimitError(reason="pages", limit=3, progress={"pages": 3, "items": 30}, **context),
        errors.StreamInterruptedError(reason="transport", sequence=7, **context),
        errors.WebSocketClosedError(code=4001, reason=secret, clean=False, **context),
    ):
        fields = {
            name: value
            for name, value in vars(error).items()
            if name not in context and name not in {"attempt_count", "elapsed"}
        }
        lines.extend((
            f"  {type(error).__name__} {error} secret={secret in str(error) or secret in repr(error)}",
            f"    fields={fields!r}",
            f"    context={all(getattr(error, name) is value for name, value in context.items())} "
            f"measurements={(error.attempt_count, error.elapsed, error.request_id)}",
        ))
    progress = {"pages": 1}
    limited = errors.SessionLimitError(reason="items", limit=0, progress=progress)
    progress["pages"] = 9
    lines.append(f"  progress copy={dict(limited.progress)} type={type(limited.progress).__name__}")
    lines.append(f"  defaults={errors.ProtocolDataError()} {errors.ProtocolDataError().reason}")
