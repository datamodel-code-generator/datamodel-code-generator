"""Tests for field name resolver functionality."""

from __future__ import annotations

import copy
import os
import pickle
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import cast

import pytest

from datamodel_code_generator.reference import FieldNameResolver, ModelResolver, ModelType, PydanticFieldNameResolver
from tests.conftest import assert_output
from tests.data.python.model_resolver_pickle_types import (
    GLOBAL_REDUCE_MODEL_RESOLVER,
    CustomDictStateModelResolver,
    CustomStateModelResolver,
    SlottedModelResolver,
    TupleReduceModelResolver,
)

EXPECTED_RESOLVER_PATH = Path(__file__).parent / "data" / "expected" / "resolver"
MODEL_RESOLVER_PICKLE_HELPER = Path(__file__).parent / "data" / "python" / "model_resolver_pickle_compat.py"
MODEL_RESOLVER_MAIN_PICKLE = Path(__file__).parent / "data" / "python" / "model_resolver_main_protocol4.pickle.b64"
MODEL_RESOLVER_SLOTTED_MAIN_PICKLE = (
    Path(__file__).parent / "data" / "python" / "model_resolver_slotted_main_protocol4.pickle.b64"
)


@pytest.mark.parametrize(
    ("name", "expected_resolved"),
    [
        ("3a", "field_3a"),
        ("$in", "field_in"),
        ("field", "field"),
        ("ำ62", "field_ue33_62"),
    ],
)
def test_get_valid_field_name(name: str, expected_resolved: str) -> None:
    """Test field name resolution to valid Python identifiers."""
    resolver = FieldNameResolver()
    assert expected_resolved == resolver.get_valid_name(name)


def test_model_resolver_default_field_policy_is_lazy_and_compatible() -> None:
    """Load output-owned defaults only when their legacy model type is requested."""
    resolver = ModelResolver()

    assert resolver.get_valid_field_name("schema") == "schema_"
    assert resolver.get_valid_field_name("schema", model_type=ModelType.CLASS) == "schema"
    assert resolver.get_valid_field_name("schema", model_type=ModelType.PYDANTIC) == "schema_"
    assert resolver.get_valid_field_name("field", model_type=ModelType.MSGSPEC) == "field_"
    assert resolver.get_valid_field_name("mro", model_type=ModelType.ENUM) == "mro_"
    field_name_resolvers = resolver.field_name_resolvers
    assert type(field_name_resolvers) is dict
    assert list(field_name_resolvers) == [ModelType.ENUM, ModelType.PYDANTIC, ModelType.CLASS, ModelType.MSGSPEC]
    assert resolver.field_name_resolvers is field_name_resolvers


def test_output_field_name_resolver_legacy_exports_are_lazy_aliases() -> None:
    """Keep existing direct resolver imports compatible after moving ownership."""
    from datamodel_code_generator.model.msgspec import MsgspecFieldNameResolver as OutputMsgspecFieldNameResolver
    from datamodel_code_generator.reference import DEFAULT_FIELD_NAME_RESOLVERS, MsgspecFieldNameResolver

    reference_module = sys.modules["datamodel_code_generator.reference"]
    assert MsgspecFieldNameResolver is OutputMsgspecFieldNameResolver
    assert isinstance(DEFAULT_FIELD_NAME_RESOLVERS, dict)
    assert {
        ModelType.ENUM: type(ModelResolver().field_name_resolvers[ModelType.ENUM]),
        ModelType.PYDANTIC: PydanticFieldNameResolver,
        ModelType.CLASS: FieldNameResolver,
        ModelType.MSGSPEC: OutputMsgspecFieldNameResolver,
    } == DEFAULT_FIELD_NAME_RESOLVERS
    namespace: dict[str, object] = {}
    exec("from datamodel_code_generator.reference import *", namespace)
    legacy_names = {"PydanticFieldNameResolver", "MsgspecFieldNameResolver", "DEFAULT_FIELD_NAME_RESOLVERS"}
    assert legacy_names <= set(dir(reference_module))
    assert legacy_names <= namespace.keys()


