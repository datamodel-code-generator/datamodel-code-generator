"""What the model codecs build on: the value built once per object and kept by its identity, and the shared members.

The codec backends keep their native readers and serializers by identity, and so does the client core its per-call
inputs and server URLs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Generic

from typing_extensions import TypeVar

from .media import plain

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from typing_extensions import TypeForm

K = TypeVar("K")
V = TypeVar("V")
T = TypeVar("T")


def identity_cached(cache: dict[int, tuple[K, V]], key: K, build: Callable[[K], V]) -> V:
    """Return the value a cache keeps for an object, building it from the object on first use.

    The cache is keyed by the object's identity and keeps the object beside its value, so the identity stays the
    object's while the entry lives. A failed build keeps nothing; a concurrent first use builds an equal value.
    """
    if (cached := cache.get(id(key))) is None:
        cache[id(key)] = cached = (key, build(key))
    return cached[1]


class ModelCodec(Generic[T]):
    """Read header, form, and part text through the codec's own conversion, alike in every backend.

    A backend that validates what it converts leaves text that is no JSON literal to its validation; the others refuse
    such text before converting.
    """

    __slots__ = ()
    _validates: ClassVar[bool] = False

    def convert(self, value: object) -> T:
        """Construct from a parsed parameter or non-JSON body, as the backend builds its models."""
        raise NotImplementedError

    def text(self, value: object) -> T:
        """Construct from header, form, or part text; text that is no JSON literal fails unless a backend validates."""
        return self.convert(plain(value, strict=not self._validates))


class ValidatingCodec(ModelCodec[T]):
    """The codec of a backend that validates what it converts, and so builds a model from its field arguments too."""

    __slots__ = ("type",)
    _validates: ClassVar[bool] = True

    def __init__(self, type_: TypeForm[Any]) -> None:
        """Keep the generated type; the backend builds its native reader on first use and shares it by identity."""
        self.type = type_

    def assemble(self, fields: Mapping[str, object]) -> T:
        """Construct the native model from field arguments addressed by their wire names, and its own defaults."""
        return self.convert(dict(fields))
