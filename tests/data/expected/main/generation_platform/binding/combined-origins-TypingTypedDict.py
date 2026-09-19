from __future__ import annotations
from typing_extensions import NotRequired
from typing import Any, TypeAlias, TypedDict, Union




ShipTo = TypedDict('ShipTo', {
    'postal-code': NotRequired[str],})





Choice1 = TypedDict('Choice1', {
    'common-key': NotRequired[str],
    'ship-to': NotRequired[ShipTo],})





Choice2 = TypedDict('Choice2', {
    'common-key': NotRequired[str],})




Never: TypeAlias = Any




Named = TypedDict('Named', {
    'external-name': NotRequired[str],})




Choice: TypeAlias = Union[Choice1, Choice2, Named]