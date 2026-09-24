from __future__ import annotations
from typing_extensions import NotRequired
from typing import List, TypedDict




BaseRequest = TypedDict('BaseRequest', {
    'ship-to': NotRequired[str],})





Base = TypedDict('Base', {
    'id': NotRequired[int],
    'ship-to': NotRequired[str],})





ItemRequest = TypedDict('ItemRequest', {
    'ship-to': str,
    'secret': NotRequired[str],
    'values': NotRequired[List[str]],})





ItemResponse = TypedDict('ItemResponse', {
    'id': NotRequired[int],
    'ship-to': str,
    'values': NotRequired[List[str]],})





Item = TypedDict('Item', {
    'id': NotRequired[int],
    'ship-to': str,
    'secret': NotRequired[str],
    'values': NotRequired[List[str]],})
