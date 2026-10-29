"""Every Pydantic ``ConfigDict`` key that the generator can write has a row in ``_requirements.py``.

It depends only on the installed Pydantic, so each test environment checks it once.
"""

from __future__ import annotations


def config_keys_without_requirement() -> tuple[str, ...]:
    """Return the keys of the installed Pydantic's ``ConfigDict`` that have no row: user config writes any of them."""
    from pydantic import ConfigDict

    from datamodel_code_generator._requirements import CONFIG_KEY_REQUIREMENTS

    return tuple(
        sorted(
            key
            for key in ConfigDict.__required_keys__ | ConfigDict.__optional_keys__
            if key not in CONFIG_KEY_REQUIREMENTS
        )
    )
