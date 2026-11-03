"""``--treat-dot-as-module`` must not crash on an OpenAPI input whose path
items live in external files.

The postprocessor that seeds ``__init__.py`` from an existing package init
used ``next(...)`` on a generator of matching results. When the input has no
module ending in ``__init__.py`` (the case here: every result is a plain
module produced from an external ``$ref``), the generator was exhausted and
raised ``StopIteration``, aborting generation. Fall back to an empty init
result so generation proceeds.
"""

from __future__ import annotations

from pathlib import Path

from datamodel_code_generator.__main__ import Exit

from tests.main.conftest import (
    DATA_PATH,
    run_main_and_assert,
)

OPEN_API_DATA_PATH: Path = DATA_PATH / "openapi"
EXTERNAL_REF = "paths_external_ref"


def test_treat_dot_as_module_external_ref(output_dir: Path, capsys: object) -> None:
    """Generate a package from external path refs with ``--treat-dot-as-module``.

    The input has no module ending in ``__init__.py``, so the ``__init__``
    postprocessor used to raise ``StopIteration`` and abort generation. It must
    now degrade to the same outcome as running without the flag.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / EXTERNAL_REF,
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--treat-dot-as-module"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Models not found in the input data",
    )
