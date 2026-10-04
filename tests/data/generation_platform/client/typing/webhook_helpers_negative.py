"""Reject other key types, positional or missing clocks, and other events and inputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pets.protocols import KeySet
from pets.webhooks import keys as key_types
from pets.webhooks.keys import HmacKey
from pets.webhooks.standard import message
from pets_models import Push

if TYPE_CHECKING:
    from datetime import datetime


async def wrong_calls(body: bytes, now: datetime, keys: KeySet[HmacKey]) -> None:
    """Reject each misuse of a helper."""
    message.verify(body, [], KeySet(keys=("key",)), now=now)  # error
    message.verify(body, [], keys, now)  # error
    message.verify(body, [], keys)  # error
    message.verify(body, [], keys, now=now, verifier=object())  # error
    message.verify(body, {"name": "value"}, keys, now=now)  # error
    message.verify("body", [], keys, now=now)  # error
    await message.verify(body, [], keys, now=now)  # error
    data: Push = message.verify(body, [], keys, now=now).data  # error
    public_key: type[object] = key_types.Ed25519Key  # error
    del data, public_key
