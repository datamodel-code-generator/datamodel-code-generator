"""Custom-template dependency observation, imported only when Jinja is needed."""

from __future__ import annotations

from typing import TYPE_CHECKING

from jinja2 import FileSystemLoader
from jinja2.loaders import split_template_path
from typing_extensions import override

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from jinja2 import Environment


class DependencyTrackingLoader(FileSystemLoader):
    """Observe actual dynamic lookups, including missing include/import/extends candidates."""

    def __init__(self, directory: Path, remember: Callable[[Path], None]) -> None:
        super().__init__(str(directory))
        self._directory = directory
        self._remember = remember

    @override
    def get_source(self, environment: Environment, template: str) -> tuple[str, str, Callable[[], bool]]:
        """Track a candidate before loading so failures retain their negative dependencies."""
        self._remember(self._directory.joinpath(*split_template_path(template)))
        return super().get_source(environment, template)
