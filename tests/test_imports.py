"""Tests for import management functionality."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator.imports import Import, Imports

if TYPE_CHECKING:
    from collections.abc import Sequence


@pytest.mark.parametrize(
    ("inputs", "value"),
    [
        ([(None, "foo")], "import foo"),
        ([(".", "foo")], "from . import foo"),
        ([("bar", "foo")], "from bar import foo"),
        ([("bar", "foo"), ("bar", "baz")], "from bar import baz, foo"),
        ([("bar", "foo"), ("rab", "oof")], "from bar import foo\nfrom rab import oof"),
        ([("bar", "foo"), ("bar", "foo")], "from bar import foo"),
        ([(None, "foo.baz")], "import foo.baz"),
    ],
)
def test_dump(inputs: Sequence[tuple[str | None, str]], value: str) -> None:
    """Test creating import lines."""
    imports = Imports()
    imports.append(Import(from_=from_, import_=import_) for from_, import_ in inputs)

    assert str(imports) == value


def test_is_future_true() -> None:
    """Test that __future__ imports are identified as future imports."""
    import_ = Import(from_="__future__", import_="annotations")
    assert import_.is_future is True


def test_is_future_false_regular_import() -> None:
    """Test that regular imports are not identified as future imports."""
    import_ = Import(from_="typing", import_="Optional")
    assert import_.is_future is False


def test_is_future_false_no_from() -> None:
    """Test that imports without from_ are not identified as future imports."""
    import_ = Import(from_=None, import_="os")
    assert import_.is_future is False


def test_pydantic_import_constants_share_leaf_module() -> None:
    """Test Pydantic import constants share identity without changing dataclass Field."""
    from datamodel_code_generator.model import imports as model_imports
    from datamodel_code_generator.model import pydantic_base
    from datamodel_code_generator.model.pydantic_v2 import imports as pydantic_v2_imports

    assert pydantic_base.IMPORT_FIELD is pydantic_v2_imports.IMPORT_FIELD
    assert pydantic_base.IMPORT_ANYURL is pydantic_v2_imports.IMPORT_ANYURL
    assert Import(import_="Field", from_="pydantic") == pydantic_base.IMPORT_FIELD
    assert Import(import_="AnyUrl", from_="pydantic") == pydantic_base.IMPORT_ANYURL
    assert model_imports.IMPORT_FIELD is not pydantic_base.IMPORT_FIELD
    assert Import(import_="field", from_="dataclasses") == model_imports.IMPORT_FIELD


def test_import_from_full_path_cache_clear_preserves_value() -> None:
    """Clearing the bounded cache must not change Import.from_full_path results."""
    import_ = Import.from_full_path("typing.Optional")

    Import.from_full_path.cache_clear()

    assert Import.from_full_path("typing.Optional") == import_


def test_extract_future_with_future_imports() -> None:
    """Test extracting future imports from mixed imports."""
    imports = Imports()
    imports.append(Import(from_="__future__", import_="annotations"))
    imports.append(Import(from_="typing", import_="Optional"))

    future = imports.extract_future()

    assert str(future) == "from __future__ import annotations"
    assert str(imports) == "from typing import Optional"
    assert "__future__" not in imports


def test_extract_future_no_future_imports() -> None:
    """Test extracting from imports without future imports."""
    imports = Imports()
    imports.append(Import(from_="typing", import_="Optional"))

    future = imports.extract_future()

    assert not str(future)
    assert str(imports) == "from typing import Optional"


def test_extract_future_only_future_imports() -> None:
    """Test extracting when only future imports exist."""
    imports = Imports()
    imports.append(Import(from_="__future__", import_="annotations"))

    future = imports.extract_future()

    assert str(future) == "from __future__ import annotations"
    assert not str(imports)


def test_extract_future_with_alias() -> None:
    """Test extracting future imports with alias (edge case)."""
    imports = Imports()
    imports.append(Import(from_="__future__", import_="annotations", alias="ann"))
    imports.append(Import(from_="typing", import_="Optional"))

    future = imports.extract_future()

    assert "annotations as ann" in str(future)
    assert "__future__" not in imports
    assert "__future__" not in imports.alias


def test_extract_future_moves_dual_binding_entry() -> None:
    """extract_future migrates a dual-binding entry to the extracted Imports, leaving others behind."""
    imports = Imports()
    imports.append([
        Import(from_="__future__", import_="annotations"),
        Import(from_="__future__", import_="annotations", alias="ann", keep_unaliased=True),
        Import(from_="collections.abc", import_="Mapping"),
        Import(from_="collections.abc", import_="Mapping", alias="_Mapping", keep_unaliased=True),
    ])

    future = imports.extract_future()

    assert str(future) == "from __future__ import annotations, annotations as ann"
    assert str(imports) == "from collections.abc import Mapping, Mapping as _Mapping"
    assert "__future__" not in imports


def test_remove_nonexistent_import() -> None:
    """Test that removing non-existent import doesn't crash."""
    imports = Imports()
    imports.append(Import(from_="typing", import_="Optional"))

    imports.remove(Import(from_="typing", import_="List"))

    assert str(imports) == "from typing import Optional"


