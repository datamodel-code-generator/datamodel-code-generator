from __future__ import annotations
from enum import Enum
from pydantic import RootModel


class NonFinite(Enum):
    number_inf = float('inf')
    number__inf = float('-inf')
    number_nan = float('nan')


class Value(RootModel[NonFinite]):
    root: NonFinite
