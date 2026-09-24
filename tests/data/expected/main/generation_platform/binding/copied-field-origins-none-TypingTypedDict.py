from __future__ import annotations
from typing_extensions import NotRequired
from typing import TypedDict




BaseRequest = TypedDict('BaseRequest', {
    'raw-note': NotRequired[str],
    'secret': NotRequired[str],})





BaseResponse = TypedDict('BaseResponse', {
    'raw-note': NotRequired[str],
    'id': NotRequired[int],})





Base = TypedDict('Base', {
    'raw-note': NotRequired[str],
    'id': NotRequired[int],
    'secret': NotRequired[str],})





ChildRequest = TypedDict('ChildRequest', {
    'raw-note': NotRequired[str],
    'secret': NotRequired[str],})





ChildResponse = TypedDict('ChildResponse', {
    'raw-note': NotRequired[str],
    'id': NotRequired[int],})





Child = TypedDict('Child', {
    'raw-note': NotRequired[str],
    'id': NotRequired[int],
    'secret': NotRequired[str],})





GrandChildRequest = TypedDict('GrandChildRequest', {
    'raw-note': NotRequired[str],
    'secret': NotRequired[str],})





GrandChildResponse = TypedDict('GrandChildResponse', {
    'raw-note': NotRequired[str],
    'id': NotRequired[int],})





GrandChild = TypedDict('GrandChild', {
    'raw-note': NotRequired[str],
    'id': NotRequired[int],
    'secret': NotRequired[str],})
