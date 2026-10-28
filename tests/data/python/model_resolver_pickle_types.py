"""External ModelResolver types shared by cross-version pickle fixtures."""

from __future__ import annotations

from datamodel_code_generator.reference import ModelResolver


class SlottedModelResolver(ModelResolver):
    """ModelResolver subclass with state outside the inherited instance dictionary."""

    __slots__ = ("slot_marker",)


class CustomStateModelResolver(ModelResolver):
    """ModelResolver subclass that owns a nonstandard pickle state."""

    def __getstate__(self) -> str:
        return "custom-state"

    def __setstate__(self, state: str) -> None:
        self.restored_state = state


class CustomDictStateModelResolver(ModelResolver):
    """ModelResolver subclass that owns a dictionary without base instance state."""

    def __getstate__(self) -> dict[str, str]:
        return {"custom_state": "custom-dict-state"}

    def __setstate__(self, state: dict[str, str]) -> None:
        self.restored_state = state["custom_state"]


class GlobalReduceModelResolver(ModelResolver):
    """ModelResolver subclass serialized through a module global name."""

    def __reduce__(self) -> str:
        return "GLOBAL_REDUCE_MODEL_RESOLVER"


GLOBAL_REDUCE_MODEL_RESOLVER = GlobalReduceModelResolver()


class TupleReduceModelResolver(ModelResolver):
    """ModelResolver subclass rebuilt from a valid two-item reduce tuple."""

    def __reduce__(self) -> tuple[type[TupleReduceModelResolver], tuple[()]]:
        return TupleReduceModelResolver, ()
