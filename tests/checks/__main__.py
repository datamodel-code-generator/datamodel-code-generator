"""Run ``datamodel-codegen`` with the debug checks installed: ``python -m tests.checks <datamodel-codegen args>``.

The ``DATAMODEL_CODE_GENERATOR_CHECK_<AREA>`` variables turn the areas on as in the test suite.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from tests.checks.installer import install

if TYPE_CHECKING:
    from collections.abc import Sequence


def main(args: Sequence[str]) -> int:
    """Install the checks and run ``datamodel-codegen`` with ``args``; return its exit status."""
    install()
    from datamodel_code_generator.__main__ import main as generate

    return int(generate(args))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
