"""Tests for reference resolution functionality."""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator.http import join_url
from datamodel_code_generator.reference import (
    ModelResolver,
    get_relative_path,
    get_singular_name,
    is_url,
    snake_to_upper_camel,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from os import stat_result
    from typing import Literal


@pytest.mark.parametrize(
    ("base_path", "target_path", "expected"),
    [
        ("/a/b", "/a/b", "."),
        ("/a/b", "/a/b/c", "c"),
        ("/a/b", "/a/b/c/d", "c/d"),
        ("/a/b/c", "/a/b", ".."),
        ("/a/b/c/d", "/a/b", "../.."),
        ("/a/b/c/d", "/a", "../../.."),
        ("/a/b/c/d", "/a/x/y/z", "../../../x/y/z"),
        ("/a/b/c/d", "a/x/y/z", "a/x/y/z"),
        ("/a/b/c/d", "/a/b/e/d", "../../e/d"),
    ],
)
def test_get_relative_path_posix(base_path: str, target_path: str, expected: str) -> None:
    """Test get_relative_path function on POSIX paths."""
    assert PurePosixPath(get_relative_path(PurePosixPath(base_path), PurePosixPath(target_path))) == PurePosixPath(
        expected
    )


@pytest.mark.parametrize(
    ("base_path", "target_path", "expected"),
    [
        ("c:/a/b", "c:/a/b", "."),
        ("c:/a/b", "c:/a/b/c", "c"),
        ("c:/a/b", "c:/a/b/c/d", "c/d"),
        ("c:/a/b/c", "c:/a/b", ".."),
        ("c:/a/b/c/d", "c:/a/b", "../.."),
        ("c:/a/b/c/d", "c:/a", "../../.."),
        ("c:/a/b/c/d", "c:/a/x/y/z", "../../../x/y/z"),
        ("c:/a/b/c/d", "a/x/y/z", "a/x/y/z"),
        ("c:/a/b/c/d", "c:/a/b/e/d", "../../e/d"),
    ],
)
def test_get_relative_path_windows(base_path: str, target_path: str, expected: str) -> None:
    """Test get_relative_path function on Windows paths."""
    assert PureWindowsPath(
        get_relative_path(PureWindowsPath(base_path), PureWindowsPath(target_path))
    ) == PureWindowsPath(expected)


def test_model_resolver_add_ref_with_hash() -> None:
    """Test adding reference with URL fragment."""
    model_resolver = ModelResolver()
    reference = model_resolver.add_ref("https://json-schema.org/draft/2020-12/meta/core#")
    assert reference.original_name == "core"


def test_model_resolver_add_ref_without_hash() -> None:
    """Test adding reference without URL fragment."""
    model_resolver = ModelResolver()
    reference = model_resolver.add_ref("meta/core")
    assert reference.original_name == "core"


def test_model_resolver_add_ref_unevaluated() -> None:
    """Test adding reference for unevaluated schema."""
    model_resolver = ModelResolver()
    reference = model_resolver.add_ref("meta/unevaluated")
    assert reference.original_name == "unevaluated"


def test_model_resolver_add_class_name_generates_class_reference() -> None:
    """Class-name references still flow through get_class_name."""
    model_resolver = ModelResolver()

    reference = model_resolver.add(["#", "definitions", "user"], "user", class_name=True)

    assert reference.name == "User"
    assert reference.duplicate_name is None


@pytest.mark.parametrize(
    ("name", "treat_dot_as_module", "strict_dotted_module_names", "expected"),
    [
        ("models.Pet", None, False, "models.Pet"),
        ("SaveTrifectaV2.1", None, False, "SaveTrifectaV2.Field1"),
        ("pkg.class.Model", None, False, "pkg.class_.Model"),
        ("models.Pet", None, True, "models.Pet"),
        ("SaveTrifectaV2.1", None, True, "SaveTrifectaV21"),
        ("pkg.class.Model", None, True, "PkgClassModel"),
        ("models.Pet", False, True, "ModelsPet"),
        ("SaveTrifectaV2.1", True, True, "SaveTrifectaV2.Field1"),
    ],
)
def test_model_resolver_dotted_class_name(
    name: str,
    treat_dot_as_module: bool | None,
    strict_dotted_module_names: bool,
    expected: str,
) -> None:
    """Interpret dotted class names according to explicit and inferred policies."""
    resolver = ModelResolver(
        treat_dot_as_module=treat_dot_as_module,
        strict_dotted_module_names=strict_dotted_module_names,
    )

    class_name = resolver.get_class_name(name, unique=False)

    assert class_name.name == expected


def test_reference_cache_clear_preserves_helper_values() -> None:
    """Clearing bounded reference caches must not change helper results."""
    singular_name = get_singular_name("users")
    upper_camel = snake_to_upper_camel("user_name")

    get_singular_name.cache_clear()
    snake_to_upper_camel.cache_clear()

    assert get_singular_name("users") == singular_name
    assert snake_to_upper_camel("user_name") == upper_camel


def test_model_resolver_delete_missing_reference_noop() -> None:
    """Deleting a missing reference should not change existing references."""
    model_resolver = ModelResolver()
    reference = model_resolver.add(["#", "definitions", "User"], "User")

    model_resolver.delete(["#", "definitions", "Missing"])

    assert model_resolver.get(["#", "definitions", "User"]) is reference


def test_base_url_context_sets_url_when_base_url_already_set() -> None:
    """When _base_url is already set, base_url_context should switch to new URL."""
    resolver = ModelResolver(base_url="https://example.com/original.json")
    assert resolver.base_url == "https://example.com/original.json"

    with resolver.base_url_context("https://example.com/new.json"):
        assert resolver.base_url == "https://example.com/new.json"

    # Should restore original
    assert resolver.base_url == "https://example.com/original.json"


def test_base_url_context_sets_url_when_new_value_is_url() -> None:
    """When _base_url is None but new value is a URL, should set base_url."""
    resolver = ModelResolver()
    assert resolver.base_url is None

    with resolver.base_url_context("https://example.com/schema.json"):
        assert resolver.base_url == "https://example.com/schema.json"

    # Should restore to None
    assert resolver.base_url is None


def test_base_url_context_noop_when_new_value_is_not_url() -> None:
    """When _base_url is None and new value is not a URL, should do nothing."""
    resolver = ModelResolver()
    assert resolver.base_url is None

    with resolver.base_url_context("../relative/path.json"):
        # Should remain None because the value is not a URL
        assert resolver.base_url is None

    assert resolver.base_url is None


def test_base_url_context_nested() -> None:
    """Nested base_url_context should properly restore values."""
    resolver = ModelResolver(base_url="https://example.com/level0.json")

    with resolver.base_url_context("https://example.com/level1.json"):
        assert resolver.base_url == "https://example.com/level1.json"

        with resolver.base_url_context("https://example.com/level2.json"):
            assert resolver.base_url == "https://example.com/level2.json"

        assert resolver.base_url == "https://example.com/level1.json"

    assert resolver.base_url == "https://example.com/level0.json"


def test_resolve_ref_with_base_url_does_not_prepend_root_id_base_path() -> None:
    """When base_url is set, root_id_base_path should not be prepended to refs."""
    resolver = ModelResolver(base_url="https://example.com/schemas/main.json")
    resolver.set_root_id("https://example.com/schemas/main.json")

    # Resolve a relative ref
    result = resolver.resolve_ref("../other/schema.json")

    # Should resolve via join_url, not prepend root_id_base_path
    assert result == "https://example.com/other/schema.json#"
    # Should NOT be like "https://example.com/schemas/../other/schema.json#"


def test_resolve_ref_with_base_url_nested_relative_refs() -> None:
    """Nested relative refs should resolve correctly when base_url is set."""
    resolver = ModelResolver(base_url="https://example.com/a/b/c/main.json")

    # Resolve a deeply nested relative ref
    result = resolver.resolve_ref("../../other/schema.json")

    assert result == "https://example.com/a/other/schema.json#"


def test_resolve_ref_with_base_url_context_switch() -> None:
    """Relative refs should resolve correctly after base_url context switch."""
    resolver = ModelResolver(base_url="https://example.com/schemas/person.json")

    # Switch context to a different file
    with resolver.base_url_context("https://example.com/schemas/definitions/pet.json"):
        # Resolve a relative ref from the new context
        result = resolver.resolve_ref("../common/types.json")

        assert result == "https://example.com/schemas/common/types.json#"


def test_resolve_ref_local_fragment_with_base_url() -> None:
    """Local fragment refs should resolve to full URL when base_url is set."""
    resolver = ModelResolver(base_url="https://example.com/schemas/main.json")

    result = resolver.resolve_ref("#/definitions/Foo")

    # When base_url is set, local fragments are resolved to full URL
    assert result == "https://example.com/schemas/main.json#/definitions/Foo"


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        # HTTP/HTTPS URLs
        ("https://example.com/schema.json", True),
        ("http://example.com/schema.json", True),
        ("https://example.com/path/to/schema.json", True),
        # file:// URLs - recognized and handled via filesystem
        ("file:///home/user/schema.json", True),
        ("file:///C:/path/to/schema.json", True),
        ("file://server/share/schema.json", True),
        # file:/ (single slash) - NOT recognized as valid file URL
        ("file:/home/user/schema.json", False),
        # Other URL schemes - NOT recognized
        ("ftp://example.com/schema.json", False),
        # Relative paths (not URLs)
        ("../relative/path.json", False),
        ("relative/path.json", False),
        # Local fragments (not URLs)
        ("#/definitions/Foo", False),
        ("#", False),
        # Absolute paths (not URLs)
        ("/absolute/path.json", False),
        # Windows paths (not URLs)
        ("c:/windows/path.json", False),
        ("d:/path/to/file.json", False),
    ],
)
def test_is_url(ref: str, expected: bool) -> None:
    """Test is_url correctly identifies HTTP(S) and file:// URLs."""
    assert is_url(ref) == expected


