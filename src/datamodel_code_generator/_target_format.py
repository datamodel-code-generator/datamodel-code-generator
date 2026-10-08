"""Format generated target files with the model formatter, sorting the model package's imports the same every run."""

from __future__ import annotations

import json
from typing import Any

from datamodel_code_generator.format import CodeFormatter


class TargetCodeFormatter(CodeFormatter):
    """Format target files like model files, with the model package a third-party import for isort and Ruff.

    The model files are still staged while the target files are formatted. isort and Ruff treat an import as first
    party when its module exists in a source root, so without this the model imports would sort differently in the
    first run, which finds no models yet, and in the runs after it. The builtin formatter already treats them as
    third party.
    """

    def __init__(self, *args: Any, model_package: str, **kwargs: Any) -> None:
        """Build the model formatter, naming the top-level model package as third party."""
        self.model_root = model_package.partition(".")[0]
        super().__init__(*args, known_third_party=[self.model_root], **kwargs)

    def _ruff_check_command(self, *paths: str, ruff_path: str | None = None) -> tuple[str, ...]:
        """Return the model Ruff check command with the model package as third party for the isort rules."""
        option = f"lint.isort.known-third-party = {json.dumps([self.model_root])}"
        return (*super()._ruff_check_command(ruff_path=ruff_path), "--config", option, *paths)
