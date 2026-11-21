"""#7026 private-reexport: which imports of a private name only a suppression of unused imports keeps.

Every comment form that covers the name's line or the first line of its statement counts; an import that its own scope
reads is no re-export, and a parameter that shadows the name does not read it.

codes: F401 unused-import
"""
from .home import _bare  # noqa
from .home import _coded  # noqa: F401
from .home import _other  # noqa: E501
# ruff: ignore[F401]
from .home import _above
from .home import (  # noqa: F401
    _first,
    _second,
)
from .home import _named  # ruff: ignore[unused-import]
from .home import _read, _shadowed  # noqa: F401
import _plain.module as _alias  # noqa: F401
from .home import __dunder__  # noqa: F401
from .home import public  # noqa: F401


def reader():
    return _read


def shadow(_shadowed):
    return _shadowed


def local():
    from .home import _inside  # noqa: F401
    from .home import _used  # noqa: F401

    return _used
