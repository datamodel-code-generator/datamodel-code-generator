"""Validate the arguments of a call with Pydantic, for packages generated with Pydantic argument validation.

Each operation branch has a private function whose parameters take the final types of its arguments, which Pydantic's
validate_call checks under one configuration: strict unless generated otherwise, revalidating existing instances of
types whose configuration it owns, and keeping extra keys. Pydantic models and dataclasses keep their own
configuration, so they may trust their existing instances.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any, Final

from ..model_codecs.unset import Unset
from ..model_codecs.values import ModelInput, ModelValue
from .errors import RequestEncodingError

if TYPE_CHECKING:
    from collections.abc import Callable


OMITTED: Final[Any] = object()


class ArgumentCheck:
    """The Pydantic validation of one operation branch's arguments, built when a call first selects it."""

    __slots__ = ("_function", "_invalid", "_locations", "names")

    def __init__(
        self,
        arguments: tuple[tuple[str, tuple[str, ...]], ...],
        function: Callable[..., tuple[object, ...]],
        *,
        strict: bool,
    ) -> None:
        """Wrap the branch's function, which takes each named argument and returns them all in order.

        Each argument has the location its failures are reported at, and the function's parameters default to
        OMITTED, so only supplied arguments are validated. A closed TypedDict still refuses extra keys, for which
        Pydantic warns that it ignores the configuration.
        """
        from pydantic import ConfigDict, ValidationError, validate_call  # noqa: PLC0415
        from pydantic.warnings import TypedDictExtraConfigWarning  # noqa: PLC0415

        self.names = tuple(name for name, _ in arguments)
        self._locations = dict(arguments)
        self._invalid = ValidationError
        config = ConfigDict(strict=strict, revalidate_instances="always", extra="allow")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", TypedDictExtraConfigWarning)
            self._function = validate_call(config=config)(function)

    def __call__(self, values: tuple[object, ...], operation_id: str | None) -> tuple[object, ...]:
        """Return the values with each supplied native one validated; omitted values and snapshots stay as they are.

        A value Pydantic refuses raises RequestEncodingError at its argument, with the ValidationError as its cause.
        """
        supplied = {
            name: value
            for name, value in zip(self.names, values, strict=True)
            if not isinstance(value, (Unset, ModelValue, ModelInput))
        }
        if not supplied:
            return values
        try:
            checked = self._function(**supplied)
        except self._invalid as error:
            name, *path = error.errors()[0]["loc"]
            location = (*self._locations[str(name)], *path)
            raise RequestEncodingError(location=location, operation_id=operation_id, cause=error) from None
        return tuple(
            new if name in supplied else value for name, value, new in zip(self.names, values, checked, strict=True)
        )
