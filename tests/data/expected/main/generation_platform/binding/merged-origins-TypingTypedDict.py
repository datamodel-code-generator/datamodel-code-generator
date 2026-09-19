from __future__ import annotations
from typing_extensions import NotRequired
from typing import TypeAlias, TypedDict




SideKey = TypedDict('SideKey', {
    'nested-key': NotRequired[str],})





RefUse1 = TypedDict('RefUse1', {
    'shared-key': NotRequired[str],
    'side-key': NotRequired[SideKey],})




RefUse: TypeAlias = RefUse1




Combined = TypedDict('Combined', {
    'shared-key': NotRequired[str],})





Base = TypedDict('Base', {
    'shared-key': NotRequired[str],})





Other = TypedDict('Other', {
    'shared-key': NotRequired[str],})
