from __future__ import annotations
from typing_extensions import NotRequired
from typing import TypedDict




ShipTo = TypedDict('ShipTo', {
    'postal-code': NotRequired[str],})





ChildRequest = TypedDict('ChildRequest', {
    'ship-to': NotRequired[ShipTo],
    'note': NotRequired[str],})





ChildResponse = TypedDict('ChildResponse', {
    'ship-to': NotRequired[ShipTo],
    'note': NotRequired[str],
    'id': NotRequired[int],})





RenamedParentRequest = TypedDict('RenamedParentRequest', {
    'ship-to': NotRequired[ShipTo],
    'note': NotRequired[str],})





RenamedParentResponse = TypedDict('RenamedParentResponse', {
    'ship-to': NotRequired[ShipTo],
    'note': NotRequired[str],
    'id': NotRequired[int],})