def test_model_resolver_preserves_mutable_legacy_default_registry() -> None:
    """Honor the resolver registry snapshot used by existing extensions."""
    from datamodel_code_generator.reference import DEFAULT_FIELD_NAME_RESOLVERS

    class CustomFieldNameResolver(FieldNameResolver):
        pass

    original_resolver_class = DEFAULT_FIELD_NAME_RESOLVERS[ModelType.CLASS]
    DEFAULT_FIELD_NAME_RESOLVERS[ModelType.CLASS] = CustomFieldNameResolver
    try:
        resolver = ModelResolver()
    finally:
        DEFAULT_FIELD_NAME_RESOLVERS[ModelType.CLASS] = original_resolver_class

    assert type(resolver.field_name_resolvers[ModelType.CLASS]) is CustomFieldNameResolver


def test_model_resolver_preserves_removed_legacy_default() -> None:
    """Keep missing registry entries missing in resolver snapshots."""
    from datamodel_code_generator.reference import DEFAULT_FIELD_NAME_RESOLVERS

    original_resolver_classes = DEFAULT_FIELD_NAME_RESOLVERS.copy()
    del DEFAULT_FIELD_NAME_RESOLVERS[ModelType.CLASS]
    try:
        resolver = ModelResolver()
        with pytest.raises(KeyError, match="CLASS"):
            resolver.default_class_name_generator("name")
    finally:
        DEFAULT_FIELD_NAME_RESOLVERS.clear()
        DEFAULT_FIELD_NAME_RESOLVERS.update(original_resolver_classes)


def test_model_resolver_lazy_field_aliases_are_isolated_from_input() -> None:
    """Preserve eager resolver isolation while constructing resolvers lazily."""
    aliases = {"schema": "schema_name"}
    resolver = ModelResolver(aliases=aliases)
    aliases.clear()

    assert resolver.get_valid_field_name_and_alias("schema") == ("schema_name", "schema")


def test_model_resolver_public_field_mapping_preserves_independent_instances_and_replacement() -> None:
    """Retain the mutable plain-dict compatibility surface outside the fast path."""
    resolver = ModelResolver(
        field_name_resolver_classes={
            ModelType.PYDANTIC: FieldNameResolver,
            ModelType.CLASS: FieldNameResolver,
        }
    )
    field_name_resolvers = resolver.field_name_resolvers

    assert field_name_resolvers[ModelType.PYDANTIC] is not field_name_resolvers[ModelType.CLASS]
    replacement = {ModelType.CLASS: FieldNameResolver(special_field_name_prefix="replacement")}
    resolver.field_name_resolvers = replacement
    assert resolver.field_name_resolvers is replacement
    assert resolver.default_class_name_generator("3name") == "Replacement3name"