def test_remove_double_removal() -> None:
    """Test that double removal of same import doesn't crash."""
    imports = Imports()
    imports.append(Import(from_="typing", import_="Optional"))

    imports.remove(Import(from_="typing", import_="Optional"))
    imports.remove(Import(from_="typing", import_="Optional"))

    assert not str(imports)


def test_remove_cleans_up_counter() -> None:
    """Test that remove() properly cleans up counter entries."""
    imports = Imports()
    imports.append(Import(from_="typing", import_="Optional"))

    assert imports.counter.get(("typing", "Optional")) == 1

    imports.remove(Import(from_="typing", import_="Optional"))

    assert ("typing", "Optional") not in imports.counter


def test_remove_cleans_up_reference_paths() -> None:
    """Test that remove() properly cleans up reference_paths."""
    imports = Imports()
    imports.append(Import(from_="typing", import_="Optional", reference_path="/test/path"))

    assert "/test/path" in imports.reference_paths

    imports.remove(Import(from_="typing", import_="Optional", reference_path="/test/path"))

    assert "/test/path" not in imports.reference_paths


def test_remap_modules_preserves_aliases_and_reference_paths() -> None:
    """Keep import bookkeeping consistent when remapping one of two referenced aliases."""
    imports = Imports()
    model_import = Import(from_="source", import_="Model", alias="Model_1", reference_path="/model")
    other_import = Import(from_="source", import_="Other", alias="Other_1", reference_path="/other")
    imports.append([model_import, other_import])

    imports.remap_modules({"Model": "target"})

    assert str(imports) == "from source import Other as Other_1\nfrom target import Model as Model_1"
    assert imports.counter == {("source", "Other"): 1, ("target", "Model"): 1}
    assert imports.alias == {"source": {"Other": "Other_1"}, "target": {"Model": "Model_1"}}
    assert imports.reference_paths == {
        "/model": Import(from_="target", import_="Model", alias="Model_1", reference_path="/model"),
        "/other": other_import,
    }

    imports.remove_referenced_imports("/model")

    assert str(imports) == "from source import Other as Other_1"
    assert ("target", "Model") not in imports.counter
    assert "/model" not in imports.reference_paths


def test_remap_modules_moves_dual_binding_entry() -> None:
    """remap_modules migrates a dual-binding entry to the remapped module key."""
    imports = Imports()
    imports.append([
        Import(from_="collections.abc", import_="Mapping"),
        Import(from_="collections.abc", import_="Mapping", alias="_Mapping", keep_unaliased=True),
    ])

    imports.remap_modules({"Mapping": "my_project.compat"})

    assert str(imports) == "from my_project.compat import Mapping, Mapping as _Mapping"


def test_remap_modules_rejects_conflicting_aliases_without_mutation() -> None:
    """Reject overrides that would collapse distinct effective names."""
    imports = Imports()
    imports.append([
        Import(from_="source_a", import_="Model", alias="Model_1"),
        Import(from_="source_b", import_="Model", alias="Model_2"),
    ])
    original = str(imports)
    original_counter = imports.counter.copy()
    original_aliases = {module: aliases.copy() for module, aliases in imports.alias.items()}

    with pytest.raises(
        ValueError,
        match="Import override for 'Model' produces conflicting names: 'Model_1' and 'Model_2'",
    ):
        imports.remap_modules({"Model": "target"})

    assert str(imports) == original
    assert imports.counter == original_counter
    assert imports.alias == original_aliases


