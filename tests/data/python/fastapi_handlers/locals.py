"""Services of the server whose tags, operations, and parameters take the names the generated routers use."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def services(server: ModuleType, _models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return one recording service for each group."""

    def record(name: str, arguments: dict[str, object]) -> None:
        calls.append(f"{name}({', '.join(f'{key}={value!r}' for key, value in arguments.items())})")

    class Wiring(server.services.WiringService):
        def router(self, **arguments: object) -> None:
            record("router", arguments)

    class Router(server.services.RouterService):
        def wiring(self, **arguments: object) -> None:
            record("wiring", arguments)

    class Security(server.services.SecurityService):
        def authenticate(self, **arguments: object) -> None:
            record("authenticate", arguments)

    return {"default": {"wiring": Wiring(), "router": Router(), "security": Security()}}


def settings(_server: ModuleType, _models: ModuleType, calls: list[str]) -> dict[str, dict[str, object]]:
    """Return an authorize callback that records the schemes it sees."""

    def authorize(_requirement_sets: object, credentials: dict[str, object]) -> str:
        calls.append(f"authorize {sorted(credentials)}")
        return "user"

    return {"default": {"authorize": authorize}}