def test_model_resolver_output_providers_load_only_for_compatibility_access() -> None:
    """Exercise the lazy fast path and the full legacy mapping in a fresh process."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "from datamodel_code_generator.reference import ModelResolver, ModelType; "
                "modules = ('datamodel_code_generator.model.pydantic_v2.base_model', "
                "'datamodel_code_generator.model.msgspec'); "
                "resolver = ModelResolver(); "
                "print(*(module in sys.modules for module in modules)); "
                "print(resolver.get_valid_field_name('value', model_type=ModelType.CLASS)); "
                "print(*(module in sys.modules for module in modules)); "
                "mapping = resolver.field_name_resolvers; "
                "print(type(mapping).__name__, *(model_type.name for model_type in mapping)); "
                "print(*(module in sys.modules for module in modules)); "
                "print(*(type(mapping[model_type]).__module__ for model_type in "
                "(ModelType.PYDANTIC, ModelType.MSGSPEC))); "
                "import pickle; "
                "print(*(type(pickle.loads(pickle.dumps(mapping[model_type]))).__name__ for model_type in "
                "(ModelType.PYDANTIC, ModelType.MSGSPEC))); "
                "import datamodel_code_generator.reference as reference; "
                "namespace = {}; exec('from datamodel_code_generator.reference import *', namespace); "
                "legacy_names = ('PydanticFieldNameResolver', 'MsgspecFieldNameResolver', "
                "'DEFAULT_FIELD_NAME_RESOLVERS'); "
                "print(*(name in dir(reference) for name in legacy_names)); "
                "print(*(name in namespace for name in legacy_names))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert_output(result.stdout, EXPECTED_RESOLVER_PATH / "field_name_resolver_laziness.txt")


def test_model_resolver_pickles_are_compatible_across_the_resolver_state_rename(  # noqa: PLR0914
    tmp_path: Path,
) -> None:
    """Load a main-branch pickle and expose current pickles to the earlier public layout."""
    resolver = ModelResolver()
    shallow_copy = copy.copy(resolver)
    deep_copy = copy.deepcopy(resolver)
    shallow_copy.field_name_resolvers[ModelType.CLASS] = FieldNameResolver(special_field_name_prefix="current")
    current_pickle_path = tmp_path / "model-resolver-current.pickle"
    current_pickle_path.write_bytes(pickle.dumps(resolver, protocol=4))

    replacement_resolver = ModelResolver()
    replacement_resolver.field_name_resolvers = {
        ModelType.CLASS: FieldNameResolver(special_field_name_prefix="replacement")
    }
    replacement_shallow_copy = copy.copy(replacement_resolver)
    replacement_deep_copy = copy.deepcopy(replacement_resolver)
    replacement_pickle_copy = pickle.loads(pickle.dumps(replacement_resolver, protocol=4))
    keyed_resolver = ModelResolver()
    keyed_reference = keyed_resolver.add(["#/definitions/Pet"], "Pet")
    restored_keyed_reference = pickle.loads(pickle.dumps(keyed_resolver, protocol=4)).references[keyed_reference.path]
    restored_reference_state, reference_state = vars(restored_keyed_reference).copy(), vars(keyed_reference).copy()
    new_model_key = restored_reference_state.pop("_model_key") != reference_state.pop("_model_key")

    slotted_resolver = SlottedModelResolver()
    slotted_resolver.slot_marker = "current-slot"
    current_slotted_pickle_path = tmp_path / "model-resolver-slotted-current.pickle"
    current_slotted_pickle = pickle.dumps(slotted_resolver, protocol=4)
    current_slotted_pickle_path.write_bytes(current_slotted_pickle)
    restored_slotted_resolver = pickle.loads(current_slotted_pickle)
    restored_custom_state_resolver = pickle.loads(pickle.dumps(CustomStateModelResolver(), protocol=4))
    restored_custom_dict_state_resolver = pickle.loads(pickle.dumps(CustomDictStateModelResolver(), protocol=4))
    restored_global_resolver = pickle.loads(pickle.dumps(GLOBAL_REDUCE_MODEL_RESOLVER, protocol=4))
    restored_tuple_reduce_resolver = pickle.loads(pickle.dumps(TupleReduceModelResolver(), protocol=4))
    private_state_resolver = ModelResolver.__new__(ModelResolver)
    private_state_resolver.__setstate__(ModelResolver().__dict__.copy())
    legacy_base_path_state = ModelResolver(base_path=tmp_path).__dict__.copy()
    del legacy_base_path_state["_resolved_base_path_cache"]
    legacy_base_path_resolver = ModelResolver.__new__(ModelResolver)
    legacy_base_path_resolver.__setstate__(legacy_base_path_state)
    legacy_base_path_ref = legacy_base_path_resolver.resolve_ref("schema.json#/value")

    outputs = [
        (
            "current copy semantics\n"
            f"shallow mapping shared: {shallow_copy.field_name_resolvers is resolver.field_name_resolvers}\n"
            f"deep mapping shared: {deep_copy.field_name_resolvers is resolver.field_name_resolvers}\n"
            f"shallow mutation shared: {resolver.get_valid_field_name('3name', model_type=ModelType.CLASS)}\n"
            f"deep copy isolated: {deep_copy.get_valid_field_name('3name', model_type=ModelType.CLASS)}\n"
            "replacement shallow shared: "
            f"{replacement_shallow_copy.field_name_resolvers is replacement_resolver.field_name_resolvers}\n"
            "replacement deep shared: "
            f"{replacement_deep_copy.field_name_resolvers is replacement_resolver.field_name_resolvers}\n"
            "replacement pickle shared: "
            f"{replacement_pickle_copy.field_name_resolvers is replacement_resolver.field_name_resolvers}\n"
            "replacement pickle field: "
            f"{replacement_pickle_copy.get_valid_field_name('3name', model_type=ModelType.CLASS)}\n"
            f"reference pickle keeps state: {restored_reference_state == reference_state}\n"
            f"reference pickle takes a new model key: {new_model_key}\n"
            "current slotted roundtrip\n"
            f"state layout: {'private' if '_field_name_resolvers' in vars(restored_slotted_resolver) else 'public'}\n"
            f"slot marker: {restored_slotted_resolver.slot_marker}\n"
            f"custom state: {restored_custom_state_resolver.restored_state}\n"
            f"custom dict state: {restored_custom_dict_state_resolver.restored_state}\n"
            f"global reduce identity: {restored_global_resolver is GLOBAL_REDUCE_MODEL_RESOLVER}\n"
            f"tuple reduce type: {type(restored_tuple_reduce_resolver).__name__}\n"
            "private state accepted: "
            f"{private_state_resolver.get_valid_field_name('3name', model_type=ModelType.CLASS)}\n"
            f"legacy base cache ref: {legacy_base_path_ref}\n"
        )
    ]
    for direction, pickle_path in (
        ("main-to-current", MODEL_RESOLVER_MAIN_PICKLE),
        ("main-slotted-to-current", MODEL_RESOLVER_SLOTTED_MAIN_PICKLE),
        ("current-to-main", current_pickle_path),
        ("current-slotted-to-main", current_slotted_pickle_path),
    ):
        result = subprocess.run(
            [sys.executable, str(MODEL_RESOLVER_PICKLE_HELPER), direction, str(pickle_path)],
            check=True,
            capture_output=True,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).parent.parent)},
            text=True,
        )
        outputs.append(result.stdout)

    assert_output("".join(outputs), EXPECTED_RESOLVER_PATH / "model_resolver_pickle_compatibility.txt")


def test_model_resolver_reference_name_cache_preserves_multiplicity() -> None:
    """Keep a name reserved until every non-unique reference releases it."""
    delete_resolver = ModelResolver()
    delete_resolver.add(["first"], "User", class_name=True, unique=False)
    delete_resolver.add(["second"], "User", class_name=True, unique=False)
    delete_resolver.add(["third"], "User", class_name=True, unique=False)
    delete_duplicate_count = str(delete_resolver._reference_names_cache)
    delete_resolver.delete(["first"])
    delete_resolver.delete(["second"])
    delete_allocated = delete_resolver.add(["fourth"], "User", class_name=True)

    rename_resolver = ModelResolver()
    rename_resolver.add(["first"], "User", class_name=True, unique=False)
    rename_resolver.add(["second"], "User", class_name=True, unique=False)
    rename_resolver.add(["first"], "Admin", class_name=True, unique=False)
    rename_allocated = rename_resolver.add(["third"], "User", class_name=True)

    refresh_resolver = ModelResolver()
    refresh_resolver.add(["first"], "User", class_name=True, unique=False)
    refresh_resolver.add(["second"], "User", class_name=True, unique=False)
    refresh_resolver.refresh_reference_names()
    refresh_resolver.delete(["first"])

    same_name_resolver = ModelResolver()
    same_name_resolver.add(["entry"], "user-name", class_name=True)
    same_name_resolver.add(["entry"], "user_name", class_name=True)
    same_name_resolver.refresh_reference_names()
    same_name_resolver.delete(["entry"])
    same_name_allocated = same_name_resolver.add(["next"], "user_name", class_name=True)

    legacy_cache_resolver = ModelResolver()
    legacy_cache_resolver.add(["first"], "User", class_name=True, unique=False)
    legacy_cache_resolver.add(["second"], "User", class_name=True, unique=False)
    legacy_state = legacy_cache_resolver.__dict__.copy()
    legacy_state["_reference_names_cache"] = {"User"}
    restored_resolver = ModelResolver.__new__(ModelResolver)
    restored_resolver.__setstate__(legacy_state)
    restored_resolver.delete(["first"])
    legacy_allocated = restored_resolver.add(["third"], "User", class_name=True)

    counter_state = legacy_cache_resolver.__dict__.copy()
    counter_state["_reference_names_cache"] = Counter(User=2)
    counter_restored_resolver = ModelResolver.__new__(ModelResolver)
    counter_restored_resolver.__setstate__(counter_state)
    counter_restored_resolver.delete(["first"])
    counter_allocated = counter_restored_resolver.add(["third"], "User", class_name=True)

    assert_output(
        "".join((
            f"delete retained: {delete_resolver.references['third#'].name}\n",
            f"delete allocated: {delete_allocated.name}\n",
            f"duplicate count: {delete_duplicate_count}\n",
            f"single count: {delete_resolver._reference_names_cache}\n",
            f"rename retained: {rename_resolver.references['second#'].name}\n",
            f"rename allocated: {rename_allocated.name}\n",
            f"refresh allocated: {refresh_resolver.add(['third'], 'User', class_name=True).name}\n",
            f"same-name reallocated: {same_name_allocated.name}\n",
            f"legacy cache allocated: {legacy_allocated.name}\n",
            f"counter cache allocated: {counter_allocated.name}\n",
        )),
        EXPECTED_RESOLVER_PATH / "reference_name_multiplicity.txt",
    )


@pytest.mark.parametrize(
    (
        "name",
        "excludes",
        "snake_case_field",
        "capitalise_enum_members",
        "ignore_snake_case_field",
        "upper_camel",
    ),
    [
        pytest.param("account_id", None, False, False, False, False, id="snake-case"),
        pytest.param("displayName", None, False, False, False, False, id="camel-case"),
        pytest.param("version2", None, False, False, False, False, id="trailing-number"),
        pytest.param("café", None, False, False, False, False, id="non-ascii-identifier"),
        pytest.param("json", None, False, False, False, False, id="pydantic-reserved"),
        pytest.param("display-name", None, False, False, False, False, id="non-identifier"),
        pytest.param("_private", None, False, False, False, False, id="private-name"),
        pytest.param("class", None, False, False, False, False, id="keyword"),
        pytest.param("account_id", {"account_id"}, False, False, False, False, id="excluded"),
        pytest.param("displayName", None, True, False, False, False, id="snake-case-conversion"),
        pytest.param("displayName", None, True, False, True, False, id="ignore-snake-case"),
        pytest.param("account_id", None, False, True, False, False, id="enum-capitalization"),
        pytest.param("account_id", None, False, False, False, True, id="upper-camel"),
    ],
)
def test_pydantic_field_name_fast_path_matches_conventional_resolution(
    name: str,
    excludes: set[str] | None,
    snake_case_field: bool,
    capitalise_enum_members: bool,
    ignore_snake_case_field: bool,
    upper_camel: bool,
) -> None:
    """Keep the built-in fast path equivalent to the conventional resolver."""
    resolver = PydanticFieldNameResolver(
        snake_case_field=snake_case_field,
        capitalise_enum_members=capitalise_enum_members,
    )
    conventional_resolver = _ConventionalPydanticFieldNameResolver(
        snake_case_field=snake_case_field,
        capitalise_enum_members=capitalise_enum_members,
    )
    assert resolver.get_valid_name(
        name,
        excludes=excludes,
        ignore_snake_case_field=ignore_snake_case_field,
        upper_camel=upper_camel,
    ) == conventional_resolver.get_valid_name(
        name,
        excludes=excludes,
        ignore_snake_case_field=ignore_snake_case_field,
        upper_camel=upper_camel,
    )


class _ConventionalPydanticFieldNameResolver(PydanticFieldNameResolver):
    """Use the conventional path to compare against the exact built-in resolver."""


class _CustomPydanticFieldNameResolver(PydanticFieldNameResolver):
    """Record validation calls made by the extension-compatible path."""

    def __init__(self) -> None:
        super().__init__()
        self.validation_calls = 0

    def _validate_field_name(self, field_name: str) -> bool:
        del field_name
        self.validation_calls += 1
        return True


def test_pydantic_field_name_fast_path_preserves_subclass_validation_behavior() -> None:
    """Keep custom Pydantic resolvers on the existing validation path."""
    resolver = _CustomPydanticFieldNameResolver()

    assert resolver.get_valid_name("account_id") == "account_id"
    assert resolver.validation_calls == 2


def test_get_valid_field_name_alias_for_unicode_ncname() -> None:
    """Keep the source name as an alias when XML NCName is not a Python identifier."""
    resolver = FieldNameResolver()
    field_name, alias = resolver.get_valid_field_name_and_alias("ำ62")
    assert field_name == "field_ue33_62"
    assert alias == "ำ62"


def test_get_valid_field_name_upper_camel_unicode_ncname() -> None:
    """Apply stable fallback names before upper-camel conversion."""
    resolver = FieldNameResolver()
    assert resolver.get_valid_name("ำ62", upper_camel=True) == "FieldUe3362"


def test_get_valid_field_name_enum_unicode_ncname() -> None:
    """Apply stable fallback names before enum member capitalization."""
    resolver = FieldNameResolver(capitalise_enum_members=True)
    assert resolver.get_valid_name("ำ62") == "FIELD_UE33_62"


def test_ascii_identifier_fallback_prefixes_empty_and_numeric_names() -> None:
    """Generate stable fallback names for values that cannot stand as identifiers."""
    resolver = FieldNameResolver()
    assert resolver._ascii_identifier_fallback("") == "field_"
    assert resolver._ascii_identifier_fallback("1") == "field_1"
    assert resolver._ascii_identifier_fallback("aำ") == "a_ue33_"


def test_hierarchical_flat_alias() -> None:
    """Test traditional flat alias resolution."""
    resolver = FieldNameResolver(aliases={"name": "name_alias"})
    field_name, alias = resolver.get_valid_field_name_and_alias("name")
    assert field_name == "name_alias"
    assert alias == "name"


def test_hierarchical_scoped_alias() -> None:
    """Test scoped alias resolution (ClassName.field)."""
    resolver = FieldNameResolver(
        aliases={
            "User.name": "user_name",
            "Address.name": "address_name",
            "name": "default_name",
        }
    )

    field_name, alias = resolver.get_valid_field_name_and_alias("name", class_name="User")
    assert field_name == "user_name"
    assert alias == "name"

    field_name, alias = resolver.get_valid_field_name_and_alias("name", class_name="Address")
    assert field_name == "address_name"
    assert alias == "name"

    field_name, alias = resolver.get_valid_field_name_and_alias("name", class_name="Other")
    assert field_name == "default_name"
    assert alias == "name"


def test_hierarchical_alias_priority() -> None:
    """Test that scoped aliases have priority over flat aliases."""
    resolver = FieldNameResolver(
        aliases={
            "User.name": "scoped_name",
            "name": "flat_name",
        }
    )

    field_name, _ = resolver.get_valid_field_name_and_alias("name", class_name="User")
    assert field_name == "scoped_name"

    field_name, _ = resolver.get_valid_field_name_and_alias("name", class_name="Other")
    assert field_name == "flat_name"


def test_hierarchical_class_name_provided_but_no_scoped_aliases() -> None:
    """Test when class_name is provided but no scoped aliases are configured."""
    resolver = FieldNameResolver(aliases={"name": "name_alias"})
    field_name, alias = resolver.get_valid_field_name_and_alias("name", class_name="User")
    assert field_name == "name_alias"
    assert alias == "name"


def test_hierarchical_scoped_alias_not_matching() -> None:
    """Test when scoped alias exists but doesn't match current class."""
    resolver = FieldNameResolver(
        aliases={
            "Other.name": "other_name",
            "name": "default_name",
        }
    )
    field_name, alias = resolver.get_valid_field_name_and_alias("name", class_name="User")
    assert field_name == "default_name"
    assert alias == "name"