def test_remap_modules_preserves_future_reference_paths() -> None:
    """Keep future imports and their reference metadata outside override handling."""
    imports = Imports()
    future_import = Import(
        from_="__future__",
        import_="annotations",
        reference_path="/future/annotations",
    )
    imports.append(future_import)

    imports.remap_modules({"annotations": "custom.future"})
    future_imports = imports.extract_future()

    assert str(future_imports) == "from __future__ import annotations"
    assert future_imports.reference_paths == {"/future/annotations": future_import}
    assert not imports.reference_paths


def test_remove_dotted_import_decrements_counter_and_cleans_reference_paths() -> None:
    """Dotted imports keep their public counters and reference paths consistent."""
    imports = Imports()
    first_import = Import(from_=None, import_="collections.abc", reference_path="/first/path")
    second_import = Import(from_=None, import_="collections.abc", reference_path="/second/path")
    imports.append([first_import, second_import])

    assert str(imports) == "import collections.abc"
    assert imports.counter[None, "collections.abc"] == 2
    assert imports.reference_paths == {
        "/first/path": first_import,
        "/second/path": second_import,
    }

    imports.remove(first_import)

    assert str(imports) == "import collections.abc"
    assert imports.counter[None, "collections.abc"] == 1
    assert "/first/path" not in imports.reference_paths
    assert imports.reference_paths["/second/path"] is second_import

    imports.remove(second_import)

    assert not str(imports)
    assert (None, "collections.abc") not in imports.counter
    assert None not in imports
    assert "/second/path" not in imports.reference_paths


def test_remove_missing_dotted_import_is_noop() -> None:
    """Removing a dotted import that was never added leaves imports unchanged."""
    imports = Imports()
    imports.append(Import(from_=None, import_="collections.abc"))

    imports.remove(Import(from_=None, import_="pathlib.Path"))

    assert str(imports) == "import collections.abc"
    assert imports.counter[None, "collections.abc"] == 1


def test_remove_dotted_import_keeps_bucket_when_other_dotted_imports_remain() -> None:
    """Removing one dotted import keeps the import bucket when another remains."""
    imports = Imports()
    first_import = Import(from_=None, import_="collections.abc")
    second_import = Import(from_=None, import_="pathlib.Path")
    imports.append([first_import, second_import])

    imports.remove(first_import)

    assert str(imports) == "import pathlib.Path"
    assert imports[None] == {"pathlib.Path"}
    assert (None, "collections.abc") not in imports.counter
    assert imports.counter[None, "pathlib.Path"] == 1


def test_remove_unused_dotted_import_uses_bound_package_name() -> None:
    """A dotted import binds its top-level package rather than its final segment."""
    imports = Imports()
    imports.append(Import(from_=None, import_="package.submodule"))

    imports.remove_unused({"package"})

    assert str(imports) == "import package.submodule"
    imports.remove_unused({"submodule"})
    assert not str(imports)


def test_remove_dotted_import_without_bucket_cleans_reference_path() -> None:
    """Defensive dotted import removal still clears counters and reference paths."""
    imports = Imports()
    import_ = Import(from_=None, import_="collections.abc", reference_path="/stale/path")
    imports.counter[None, "collections.abc"] = 1
    imports.reference_paths["/stale/path"] = import_

    imports.remove(import_)

    assert not str(imports)
    assert (None, "collections.abc") not in imports.counter
    assert "/stale/path" not in imports.reference_paths


def test_plain_from_none_alias_cleanup_after_remove_and_remove_unused() -> None:
    """Plain imports without from_ still record and clean aliases."""
    imports = Imports()
    aliased_import = Import(from_=None, import_="datetime", alias="datetime_module", reference_path="/datetime")
    imports.append(aliased_import)

    assert str(imports) == "import datetime as datetime_module"
    assert imports[None] == {"datetime"}
    assert imports.alias[None] == {"datetime": "datetime_module"}
    assert imports.counter[None, "datetime"] == 1
    assert imports.reference_paths["/datetime"] is aliased_import

    imports.remove(aliased_import)

    assert not str(imports)
    assert None not in imports
    assert None not in imports.alias
    assert (None, "datetime") not in imports.counter
    assert "/datetime" not in imports.reference_paths

    imports.append(aliased_import)
    imports.remove_unused(set())

    assert not str(imports)
    assert None not in imports
    assert None not in imports.alias
    assert (None, "datetime") not in imports.counter
    assert "/datetime" not in imports.reference_paths

    imports.append(Import(from_=None, import_="datetime"))

    assert str(imports) == "import datetime"