def test_resolve_ref_with_root_id_differs_from_base_url() -> None:
    """When $id differs from fetch URL, refs should resolve against $id."""
    # Scenario: Schema fetched from CDN but has canonical $id
    resolver = ModelResolver(base_url="https://cdn.example.com/latest/schema.json")
    resolver.set_root_id("https://example.com/v1/schema.json")

    result = resolver.resolve_ref("../common/types.json")

    assert result == "https://example.com/common/types.json#"


def test_resolve_ref_with_relative_root_id_and_base_url() -> None:
    """Relative root $id should be resolved against the retrieval URL before $ref resolution."""
    resolver = ModelResolver(base_url="http://localhost:8888/schemas/v1/main.schema.json")
    resolver.set_root_id("/schemas/v1/main.schema.json")

    result = resolver.resolve_ref("sub.schema.json")

    assert result == "http://localhost:8888/schemas/v1/sub.schema.json#"


def test_resolve_ref_caches_local_file_parts_by_base_path(tmp_path: Path) -> None:
    """Keep fragment handling separate while caching each local file path per base path."""
    first_base_path = tmp_path / "first"
    second_base_path = tmp_path / "second"
    for base_path in (first_base_path, second_base_path):
        base_path.mkdir()
        (base_path / "shared.json").touch()

    resolver = ModelResolver(base_path=tmp_path)
    with resolver.current_base_path_context(first_base_path):
        assert resolver.resolve_ref("shared.json#/$defs/First") == "first/shared.json#/$defs/First"
        assert resolver.resolve_ref("shared.json#/$defs/Second") == "first/shared.json#/$defs/Second"
        assert (
            resolver.resolve_ref(f"{first_base_path / 'shared.json'}#/$defs/Absolute")
            == "first/shared.json#/$defs/Absolute"
        )
    with resolver.current_base_path_context(second_base_path):
        assert resolver.resolve_ref("shared.json#/$defs/Third") == "second/shared.json#/$defs/Third"

    assert resolver.resolve_ref("#/defs/Local") == "#/defs/Local"
    with resolver.current_root_context(["root"]):
        assert resolver.resolve_ref("#") == "root#"
    assert (
        resolver.resolve_ref("https://example.com/shared.json#/$defs/Remote")
        == "https://example.com/shared.json#/$defs/Remote"
    )
    assert resolver._resolved_local_file_parts == {
        (first_base_path, "shared.json"): "first/shared.json",
        (first_base_path, str(first_base_path / "shared.json")): "first/shared.json",
        (second_base_path, "shared.json"): "second/shared.json",
    }