def test_hierarchical_no_alias_match() -> None:
    """Test that unmatched fields return valid name without alias."""
    resolver = FieldNameResolver(aliases={"other": "other_alias"})
    field_name, alias = resolver.get_valid_field_name_and_alias("name")
    assert field_name == "name"
    assert alias is None


def test_hierarchical_backward_compatibility() -> None:
    """Test that existing flat alias behavior is preserved."""
    resolver = FieldNameResolver(aliases={"name": "name_", "id": "id_"})
    field_name, alias = resolver.get_valid_field_name_and_alias("name")
    assert field_name == "name_"
    assert alias == "name"

    field_name, alias = resolver.get_valid_field_name_and_alias("id")
    assert field_name == "id_"
    assert alias == "id"


def test_hierarchical_dotted_field_name_alias() -> None:
    """Test that field names containing dots can be aliased (backward compat)."""
    resolver = FieldNameResolver(aliases={"filter.name": "filter_name_alias"})
    field_name, alias = resolver.get_valid_field_name_and_alias("filter.name")
    assert field_name == "filter_name_alias"
    assert alias == "filter.name"


def test_hierarchical_dotted_field_name_without_class_name() -> None:
    """Test dotted field name alias works without class_name parameter."""
    resolver = FieldNameResolver(
        aliases={
            "a.b": "a_b_alias",
            "User.name": "user_name",
        }
    )
    field_name, alias = resolver.get_valid_field_name_and_alias("a.b")
    assert field_name == "a_b_alias"
    assert alias == "a.b"


