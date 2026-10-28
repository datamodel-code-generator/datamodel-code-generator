# -*- coding: latin-1 -*-
# Café

"""The pets resource."""

from ._async import (
    AsyncPetsResource,
    AsyncPetsWithRawResponse,
    AsyncPetsWithResponse,
    AsyncPetsWithStreamingResponse,
)
from ._sync import (
    PetsResource,
    PetsWithRawResponse,
    PetsWithResponse,
    PetsWithStreamingResponse,
)

__all__ = [
    'AsyncPetsResource',
    'AsyncPetsWithRawResponse',
    'AsyncPetsWithResponse',
    'AsyncPetsWithStreamingResponse',
    'PetsResource',
    'PetsWithRawResponse',
    'PetsWithResponse',
    'PetsWithStreamingResponse',
]