def test_resolve_ref_normalizes_empty_path_as_root_fragment() -> None:
    """Treat an empty reference as the ``#`` root fragment instead of indexing it."""
    resolver = ModelResolver()

    assert resolver.resolve_ref("") == "#"
    with resolver.current_root_context(["root"]):
        assert resolver.resolve_ref("") == "root#"


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX symlinks")
@pytest.mark.parametrize("symlink_kind", ["file", "parent"])
def test_resolve_ref_local_file_cache_tracks_retargeted_symlinks(
    tmp_path: Path, symlink_kind: Literal["file", "parent"]
) -> None:
    """Do not retain a reference whose direct or parent symlink is later retargeted."""
    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"
    for directory in (first_directory, second_directory):
        directory.mkdir()
        (directory / "shared.json").touch()

    if symlink_kind == "file":
        symlink_path = tmp_path / "shared.json"
        first_target = first_directory / "shared.json"
        second_target = second_directory / "shared.json"
        reference_path = "shared.json"
        target_is_directory = False
    else:
        symlink_path = tmp_path / "schemas"
        first_target = first_directory
        second_target = second_directory
        reference_path = "schemas/shared.json"
        target_is_directory = True

    symlink_path.symlink_to(first_target, target_is_directory=target_is_directory)
    resolver = ModelResolver(base_path=tmp_path)

    assert resolver.resolve_ref(f"{reference_path}#/$defs/First") == "first/shared.json#/$defs/First"
    assert resolver._resolved_local_file_parts == {}

    symlink_path.unlink()
    symlink_path.symlink_to(second_target, target_is_directory=target_is_directory)

    assert resolver.resolve_ref(f"{reference_path}#/$defs/Second") == "second/shared.json#/$defs/Second"
    assert resolver._resolved_local_file_parts == {}


