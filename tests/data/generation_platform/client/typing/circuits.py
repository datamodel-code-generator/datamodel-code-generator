"""Keep circuit breaker options, records, stores, errors, and resets typed through the public contracts."""

from __future__ import annotations

from typing import Literal

from pets import AsyncClient, Client
from pets.errors import CircuitOpenError, CircuitStoreError, ProtocolStoreError
from pets.options import ClientOptions, ProtocolClientOptions
from pets.protocols import (
    AsyncCircuitStore,
    AsyncMemoryCircuitStore,
    CircuitBreakerOptions,
    CircuitKey,
    CircuitPermit,
    CircuitSnapshot,
    CircuitStore,
    MemoryCircuitStore,
    Origin,
    ProtocolSecurityContext,
    ResolvedCircuitBreakerOptions,
)
from typing_extensions import assert_type

ORIGIN = Origin(scheme="https", host="api.example.com", port=443)


def settings(store: CircuitStore | AsyncCircuitStore | None) -> ClientOptions:
    """Enable the breaker with a store, or the client's own memory store with None."""
    breaker = CircuitBreakerOptions(enabled=True, failure_threshold=3, cooldown=10.0)
    security = ProtocolSecurityContext(credential_partition="tenant")
    return ClientOptions(protocols=ProtocolClientOptions(circuit=breaker, circuit_store=store, security=security))


def records(store: MemoryCircuitStore) -> None:
    """Keep the store methods' records and the snapshot's closed state type."""
    key = CircuitKey(origin=ORIGIN, credential_partition="anonymous", group="backend")
    resolved = CircuitBreakerOptions().resolved()
    assert_type(resolved, ResolvedCircuitBreakerOptions)
    assert_type(resolved.cooldown, float)
    permit = store.admit(key, now=0.0, options=resolved)
    assert_type(permit, CircuitPermit)
    assert_type(permit.probe, bool)
    store.record(permit, "failure", now=1.0)
    snapshot = store.snapshot(key)
    assert_type(snapshot, CircuitSnapshot)
    assert_type(snapshot.state, Literal["closed", "open", "half_open"])
    assert_type(snapshot.retry_at, float | None)
    store.reset(key)
    stores: tuple[CircuitStore, AsyncCircuitStore] = (store, AsyncMemoryCircuitStore())
    del stores


async def async_records(store: AsyncMemoryCircuitStore, key: CircuitKey) -> None:
    """Await the asynchronous store's methods."""
    permit = await store.admit(key, now=0.0, options=CircuitBreakerOptions().resolved())
    await store.record(permit, "neutral", now=0.0)
    assert_type(await store.snapshot(key), CircuitSnapshot)


def reset(client: Client) -> None:
    """Reset a declared group's circuit at an origin."""
    client.reset_circuit("backend", origin=ORIGIN)


async def async_reset(client: AsyncClient) -> None:
    """Await an asyncio client's reset."""
    await client.reset_circuit("search", origin=ORIGIN)


def failures(error: CircuitOpenError, store_error: CircuitStoreError) -> None:
    """Keep the open circuit's key and retry time, and the store error's base."""
    assert_type(error.key, CircuitKey)
    assert_type(error.retry_at, float)
    stored: ProtocolStoreError = store_error
    del stored