def test_hierarchical_path_parameter_backward_compatibility() -> None:
    """Test that path parameter is accepted but ignored."""
    resolver = FieldNameResolver(aliases={"name": "name_alias"})
    field_name, alias = resolver.get_valid_field_name_and_alias("name", path=["root", "properties", "name"])
    assert field_name == "name_alias"
    assert alias == "name"


def test_multiple_aliases_flat() -> None:
    """Test multiple aliases return list including original field name."""
    resolver = FieldNameResolver(aliases={"my_field": ["my-field", "myField"]})
    field_name, aliases = resolver.get_valid_field_name_and_alias("my_field")
    assert field_name == "my_field"  # First alias validated to valid identifier
    assert aliases == ["my_field", "my-field", "myField"]  # Original + all aliases


def test_multiple_aliases_scoped() -> None:
    """Test multiple aliases with scoped format (ClassName.field)."""
    resolver = FieldNameResolver(
        aliases={
            "User.name": ["user-name", "userName"],
            "name": ["default-name", "defaultName"],
        }
    )

    field_name, aliases = resolver.get_valid_field_name_and_alias("name", class_name="User")
    assert field_name == "user_name"  # Hyphen converted to valid identifier
    assert aliases == ["name", "user-name", "userName"]

    field_name, aliases = resolver.get_valid_field_name_and_alias("name", class_name="Other")
    assert field_name == "default_name"  # Hyphen converted to valid identifier
    assert aliases == ["name", "default-name", "defaultName"]