def test_resolve_ref_does_not_cache_when_symlink_inspection_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preserve uncached resolution if checking a local path for symlinks fails."""
    (tmp_path / "shared.json").touch()
    original_lstat = Path.lstat

    def raise_symlink_error(path: Path) -> stat_result:
        if path == tmp_path:
            msg = "simulated symlink inspection failure"
            raise OSError(msg)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", raise_symlink_error)
    resolver = ModelResolver(base_path=tmp_path)

    assert resolver.resolve_ref("shared.json#/$defs/Shared") == "shared.json#/$defs/Shared"
    assert resolver._resolved_local_file_parts == {}


def test_resolve_ref_does_not_cache_failed_local_file_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do not retain a local reference when filesystem resolution fails."""
    target = tmp_path / "missing.json"
    resolver = ModelResolver(base_path=tmp_path)

    def raise_resolution_error(path: Path, *args: object, **kwargs: object) -> Path:
        del path, args, kwargs
        msg = "simulated filesystem failure"
        raise OSError(msg)

    monkeypatch.setattr(Path, "resolve", raise_resolution_error)

    with pytest.raises(OSError, match="simulated filesystem failure"):
        resolver.resolve_ref(f"{target.name}#/$defs/Loop")

    assert resolver._resolved_local_file_parts == {}


