"""Direct tests for datamodel_code_generator.util helper behavior."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from datamodel_code_generator import util
from datamodel_code_generator.util import _get_toml_loader, create_module_getattr, get_safe_loader, load_toml


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    """Clear lru_cache state so loader construction is characterized directly."""
    get_safe_loader.cache_clear()
    _get_toml_loader.cache_clear()


@pytest.mark.allow_direct_assert
def test_import_util_does_not_load_toml_parser() -> None:
    """Importing util does not import a TOML parser until load_toml is used."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "before = {name for name in ('tomli', 'tomllib') if name in sys.modules}; "
                "import datamodel_code_generator.util; "
                "after = {name for name in ('tomli', 'tomllib') if name in sys.modules}; "
                "print(sorted(after - before))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == "[]\n"


@pytest.mark.allow_direct_assert
def test_load_toml_parses_file(tmp_path: Path) -> None:
    """load_toml still parses TOML files through the selected backend."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.example]\nvalue = 'ok'\n", encoding="utf-8")

    assert load_toml(pyproject) == {"tool": {"example": {"value": "ok"}}}


@pytest.mark.allow_direct_assert
def test_safe_loader_warns_for_uppercase_yaml_bool() -> None:
    """PyYAML uppercase bool construction emits the deprecation warning."""
    yaml = pytest.importorskip("yaml")

    with pytest.warns(DeprecationWarning, match=r"YAML bool 'TRUE' is deprecated"):
        result = yaml.load("enabled: TRUE\n", Loader=get_safe_loader())

    assert result == {"enabled": True}


@pytest.mark.allow_direct_assert
def test_create_module_getattr_imports_lazy_attribute() -> None:
    """create_module_getattr imports and returns configured lazy attributes."""
    module_getattr = create_module_getattr(
        "example.module",
        {"Path": ("pathlib", "Path")},
    )

    assert module_getattr("Path") is Path


@pytest.mark.allow_direct_assert
def test_create_module_getattr_raises_attribute_error_for_unknown_attribute() -> None:
    """create_module_getattr raises the normal module AttributeError message."""
    module_getattr = create_module_getattr("example.module", {})

    with pytest.raises(AttributeError) as exc_info:
        module_getattr("Missing")

    assert str(exc_info.value) == "module 'example.module' has no attribute 'Missing'"


@pytest.mark.allow_direct_assert("checks the identity of a lazily imported module attribute")
def test_util_safe_loader_is_the_selected_safe_loader() -> None:
    """The lazy ``util.SafeLoader`` name stays the loader get_safe_loader selects."""
    assert util.SafeLoader is get_safe_loader()
