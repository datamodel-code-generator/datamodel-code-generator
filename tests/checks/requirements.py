"""Every Pydantic ``ConfigDict`` key that the generator can write has a row in ``_requirements.py``.

It depends only on the installed Pydantic, so each test environment checks it once. The type of the constructs and the
table of their rows list the same constructs.
"""

from __future__ import annotations

from typing import get_args


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


def constructs_out_of_step() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the rows that the construct types do not list, and the constructs they list that have no row."""
    from datamodel_code_generator._requirements import REQUIREMENTS, PydanticConstruct, PythonConstruct

    listed = {*get_args(PythonConstruct), *get_args(PydanticConstruct)}
    return tuple(sorted(REQUIREMENTS.keys() - listed)), tuple(sorted(listed - REQUIREMENTS.keys()))
