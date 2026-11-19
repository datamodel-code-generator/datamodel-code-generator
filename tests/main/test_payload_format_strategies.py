"""Keep the payload format strategies complete over the string formats the generator types as other than str."""

from __future__ import annotations

import pytest

from tests.main.payload_validation.models import PayloadBackend
from tests.main.payload_validation.strategy import PAYLOAD_FORMAT_STRATEGIES, typed_string_formats


@pytest.mark.allow_direct_assert("compares two sets of format names, which no output helper stores")
def test_payload_format_strategies_cover_every_typed_string_format() -> None:
    """Every string format a payload backend types as other than str has a strategy, or a reason it needs none."""
    typed = set().union(*map(typed_string_formats, PayloadBackend))
    assert set(PAYLOAD_FORMAT_STRATEGIES) == typed, (
        f"formats without a payload strategy: {sorted(typed - set(PAYLOAD_FORMAT_STRATEGIES))}; "
        f"strategies of formats every backend types as str: {sorted(set(PAYLOAD_FORMAT_STRATEGIES) - typed)}"
    )