def test_resolve_ref_local_file_cache_stays_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve uncached files without symlink inspection after the cache reaches its limit."""
    (tmp_path / "uncached.json").touch()
    resolver = ModelResolver(base_path=tmp_path)
    resolver._resolved_local_file_parts.update({
        (tmp_path, f"cached-{index}.json"): f"cached-{index}.json"
        for index in range(resolver._MAX_RESOLVED_LOCAL_FILE_PARTS)
    })

    def fail_unexpected_lstat(_path: Path) -> stat_result:
        pytest.fail("symlink inspection should be skipped when the cache is full")  # pragma: no cover

    monkeypatch.setattr(Path, "lstat", fail_unexpected_lstat)

    assert resolver.resolve_ref("uncached.json#/$defs/Uncached") == "uncached.json#/$defs/Uncached"
    assert (tmp_path, "uncached.json") not in resolver._resolved_local_file_parts


def test_resolve_ref_local_file_cache_preserves_model_resolver_overrides(tmp_path: Path) -> None:
    """Keep custom resolver overrides on the normal reference-resolution dispatch path."""

    class TrackingModelResolver(ModelResolver):
        resolved_paths: list[str]

        def __init__(self, base_path: Path) -> None:
            super().__init__(base_path=base_path)
            self.resolved_paths = []

        def resolve_ref(self, path: Sequence[str] | str) -> str:
            self.resolved_paths.append(path if isinstance(path, str) else "/".join(path))
            return super().resolve_ref(path)

    (tmp_path / "shared.json").touch()
    resolver = TrackingModelResolver(tmp_path)

    assert resolver.resolve_ref("shared.json#/$defs/Model") == "shared.json#/$defs/Model"
    assert resolver.resolved_paths == ["shared.json#/$defs/Model"]


def test_resolve_path_absolute_local_ref_with_root_id(tmp_path: Path) -> None:
    """Path-absolute refs with path-only $id should resolve inside the local schema root."""
    schema_root = tmp_path
    target = schema_root / "core" / "platform-extension-ref.json"
    target.parent.mkdir()
    target.touch()

    resolver = ModelResolver(base_path=schema_root)
    resolver.set_root_id("/schemas/3.1.0-beta.1/formats/canonical/_base.json")

    with (
        resolver.current_root_context(["formats", "canonical", "_base.json"]),
        resolver.current_base_path_context(Path("formats/canonical")),
    ):
        result = resolver.resolve_ref("/schemas/3.1.0-beta.1/core/platform-extension-ref.json")

    assert result == "core/platform-extension-ref.json#"


def test_resolve_path_absolute_local_ref_rejects_parent_traversal(tmp_path: Path) -> None:
    """Path-absolute refs should not resolve files outside the local schema root."""
    schema_root = tmp_path
    outside_target = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside_target.touch()

    resolver = ModelResolver(base_path=schema_root)
    resolver.set_root_id("/schemas/3.1.0-beta.1/formats/canonical/_base.json")

    with (
        resolver.current_root_context(["formats", "canonical", "_base.json"]),
        resolver.current_base_path_context(Path("formats/canonical")),
    ):
        result = resolver._resolve_path_absolute_local_ref(f"/schemas/3.1.0-beta.1/../{outside_target.name}")

    assert result is None


def test_resolve_path_absolute_local_ref_with_fragment(tmp_path: Path) -> None:
    """Path-absolute refs should preserve fragments after local schema root resolution."""
    schema_root = tmp_path
    target = schema_root / "core" / "platform-extension-ref.json"
    target.parent.mkdir()
    target.touch()

    resolver = ModelResolver(base_path=schema_root)
    resolver.set_root_id("/schemas/3.1.0-beta.1/formats/canonical/_base.json")

    with (
        resolver.current_root_context(["formats", "canonical", "_base.json"]),
        resolver.current_base_path_context(Path("formats/canonical")),
    ):
        result = resolver.resolve_ref("/schemas/3.1.0-beta.1/core/platform-extension-ref.json#/definitions/ref")

    assert result == "core/platform-extension-ref.json#/definitions/ref"


@pytest.mark.parametrize(
    ("root_id", "current_root", "ref"),
    [
        (
            "/schemas/3.1.0-beta.1/formats/canonical/_base.json",
            ["formats", "canonical", "_base.json"],
            "/other/core/platform-extension-ref.json",
        ),
        (
            "/schemas/3.1.0-beta.1/formats/canonical/_base.json",
            ["formats", "canonical", "_base.json"],
            "/schemas/3.1.0-beta.1/",
        ),
        (
            "/schemas/3.1.0-beta.1/formats/canonical/_base.json",
            ["formats", "canonical", "_base.json"],
            "/schemas/3.1.0-beta.1/core/missing.json",
        ),
        (
            "/schemas/3.1.0-beta.1/formats/canonical/_base.json",
            ["schemas", "formats", "canonical", "_base.json"],
            "/schemas/3.1.0-beta.1/core/platform-extension-ref.json",
        ),
    ],
)
def test_resolve_path_absolute_local_ref_falls_back(
    tmp_path: Path,
    root_id: str,
    current_root: list[str],
    ref: str,
) -> None:
    """Unmatched path-absolute refs should fall back to the regular local resolver."""
    resolver = ModelResolver(base_path=tmp_path)
    resolver.set_root_id(root_id)

    with (
        resolver.current_root_context(current_root),
        resolver.current_base_path_context(Path("formats/canonical")),
    ):
        result = resolver._resolve_path_absolute_local_ref(ref)

    assert result is None


@pytest.mark.parametrize(
    ("base_url", "ref", "expected"),
    [
        # file:// URL joining - relative refs
        ("file:///home/user/schemas/main.json", "../common/types.json", "file:///home/user/common/types.json"),
        ("file:///home/user/schemas/main.json", "other.json", "file:///home/user/schemas/other.json"),
        ("file:///home/user/schemas/main.json", "./sub/schema.json", "file:///home/user/schemas/sub/schema.json"),
        # file:// URL joining - absolute file:// refs
        ("file:///home/user/schemas/main.json", "file:///other/schema.json", "file:///other/schema.json"),
        # file:// URL joining - absolute path refs (starts with /)
        ("file:///home/user/schemas/main.json", "/absolute/path.json", "file:///absolute/path.json"),
        ("file://server/share/main.json", "/absolute/path.json", "file://server/absolute/path.json"),
        # Windows-style file:// URLs
        ("file:///C:/schemas/main.json", "../common/types.json", "file:///C:/common/types.json"),
        # UNC file:// URLs
        ("file://server/share/main.json", "../common/types.json", "file://server/share/common/types.json"),
        ("file://server/share/main.json", "child.json", "file://server/share/child.json"),
        # Fragment handling
        (
            "file:///home/user/schemas/main.json",
            "other.json#/definitions/Foo",
            "file:///home/user/schemas/other.json#/definitions/Foo",
        ),
        (
            "file:///home/user/schemas/main.json",
            "#/definitions/Bar",
            "file:///home/user/schemas/main.json#/definitions/Bar",
        ),
        # Multiple .. traversal - stops at root for non-UNC
        ("file:///a/b/main.json", "../../../other.json", "file:///other.json"),
        # Multiple .. traversal - stops at share level for UNC (min_depth=1)
        ("file://server/share/a/b/main.json", "../../../../other.json", "file://server/share/other.json"),
        # Empty and dot segments
        ("file:///home/user/schemas/main.json", "./", "file:///home/user/schemas/"),
        ("file:///home/user/schemas/main.json", "a//b/./c.json", "file:///home/user/schemas/a/b/c.json"),
        # Fragment-only ref without fragment content (just #)
        ("file:///home/user/schemas/main.json", "#", "file:///home/user/schemas/main.json#"),
        # Empty ref (keeps base URL unchanged)
        ("file:///home/user/schemas/main.json", "", "file:///home/user/schemas/main.json"),
        # Root directory base URL (triggers empty base_segments branch)
        ("file:///", "schema.json", "file:///schema.json"),
        ("file:///main.json", "../other.json", "file:///other.json"),
    ],
)
def test_join_url_file_scheme(base_url: str, ref: str, expected: str) -> None:
    """Test join_url correctly handles file:// URLs."""
    assert join_url(base_url, ref) == expected