def test_dotted_direct_import_alias_lifecycle() -> None:
    """Dotted direct imports retain aliases through use tracking and removal."""
    imports = Imports()
    aliased_import = Import(import_="pkg.models", alias="pkg_1", reference_path="/pkg/models")
    imports.append(aliased_import)

    assert str(imports) == "import pkg.models as pkg_1"
    assert imports.get_effective_name(None, "pkg.models") == "pkg_1"
    imports.remove_unused({"pkg_1"})
    assert str(imports) == "import pkg.models as pkg_1"

    imports.remove(aliased_import)

    assert not str(imports)
    assert None not in imports.alias
    assert (None, "pkg.models") not in imports.counter
    assert "/pkg/models" not in imports.reference_paths


@pytest.mark.parametrize(
    ("aliased_import", "plain_import", "expected"),
    [
        (
            Import(import_="pkg.models", alias="pkg_1"),
            Import(import_="pkg.models"),
            "import pkg.models",
        ),
        (
            Import(from_="foo", import_="Bar", alias="Bar_1"),
            Import(from_="foo", import_="Bar"),
            "from foo import Bar",
        ),
    ],
)
def test_last_unaliased_remove_clears_existing_alias(
    aliased_import: Import,
    plain_import: Import,
    expected: str,
) -> None:
    """Alias state belongs to the identity and expires with its final reference."""
    imports = Imports()
    imports.append([aliased_import, plain_import])

    imports.remove(aliased_import)
    imports.remove(plain_import)
    imports.append(plain_import)

    assert str(imports) == expected


def test_dump_emits_both_bindings_for_keep_unaliased_import() -> None:
    """`keep_unaliased=True` binds both spellings when the bare name is also imported elsewhere."""
    imports = Imports()
    imports.append([
        Import(from_="collections.abc", import_="Mapping"),
        Import(from_="collections.abc", import_="Mapping", alias="_Mapping", keep_unaliased=True),
    ])

    assert str(imports) == "from collections.abc import Mapping, Mapping as _Mapping"

    imports.remove(Import(from_="collections.abc", import_="Mapping"))

    assert str(imports) == "from collections.abc import Mapping as _Mapping"


def test_remove_aliased_keep_unaliased_import_clears_alias_and_dual_binding() -> None:
    """Removing the aliased half of a dual-binding pair drops the alias, not just its own count."""
    imports = Imports()
    imports.append([
        Import(from_="collections.abc", import_="Mapping"),
        Import(from_="collections.abc", import_="Mapping", alias="_Mapping", keep_unaliased=True),
    ])

    imports.remove(Import(from_="collections.abc", import_="Mapping", alias="_Mapping", keep_unaliased=True))

    assert str(imports) == "from collections.abc import Mapping"
    assert "Mapping" not in imports.alias["collections.abc"]


def test_remove_one_of_two_keep_unaliased_registrations_keeps_dual_binding() -> None:
    """dual_binding survives while another keep_unaliased registration for the same key remains."""
    imports = Imports()
    bare = Import(from_="collections.abc", import_="Mapping")
    first = Import(from_="collections.abc", import_="Mapping", alias="_Mapping", keep_unaliased=True)
    second = Import(from_="collections.abc", import_="Mapping", alias="_Mapping", keep_unaliased=True)
    imports.append([bare, first, second])

    imports.remove(first)

    assert str(imports) == "from collections.abc import Mapping, Mapping as _Mapping"

    imports.remove(second)

    assert str(imports) == "from collections.abc import Mapping"


def test_reuse_key_after_complete_removal_of_dual_binding_import() -> None:
    """A key fully removed and re-imported bare does not resurrect its former alias or dual binding."""
    imports = Imports()
    pair = [
        Import(from_="collections.abc", import_="Mapping"),
        Import(from_="collections.abc", import_="Mapping", alias="_Mapping", keep_unaliased=True),
    ]
    imports.append(pair)
    imports.remove(pair)

    assert not imports.alias["collections.abc"]

    imports.append(Import(from_="collections.abc", import_="Mapping"))

    assert str(imports) == "from collections.abc import Mapping"


