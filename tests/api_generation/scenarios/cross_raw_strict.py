"""Send and receive whole text bodies of binary strings as raw bytes when the models hold them as strict bytes.

`--strict-types bytes` types a binary string `StrictBytes` in Pydantic models, which a wrapped body's schema stands
for as it does for `bytes`. Those models keep the length limit of a binary string beside the strict type, so the
server and the clients whose models keep it check the length of a stamp's bytes as they do without strict types.
"""

from __future__ import annotations

from datamodel_code_generator.enums import StrictTypes
from tests.api_generation.scenarios.cross_raw import BACKENDS, CLIENT, DOCUMENT, calls, services

MODEL = {"strict_types": [StrictTypes.bytes]}

__all__ = ("BACKENDS", "CLIENT", "DOCUMENT", "MODEL", "calls", "services")
