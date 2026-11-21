"""A6 conditional-bindings table, every row: version, try/except ValueError and AttributeError, except ImportError:
raise, a global builder, TYPE_CHECKING else, not TYPE_CHECKING members, a loader and a platform branch.

questions: bindings
"""

import sys
from typing import TYPE_CHECKING

if sys.version_info >= (3, 14):
    from typing import TypeAliasType
else:
    from typing_extensions import TypeAliasType

try:
    DEFAULT_TARGET = int(f"{sys.version_info.major}{sys.version_info.minor}")
except ValueError:
    DEFAULT_TARGET = 314

try:
    import graphql
except ImportError:
    raise

try:
    resolver_kind = graphql.TypeResolvers().kind
except AttributeError:
    resolver_kind = graphql.TypeFields.kind

if TYPE_CHECKING:
    from collections.abc import Callable
else:
    Callable = object


class _BaseModel:
    if not TYPE_CHECKING:
        def __getattr__(self, name):
            return name


def _define_configs():
    global JsonConfig

    class JsonConfig:
        strict = True


def get_safe_loader():
    try:
        from yaml import CSafeLoader as Loader
    except ImportError:
        from yaml import SafeLoader as Loader
    return Loader


if sys.platform == "win32":
    WATCH_BACKEND = "polling"
else:
    WATCH_BACKEND = "native"
