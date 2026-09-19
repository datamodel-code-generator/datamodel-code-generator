from __future__ import annotations
from typing_extensions import ReadOnly, Required, TypedDict




Payload = TypedDict('Payload', {
    '': Required[str],
    'ship-to': Required[ReadOnly[str]],
    'quote\'': str,
    'back\\slash': str,
    'class': str,
    'naïve': str,}, total=False)
