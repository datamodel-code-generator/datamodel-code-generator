from __future__ import annotations
from typing_extensions import NotRequired, ReadOnly
from typing import TypedDict




Payload = TypedDict('Payload', {
    '': str,
    'ship-to': ReadOnly[str],
    'quote\'': NotRequired[str],
    'back\\slash': NotRequired[str],
    'class': NotRequired[str],
    'naïve': NotRequired[str],})
