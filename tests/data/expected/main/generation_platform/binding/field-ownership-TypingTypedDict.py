from __future__ import annotations
from typing_extensions import NotRequired
from typing import TypedDict




A = TypedDict('A', {
    'root': NotRequired[str],
    'shared-key': NotRequired[str],})





B = TypedDict('B', {
    'root': NotRequired[str],
    'shared-key': NotRequired[str],
    'left': NotRequired[int],})





C = TypedDict('C', {
    'root': NotRequired[str],
    'shared-key': NotRequired[str],
    'right': NotRequired[bool],})





Leaf = TypedDict('Leaf', {
    'root': NotRequired[str],
    'shared-key': NotRequired[str],
    'left': NotRequired[int],
    'right': NotRequired[bool],
    'leaf-key': NotRequired[float],})
