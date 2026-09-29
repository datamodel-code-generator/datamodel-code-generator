"""How a logical call accounts for the token acquisitions of the SDK's own providers, and how it recognizes them."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .auth import BearerCredential, CredentialContext, TokenVersion
    from .errors import AuthBudgetKind


class RefreshRecord(Protocol):
    """An acquisition a call started or waits for: its refresh id, and whether it is queued or its work still runs."""

    refresh_id: str
    outstanding: bool


class CallAdmission(Protocol):
    """The logical call an acquisition runs for: its token exchange and network budgets, its counters, its hooks."""

    def shortfall(self) -> tuple[AuthBudgetKind, int, int] | None:
        """Return the budget, limit, and used slots that leave no room for a new acquisition, or None."""

    def charge(self) -> None:
        """Consume the token exchange and the network send of an acquisition admitted for the call."""

    def joined(self, refresh: RefreshRecord) -> None:
        """Record an acquisition the call started or waits for."""

    def exchanged(self) -> None:
        """Count the token request that an acquisition charged to the call sent."""

    def waiting(self) -> None:
        """Report that the call waits for an acquisition another caller started."""

    async def awaiting(self) -> None:
        """Report that the asyncio call waits for an acquisition another caller started."""


class TokenAcquirer(ABC):
    """A synchronous provider of the SDK whose shared acquisitions the calls using it account for."""

    __slots__ = ()

    @abstractmethod
    def acquire_for(self, context: CredentialContext, admission: CallAdmission) -> BearerCredential:
        """Return usable material, charging a new acquisition to the call that needs it."""

    @abstractmethod
    def exchange_needed(self, version: TokenVersion) -> bool:
        """Return whether replacing a rejected version needs a new acquisition rather than a running or newer one."""


class AsyncTokenAcquirer(ABC):
    """An asyncio provider of the SDK whose shared acquisitions the calls using it account for."""

    __slots__ = ()

    @abstractmethod
    async def acquire_for(self, context: CredentialContext, admission: CallAdmission) -> BearerCredential:
        """Return usable material, charging a new acquisition to the call that needs it."""

    @abstractmethod
    def exchange_needed(self, version: TokenVersion) -> bool:
        """Return whether replacing a rejected version needs a new acquisition rather than a running or newer one."""
