from __future__ import annotations
from typing_extensions import NotRequired
from typing import TypeAlias, TypedDict




Envelope1 = TypedDict('Envelope1', {
    'ship-to': NotRequired[str],
    'other-key': NotRequired[int],})





Envelope2 = TypedDict('Envelope2', {
    'other-key': NotRequired[int],
    'ship-to': NotRequired[str],})





Variants1 = TypedDict('Variants1', {
    'ship-to': NotRequired[str],
    'other-key': NotRequired[int],})




Variants: TypeAlias = Variants1



class Envelope(TypedDict):
    nested: NotRequired[Variants]