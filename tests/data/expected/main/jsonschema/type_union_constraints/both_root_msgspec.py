# Type-specific union constraints

from __future__ import annotations

from typing import Annotated, TypeAlias

from msgspec import Meta

RootInteger: TypeAlias = Annotated[int, Meta(ge=2)]


RootString: TypeAlias = Annotated[str, Meta(max_length=2)]


Root: TypeAlias = Annotated[RootInteger | RootString, Meta(title='Root')]