def test_url_ref_matches_local_id_no_fragment() -> None:
    """URL $ref matching a local $id should resolve to the $id's path (Issue #1747)."""
    resolver = ModelResolver()
    resolver.set_current_root([])
    resolver.add_id("https://schemas.example.org/child", ["#", "$defs", "child"])

    result = resolver.resolve_ref("https://schemas.example.org/child#")

    assert result == "#/$defs/child"


def test_url_ref_matches_local_id_with_fragment() -> None:
    """URL $ref with fragment should combine $id path with fragment (Issue #1747)."""
    resolver = ModelResolver()
    resolver.set_current_root([])
    resolver.add_id("https://schemas.example.org/child", ["#", "$defs", "child"])

    result = resolver.resolve_ref("https://schemas.example.org/child#/properties/name")

    assert result == "#/$defs/child/properties/name"


def test_url_ref_no_matching_local_id() -> None:
    """URL $ref not matching any local $id should remain as URL (Issue #1747)."""
    resolver = ModelResolver()
    resolver.set_current_root([])

    result = resolver.resolve_ref("https://schemas.example.org/other#")

    assert result == "https://schemas.example.org/other#"


def test_url_ref_matches_local_id_nested_fragment() -> None:
    """URL $ref with deeply nested fragment should resolve correctly (Issue #1747)."""
    resolver = ModelResolver()
    resolver.set_current_root([])
    resolver.add_id("https://example.org/types", ["#", "$defs", "types"])

    result = resolver.resolve_ref("https://example.org/types#/definitions/Address/properties/city")

    assert result == "#/$defs/types/definitions/Address/properties/city"


