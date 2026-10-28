"""Third collision family with reserved suffix gaps."""
from enum import Enum

from pydantic import BaseModel

from tests.data.python.input_model.collision_b import Data_2
from tests.data.python.input_model.collision_reserved import Data_4, Kind_2, Kind_4, Root_2, Root_4


class Data(BaseModel):
    score: int





class Kind(Enum):
    VALUE = "c"
    OTHER = "other-c"




class Mode(Enum):
    VALUE = "mode-c"
    OTHER = "other-mode-c"


class PlainC(BaseModel):
    data: Data
    kind: Kind | None = None
    mode: Mode | None = None
    reserved_data2: Data_2 | None = None
    reserved_data4: Data_4 | None = None
    reserved_kind2: Kind_2 | None = None
    reserved_kind4: Kind_4 | None = None


class Root(BaseModel):
    data: Data
    reserved_data2: Data_2 | None = None
    reserved_data4: Data_4 | None = None
    reserved_root2: Root_2 | None = None
    reserved_root4: Root_4 | None = None