def test_multiple_aliases_mixed_with_single() -> None:
    """Test mixing multiple aliases with single aliases."""
    resolver = FieldNameResolver(
        aliases={
            "multi": ["alias1", "alias2"],
            "single": "single_alias",
        }
    )

    field_name, aliases = resolver.get_valid_field_name_and_alias("multi")
    assert field_name == "alias1"
    assert aliases == ["multi", "alias1", "alias2"]

    field_name, alias = resolver.get_valid_field_name_and_alias("single")
    assert field_name == "single_alias"
    assert alias == "single"


def test_empty_list_aliases_flat() -> None:
    """Test empty list aliases are ignored and field is treated as no alias."""
    resolver = FieldNameResolver(aliases={"my_field": []})
    field_name, alias = resolver.get_valid_field_name_and_alias("my_field")
    assert field_name == "my_field"
    assert alias is None  # Empty list is ignored


def test_empty_list_aliases_scoped() -> None:
    """Test empty list aliases with scoped format are ignored."""
    resolver = FieldNameResolver(aliases={"User.name": []})
    field_name, alias = resolver.get_valid_field_name_and_alias("name", class_name="User")
    assert field_name == "name"
    assert alias is None  # Empty list is ignored


def test_unsupported_alias_values_are_ignored() -> None:
    """Alias values that are neither strings nor non-empty lists fall through."""
    resolver = FieldNameResolver(aliases=cast("dict[str, str | list[str]]", {"name": ("alias",)}))

    field_name, alias = resolver.get_valid_field_name_and_alias("name")

    assert field_name == "name"
    assert alias is None