def test_url_ref_matches_local_id_with_base_url() -> None:
    """URL $ref matching local $id should resolve via $id mapping even when base_url is set (Issue #1747)."""
    resolver = ModelResolver(base_url="https://cdn.example.com/schemas/main.json")
    resolver.set_current_root([])
    resolver.add_id("https://schemas.example.org/child", ["#", "$defs", "child"])

    result = resolver.resolve_ref("https://schemas.example.org/child#")

    assert result == "https://cdn.example.com/schemas/main.json#/$defs/child"


def test_url_ref_matches_local_id_preserves_empty_json_pointer_token() -> None:
    """URL $ref fragment with empty JSON Pointer token (//) should be preserved (Issue #1747)."""
    resolver = ModelResolver()
    resolver.set_current_root([])
    resolver.add_id("https://example.org/types", ["#", "$defs", "types"])

    result = resolver.resolve_ref("https://example.org/types#/items//child")

    assert result == "#/$defs/types/items//child"


def test_resolve_ref_local_fragment_with_base_url_and_current_root() -> None:
    """Local fragment refs should resolve to current_root when it's set, even with base_url (Issue #1798)."""
    resolver = ModelResolver(base_url="https://raw.githubusercontent.com/user/repo/schema.json")
    resolver.set_root_id("https://cveproject.github.io/schema/schema.json")
    resolver.set_current_root(["https://raw.githubusercontent.com/user/repo/schema.json"])

    result = resolver.resolve_ref("#/definitions/Foo")

    assert result == "https://raw.githubusercontent.com/user/repo/schema.json#/definitions/Foo"


def test_resolve_ref_local_fragment_with_different_host_base_url_and_root_id() -> None:
    """Local fragment refs should resolve correctly when base_url and root_id have different hosts (Issue #1798)."""
    resolver = ModelResolver(base_url="https://raw.githubusercontent.com/user/repo/schema.json")
    resolver.set_root_id("https://cveproject.github.io/schema/schema.json")
    resolver.set_current_root(["https://raw.githubusercontent.com/user/repo/schema.json"])

    result = resolver.resolve_ref("#/definitions/product/properties/url")

    assert result == "https://raw.githubusercontent.com/user/repo/schema.json#/definitions/product/properties/url"


def test_resolve_ref_local_fragment_without_current_root_falls_back_to_url() -> None:
    """Local fragment refs without current_root should fall back to URL resolution (Issue #1798)."""
    resolver = ModelResolver(base_url="https://example.com/schemas/main.json")

    result = resolver.resolve_ref("#/definitions/Foo")

    assert result == "https://example.com/schemas/main.json#/definitions/Foo"