def test_dump_keeps_single_binding_without_keep_unaliased() -> None:
    """An aliased import without `keep_unaliased` still collapses a same-name unaliased request."""
    imports = Imports()
    imports.append([
        Import(from_="collections.abc", import_="Mapping"),
        Import(from_="collections.abc", import_="Mapping", alias="_Mapping"),
    ])

    assert str(imports) == "from collections.abc import Mapping as _Mapping"


def test_dump_all_uses_names_actually_bound_by_dotted_imports() -> None:
    """Exports use the package root unless a direct dotted import has an alias."""
    imports = Imports()
    imports.append([
        Import(import_="pkg.models"),
        Import(import_="other.models", alias="models_alias"),
        Import(from_="foo", import_="Bar"),
    ])
    imports.add_export("Local")

    assert imports.dump_all() == '__all__ = ["Bar", "Local", "models_alias", "pkg"]'


def test_apply_alias_is_identity_scoped() -> None:
    """Module-local aliasing updates only an exact tracked identity."""
    imports = Imports()
    imports.apply_alias(Import(from_="missing", import_="Thing", alias="Thing_1"))
    imports.append([
        Import(from_="foo", import_="Bar"),
        Import(from_="other", import_="Value"),
    ])
    imports.apply_alias(Import(from_="foo", import_="Bar"))
    imports.apply_alias(Import(from_="foo", import_="Bar", alias="Bar_1"))

    assert str(imports) == "from foo import Bar as Bar_1\nfrom other import Value"


def test_extract_future_moves_reference_paths() -> None:
    """Test that extract_future() moves reference_paths for __future__ imports."""
    imports = Imports()
    imports.append(Import(from_="__future__", import_="annotations", reference_path="/future/ref"))
    imports.append(Import(from_="typing", import_="Optional", reference_path="/typing/ref"))

    future = imports.extract_future()

    assert "/future/ref" in future.reference_paths
    assert "/future/ref" not in imports.reference_paths
    assert "/typing/ref" in imports.reference_paths
    assert "/typing/ref" not in future.reference_paths


def test_remove_unused_clears_applied_alias() -> None:
    """An alias applied after registration must not survive removal of its import."""
    imports = Imports()
    plain = Import(from_="foo", import_="Bar")
    imports.append(plain)
    imports.apply_alias(Import(from_="foo", import_="Bar", alias="BarAlias"))
    imports.remove_unused(set())
    imports.append(plain)

    assert str(imports) == "from foo import Bar"


def test_independent_alias_lifecycle() -> None:
    """Remapping and removing a helper leaves the ordinary binding independent."""
    imports = Imports()
    imports.append([
        Import(from_="foo", import_="Bar"),
        Import(from_="foo", import_="Bar", alias="HelperBar", keep_unaliased=True, reference_path="/helper"),
    ])
    imports.apply_alias(Import(from_="foo", import_="Bar", alias="FieldBar"))
    imports.remap_modules({"Bar": "other"})

    assert str(imports) == "from other import Bar as FieldBar, Bar as HelperBar"
    assert imports.dump_all() == '__all__ = ["FieldBar", "HelperBar"]'
    assert imports.reference_paths["/helper"].keep_unaliased

    imports.remove_referenced_imports("/helper")
    assert str(imports) == "from other import Bar as FieldBar"
    imports.remove_unused(set())
    imports.append(Import(from_="other", import_="Bar"))
    assert str(imports) == "from other import Bar"


@pytest.mark.parametrize("used", ["Bar", "HelperBar"])
def test_remove_unused_independent_alias(used: str) -> None:
    """Prune the unused binding while retaining the one generated code references."""
    imports = Imports()
    imports.append([
        Import(from_="foo", import_="Bar"),
        Import(from_="foo", import_="Bar", alias="HelperBar", keep_unaliased=True),
    ])
    imports.remove_unused({used})

    assert str(imports) == ("from foo import Bar" if used == "Bar" else "from foo import Bar as HelperBar")
