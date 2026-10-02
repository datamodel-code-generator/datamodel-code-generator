"""Reject undeclared groups, other origins, wrong store records, and unawaited asyncio resets."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.protocols import CircuitBreakerOptions, CircuitKey, MemoryCircuitStore, Origin

ORIGIN = Origin(scheme="https", host="api.example.com", port=443)


async def wrong_resets(client: Client, async_client: AsyncClient, store: MemoryCircuitStore, key: CircuitKey) -> None:
    """Reject each misuse of a reset, the options, or a store."""
    client.reset_circuit("payments", origin=ORIGIN)  # error
    client.reset_circuit("backend", origin="https://api.example.com")  # error
    client.reset_circuit(group="backend")  # error
    client.reset_circuit("backend", origin=ORIGIN, partition="tenant")  # error
    CircuitBreakerOptions(failure_threshold="5")  # error
    store.admit(key, now=0.0, options=CircuitBreakerOptions())  # error
    store.record(store.admit(key, now=0.0, options=CircuitBreakerOptions().resolved()), "opened", now=0.0)  # error
    CircuitKey(origin=ORIGIN, credential_partition="anonymous")  # error
    unawaited: None = async_client.reset_circuit("backend", origin=ORIGIN)  # error
    del unawaited