def test_alias_lists_with_non_string_values_are_ignored() -> None:
    """Alias lists must contain only strings before they are used."""
    resolver = FieldNameResolver(aliases=cast("dict[str, str | list[str]]", {"name": [123, "alias"]}))

    field_name, alias = resolver.get_valid_field_name_and_alias("name")

    assert field_name == "name"
    assert alias is None


def test_model_resolver_unique_name_hints_keep_numbered_sequence() -> None:
    """Keep duplicate model names in the same numbered order while storing the next hint."""
    resolver = ModelResolver()

    names = [resolver.add(["models", str(index)], "Name", class_name=True).name for index in range(5)]

    assert names == ["Name", "Name1", "Name2", "Name3", "Name4"]
    assert resolver._unique_name_start_hints["Name", "", ""] == 5


def test_model_resolver_unique_name_direct_probe_does_not_consume_hint() -> None:
    """Keep direct uniqueness probes stable when the returned name is not registered."""
    resolver = ModelResolver()
    resolver.add(["models", "0"], "Name", class_name=True)

    assert resolver._get_unique_name("Name", camel=True) == "Name1"
    assert resolver._get_unique_name("Name", camel=True) == "Name1"


def test_model_resolver_unique_name_hints_reuse_deleted_suffix() -> None:
    """Reuse a freed duplicate suffix after deleting a reference."""
    resolver = ModelResolver()
    for index in range(4):
        resolver.add(["models", str(index)], "Name", class_name=True)

    resolver.delete(["models", "1"])

    assert resolver.add(["models", "next"], "Name", class_name=True).name == "Name1"


