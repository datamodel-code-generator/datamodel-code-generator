from __future__ import annotations
from typing import TypedDict




A = TypedDict('A', {
    'foo-bar': str,})




class B(TypedDict):
    foo_bar: int




Leaf = TypedDict('Leaf', {
    'foo-bar': str,
    'foo_bar': int,
    'leaf-key': bool,})
