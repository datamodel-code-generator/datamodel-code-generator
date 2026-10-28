"""Format generated target files with the model formatter, sorting the model package's imports the same every run."""

from __future__ import annotations

import json
from typing import Any

from datamodel_code_generator.format import CodeFormatter


class TargetCodeFormatter(CodeFormatter):
    """Format target files like model files, with the model and target packages third-party imports for isort and Ruff.

    The model and target files are still staged while the target files are formatted. isort and Ruff treat an import
    as first party when its module exists in a source root, so without this the imports of the models, and those of the
    target package in the user modules beside it, would sort differently in the first run, which finds neither yet, and
    in the runs after it. The builtin formatter already treats them as third party.
    """

    def __init__(self, *args: Any, model_package: str, package: str, **kwargs: Any) -> None:
        """Build the model formatter, naming the top-level model and target packages as third party."""
        self.roots = list(dict.fromkeys(name.partition(".")[0] for name in (model_package, package)))
        super().__init__(*args, known_third_party=self.roots, **kwargs)

    def _ruff_check_command(self, *paths: str, ruff_path: str | None = None) -> tuple[str, ...]:
        """Return the model Ruff check command with the model and target packages third party for the isort rules."""
        option = f"lint.isort.known-third-party = {json.dumps(self.roots)}"
        return (*super()._ruff_check_command(ruff_path=ruff_path), "--config", option, *paths)