def test_model_resolver_unique_name_hints_reuse_renamed_suffix() -> None:
    """Reuse a freed duplicate suffix after renaming an existing reference."""
    resolver = ModelResolver()
    for index in range(3):
        resolver.add(["models", str(index)], "Name", class_name=True)

    resolver.add(["models", "1"], "Other", class_name=True)

    assert resolver.add(["models", "next"], "Name", class_name=True).name == "Name1"


def test_model_resolver_unique_name_hints_clear_on_reuse_reset() -> None:
    """Clear duplicate-name hints when the resolver naming state is reset."""
    resolver = ModelResolver()
    for index in range(3):
        resolver.add(["models", str(index)], "Name", class_name=True)

    resolver._reset_for_reuse({"Name"})

    assert resolver._unique_name_start_hints == {}
    assert resolver.add(["models", "next"], "Name", class_name=True).name == "Name1"


def test_model_resolver_unique_name_hints_reuse_custom_suffix() -> None:
    """Reuse a freed duplicate suffix when duplicate-name-suffix is configured."""
    resolver = ModelResolver(duplicate_name_suffix_map={"model": "Schema"})
    names = [resolver.add(["models", str(index)], "Name", class_name=True).name for index in range(4)]

    assert names == ["Name", "NameSchema", "NameSchema1", "NameSchema2"]

    resolver.delete(["models", "1"])

    assert resolver.add(["models", "next"], "Name", class_name=True).name == "NameSchema"


def test_model_resolver_unique_name_hints_invalidate_delimited_suffix() -> None:
    """Invalidate suffix hints for non-camel duplicate names."""
    resolver = ModelResolver()
    resolver._unique_name_start_hints["Name", "Schema", "_"] = 3

    resolver._invalidate_unique_name_hints("Name_Schema_1")

    assert resolver._unique_name_start_hints == {}


def test_model_resolver_unique_name_hints_scale_same_enum_names() -> None:
    """Keep hundreds of duplicate enum names in the same numbered order."""
    resolver = ModelResolver()

    names = [
        resolver.add(["enums", str(index)], "Status", class_name=True, model_type="enum").name for index in range(300)
    ]

    assert names[:4] == ["Status", "Status1", "Status2", "Status3"]
    assert names[-1] == "Status299"
