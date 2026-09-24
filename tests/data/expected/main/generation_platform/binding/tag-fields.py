from __future__ import annotations
from typing import TypeAlias, Union
from msgspec import Struct



class StartedEvent(Struct, tag_field='type', tag='started'):
    pass



class StoppedEvent(Struct, tag_field='type', tag='stopped'):
    pass



Event: TypeAlias = Union[StartedEvent, StoppedEvent]