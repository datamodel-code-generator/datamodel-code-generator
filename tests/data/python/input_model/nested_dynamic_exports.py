"""Dynamic short exports remain valid when their descriptive owners change."""

from __future__ import annotations

import sys
import types
from typing import Any, NoReturn

from pydantic import BaseModel

calls = []
exports = {}


class DeletedOwner:
    """Exercise a real nested export binding."""

    class Deleted(BaseModel):
        """Exercise a real nested export binding."""

        value: int


exports["Deleted"] = DeletedOwner.Deleted


class DeletedRoot(BaseModel):
    """Exercise a real nested export binding."""

    child: DeletedOwner.Deleted
    model_config = {"json_schema_extra": {"x-caller-root": "kept"}}


globals().pop("DeletedOwner")


class ScalarOwner:
    """Exercise a real nested export binding."""

    class Scalar(BaseModel):
        """Exercise a real nested export binding."""

        value: int


exports["Scalar"] = ScalarOwner.Scalar


class ScalarRoot(BaseModel):
    """Exercise a real nested export binding."""

    child: ScalarOwner.Scalar


class Replacement:
    """Exercise a real nested export binding."""

    @property
    def __class__(self) -> type:
        """Record unexpected instance type inspection."""
        calls.append("replacement.__class__")
        return type(self)

    def __getattr__(self, name: str) -> NoReturn:
        """Record unexpected owner attribute resolution."""
        calls.append(f"replacement.{name}")
        raise AttributeError(name)


ScalarOwner = Replacement()


class ChangedOwner:
    """Exercise a real nested export binding."""

    class Changed(BaseModel):
        """Exercise a real nested export binding."""

        value: int


exports["Changed"] = ChangedOwner.Changed


class ChangedRoot(BaseModel):
    """Exercise a real nested export binding."""

    child: ChangedOwner.Changed


class Other(BaseModel):
    """Exercise a real nested export binding."""

    other: str


ChangedOwner.Changed = Other


class DescriptorOwner:
    """Exercise a real nested export binding."""

    class Descriptor(BaseModel):
        """Exercise a real nested export binding."""

        value: int


exports["Descriptor"] = DescriptorOwner.Descriptor


class DescriptorRoot(BaseModel):
    """Exercise a real nested export binding."""

    child: DescriptorOwner.Descriptor


class OwnerDescriptor:
    """Exercise a real nested export binding."""

    def __get__(self, instance: object, owner: type | None) -> type[Other]:
        """Record unexpected descriptor evaluation."""
        calls.append("descriptor.__get__")
        return Other


DescriptorOwner.Descriptor = OwnerDescriptor()


class ObservedMeta(type):
    """Exercise a real nested export binding."""

    def __getattribute__(cls, name: str) -> Any:
        """Observe namespace access through the metaclass hook."""
        if name == "__dict__":
            calls.append("owner.__dict__")
        return super().__getattribute__(name)


class LiveOwner(metaclass=ObservedMeta):
    """Exercise a real nested export binding."""

    class Live(BaseModel):
        """Exercise a real nested export binding."""

        value: int


exports["Live"] = LiveOwner.Live


class LiveRoot(BaseModel):
    """Exercise a real nested export binding."""

    child: LiveOwner.Live


class LazyOwner:
    """Exercise a real nested export binding."""

    class Lazy(BaseModel):
        """Exercise a real nested export binding."""

        value: int


LazyOwner.Lazy.__module__ = "tests.data.python.input_model.nested_lazy_exports"
exports["Lazy"] = LazyOwner.Lazy


class LazyRoot(BaseModel):
    """Exercise a real nested export binding."""

    child: LazyOwner.Lazy


def __getattr__(name: str) -> type[BaseModel]:
    """Expose stale owner types through their supported short names."""
    calls.append(name)
    if name in exports and name != "Live":
        return exports[name]
    raise AttributeError(name)


class RedirectMeta(type):
    """Redirect a nested leaf while retaining its original static dictionary."""

    def __getattribute__(cls, name: str) -> Any:
        if name == "Redirected":
            return Other
        return super().__getattribute__(name)


class RedirectOwner(metaclass=RedirectMeta):
    class Redirected(BaseModel):
        value: int


exports["Redirected"] = RedirectOwner.__dict__["Redirected"]


class RedirectedRoot(BaseModel):
    child: exports["Redirected"]


class RaisingMeta(type):
    """Reject qualified lookup while the original short export remains usable."""

    def __getattribute__(cls, name: str) -> Any:
        if name == "Raising":
            raise RuntimeError("qualified lookup is unavailable")
        return super().__getattribute__(name)


class RaisingOwner(metaclass=RaisingMeta):
    class Raising(BaseModel):
        value: int


exports["Raising"] = RaisingOwner.__dict__["Raising"]


class RaisingRoot(BaseModel):
    child: exports["Raising"]


class ChainMeta(type):
    """Redirect an intermediate owner rather than the leaf itself."""

    def __getattribute__(cls, name: str) -> Any:
        if name == "Middle":
            return Other
        return super().__getattribute__(name)


class ChainOwner(metaclass=ChainMeta):
    class Middle:
        class Chained(BaseModel):
            value: int


exports["Chained"] = ChainOwner.__dict__["Middle"].Chained


class ChainedRoot(BaseModel):
    child: exports["Chained"]


class ModuleOwner:
    class ModuleRedirected(BaseModel):
        value: int


exports["ModuleRedirected"] = ModuleOwner.ModuleRedirected


class ModuleRedirectedRoot(BaseModel):
    child: ModuleOwner.ModuleRedirected


class RedirectModule(types.ModuleType):
    """Redirect a qualified owner through a real module attribute hook."""

    def __getattribute__(self, name: str) -> Any:
        if name == "ModuleOwner":
            return Other
        return super().__getattribute__(name)


sys.modules[__name__].__class__ = RedirectModule


class ProxyOwner:
    class Proxy(BaseModel):
        value: int


ProxyOwner.Proxy.__module__ = "tests.data.python.input_model.nested_dynamic_proxy"
exports["Proxy"] = ProxyOwner.Proxy


class ProxyRoot(BaseModel):
    child: ProxyOwner.Proxy


class ModuleProxy(types.SimpleNamespace):
    @property
    def __class__(self) -> type:
        calls.append("proxy.__class__")
        return type(self)


sys.modules[ProxyOwner.Proxy.__module__] = ModuleProxy(
    __name__=ProxyOwner.Proxy.__module__, __spec__=None, Proxy=ProxyOwner.Proxy, ProxyOwner=ProxyOwner
)
