"""Tests for parser-level structured Python type import collisions."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from datamodel_code_generator._python_type_annotation import PythonTypeBoundName, PythonTypeName
from datamodel_code_generator._python_type_binding import BoundPythonType, alias_bound_python_type
from datamodel_code_generator.imports import Import, Imports
from datamodel_code_generator.model.pydantic_v2.base_model import BaseModel, DataModelField
from datamodel_code_generator.parser._passes._shared import (
    _alias_base_class_imports,
    _apply_structured_import_aliases,
    _ordinary_field_shadow_aliases,
)
from datamodel_code_generator.parser._python_type_imports import resolve_python_type_import_aliases
from datamodel_code_generator.parser.generation import GenerationStore
from datamodel_code_generator.python_literal import (
    PythonRuntimeExpression,
    rewrite_runtime_expressions,
    rewrite_runtime_imports,
    runtime_expression_imports,
)
from datamodel_code_generator.reference import Reference
from datamodel_code_generator.types import DataType


def _bound_data_type(from_: str, name: str) -> DataType:
    import_ = Import(from_=from_, import_=name)
    return DataType(
        type=name,
        python_type=BoundPythonType(PythonTypeBoundName(name, from_, name), (import_,)),
    )


@pytest.mark.allow_direct_assert
def test_resolver_avoids_reserved_alias_suffixes() -> None:
    """Field reservations advance both field-style and numbered aliases."""
    aliases = resolve_python_type_import_aliases(
        [_bound_data_type("first", "Thing"), _bound_data_type("second", "Thing")],
        [],
        {"Thing_1"},
    )

    assert aliases["second", "Thing"].alias == "Thing_2"

    field_aliases = resolve_python_type_import_aliases(
        [_bound_data_type("first", "Thing"), _bound_data_type("second", "Thing")],
        [],
        {"Thing", "Thing_aliased"},
    )

    assert field_aliases["first", "Thing"].alias == "Thing_aliased_"
    assert field_aliases["second", "Thing"].alias == "Thing_aliased__"


@pytest.mark.allow_direct_assert
def test_resolver_shares_ordinary_field_alias_with_same_bound_identity() -> None:
    """One exact import identity receives one alias across ordinary and bound types."""
    import_ = Import(from_="datetime", import_="date")
    ordinary = DataType(type="date", import_=import_)
    bound = DataType(
        type="date",
        python_type=BoundPythonType(PythonTypeBoundName("date", "datetime", "date"), (import_,)),
    )

    aliases = resolve_python_type_import_aliases([ordinary, bound], [], {"date"})

    assert aliases == {("datetime", "date"): Import(from_="datetime", import_="date", alias="date_aliased")}

    assert resolve_python_type_import_aliases([ordinary, bound], [], set()) == {}


@pytest.mark.allow_direct_assert
def test_resolver_propagates_existing_alias_to_same_bound_identity() -> None:
    """An established alias remains canonical for every structured consumer."""
    ordinary_import = Import(from_="foo", import_="Bar", alias="ExistingAlias")
    bound = _bound_data_type("foo", "Bar")

    aliases = resolve_python_type_import_aliases(
        [DataType(type="ExistingAlias", import_=ordinary_import), bound],
        [],
        set(),
    )

    assert aliases == {("foo", "Bar"): ordinary_import}
    aliased_bound = alias_bound_python_type(bound.python_type, aliases)
    assert aliased_bound.expression == PythonTypeBoundName("ExistingAlias", "foo", "Bar")
    assert aliased_bound.imports == (ordinary_import,)


@pytest.mark.allow_direct_assert
def test_resolver_realiases_aggregate_binding_shadowed_by_field() -> None:
    """A canonical aggregate alias still yields to names reserved in this module."""
    module_imports = Imports()
    module_imports.append(Import(from_="foo", import_="Bar", alias="Taken"))

    aliases = resolve_python_type_import_aliases(
        [
            DataType(type="Taken", import_=Import(from_="foo", import_="Bar", alias="Taken")),
            _bound_data_type("foo", "Bar"),
        ],
        [],
        {"Taken"},
        (module_imports,),
    )

    assert aliases == {("foo", "Bar"): Import(from_="foo", import_="Bar", alias="Taken_aliased")}


@pytest.mark.allow_direct_assert
def test_resolver_collects_plain_names_model_imports_and_late_module_imports() -> None:
    """All public parser/model contexts protect their effective names."""
    plain = DataType(type="Plain")
    unbound = DataType(
        type="Local",
        python_type=BoundPythonType(PythonTypeName("Local"), ()),
    )
    model_import = Import(from_="datetime", import_="timedelta")
    target = _bound_data_type("external", "Thing")
    model = BaseModel(
        reference=Reference(path="model", name="Model"),
        fields=[
            DataModelField(name="duration", data_type=DataType(type="timedelta", import_=model_import)),
            DataModelField(name="target", data_type=target),
        ],
    )
    module_imports = Imports()
    module_imports.append([
        Import(from_="external", import_="Thing"),
        Import(from_="ordinary", import_="Thing"),
    ])

    aliases = resolve_python_type_import_aliases(
        [plain, unbound, target],
        [model],
        set(),
        (module_imports,),
    )

    assert aliases["external", "Thing"].alias == "Thing_1"


@pytest.mark.allow_direct_assert
def test_resolver_reserves_unimported_base_class_names() -> None:
    """A local base name remains protected from a structured import leaf."""
    model = BaseModel(
        reference=Reference(path="model", name="Model"),
        fields=[],
        base_classes=[Reference(path="LocalBase", name="LocalBase")],
    )

    aliases = resolve_python_type_import_aliases([_bound_data_type("external", "LocalBase")], [model], set())

    assert aliases["external", "LocalBase"].alias == "LocalBase_aliased"


@pytest.mark.allow_direct_assert
def test_runtime_expressions_rewrite_immutable_containers_and_keep_semantic_values() -> None:
    """Runtime source identities rewrite without changing their string-compatible semantic value."""
    decimal = Import(from_="decimal", import_="Decimal")
    aliased_decimal = Import(from_="decimal", import_="Decimal", alias="Decimal_aliased")
    aliases = {("decimal", "Decimal"): aliased_decimal}
    expression = PythonRuntimeExpression.from_import_call(decimal, "'0.1'", value="0.1")

    rewritten_tuple = rewrite_runtime_expressions((expression,), aliases)
    rewritten_mapping = rewrite_runtime_expressions({"value": [expression]}, aliases)
    rewritten_set = rewrite_runtime_expressions({expression}, aliases)
    rewritten_frozenset = rewrite_runtime_expressions(frozenset((expression,)), aliases)

    assert repr(rewritten_tuple[0]) == "Decimal_aliased('0.1')"
    assert repr(rewritten_mapping["value"][0]) == "Decimal_aliased('0.1')"
    assert repr(next(iter(rewritten_set))) == "Decimal_aliased('0.1')"
    assert repr(next(iter(rewritten_frozenset))) == "Decimal_aliased('0.1')"
    assert str(rewritten_tuple[0]) == "0.1"
    assert expression.imports == (decimal,)
    assert rewrite_runtime_expressions(expression, {}) is expression
    assert rewrite_runtime_expressions((expression,), {}) == (expression,)
    assert rewrite_runtime_expressions({"expression": expression}, {}) == {"expression": expression}
    assert deepcopy(expression) is expression
    with pytest.raises(FrozenInstanceError):
        expression.prefix = "other"
    assert rewrite_runtime_imports((decimal,), {}) == (decimal,)
    assert rewrite_runtime_imports((decimal,), {("other", "Decimal"): aliased_decimal}) == (decimal,)


@pytest.mark.allow_direct_assert
def test_runtime_expression_imports_walks_exact_builtin_containers_once() -> None:
    """Producers can scan nested built-ins once before import collection becomes O(1)."""
    import_ = Import(from_="decimal", import_="Decimal")
    expression = PythonRuntimeExpression.from_import_call(import_, "'0.1'")
    cycle: list[object] = []
    cycle.extend((cycle, expression))
    value = {"key": [({expression}, frozenset((expression,))), cycle]}

    assert runtime_expression_imports(value) == (import_, import_, import_)
    assert runtime_expression_imports("not an expression") == ()


@pytest.mark.allow_direct_assert
def test_data_type_runtime_expression_metadata_rewrites_kwargs_without_rescanning() -> None:
    """Producer metadata keeps constrained kwargs structural until the final alias pass."""
    decimal = Import(from_="decimal", import_="Decimal")
    expression = PythonRuntimeExpression.from_import_call(decimal, "'0.1'")
    data_type = DataType(type="condecimal", is_func=True, kwargs={"multiple_of": (expression,)})
    data_type._set_runtime_expression_imports(runtime_expression_imports(data_type.kwargs))
    field = DataModelField(name="Decimal", data_type=data_type)
    model = BaseModel(reference=Reference(path="model", name="Model"), fields=[field])

    aliases = resolve_python_type_import_aliases([data_type], [model], {"Decimal"})
    _apply_structured_import_aliases(GenerationStore().builder(), [model], aliases)
    copied_data_type = deepcopy(data_type)

    assert aliases == {("decimal", "Decimal"): Import(from_="decimal", import_="Decimal", alias="Decimal_aliased")}
    assert repr(data_type.kwargs["multiple_of"][0]) == "Decimal_aliased('0.1')"
    assert data_type.runtime_expression_imports == (aliases["decimal", "Decimal"],)
    assert copied_data_type.runtime_expression_imports == (aliases["decimal", "Decimal"],)
    assert tuple(data_type.imports) == ()
    data_type._set_runtime_expression_imports(())
    assert not data_type.runtime_expression_imports


@pytest.mark.allow_direct_assert
def test_apply_realiases_all_additional_imports_that_share_runtime_identity() -> None:
    """A re-aliased module binding updates default expressions and every aggregate import."""
    original = Import(from_="decimal", import_="Decimal", alias="Taken")
    canonical = Import(from_="decimal", import_="Decimal", alias="Taken_aliased")
    field = DataModelField(
        name="Taken",
        data_type=DataType(type="str"),
        default=PythonRuntimeExpression.from_import_call(original, "'0.1'"),
    )
    field._set_runtime_expression_imports((original,))
    model = BaseModel(reference=Reference(path="model", name="Model"), fields=[field])
    model._additional_imports.extend((original, original))

    _apply_structured_import_aliases(
        GenerationStore().builder(),
        [model],
        {("decimal", "Decimal"): canonical},
    )

    assert repr(field.default) == "Taken_aliased('0.1')"
    assert model._additional_imports.count(canonical) == 2
    assert original not in model._additional_imports


@pytest.mark.allow_direct_assert
def test_structured_alias_scan_and_apply_cover_each_runtime_consumer() -> None:
    """Type, bound annotation, and registered kwargs imports receive one canonical binding."""
    ordinary_import = Import(from_="ordinary", import_="Taken")
    bound_import = Import(from_="bound", import_="Bound")
    runtime_import = Import(from_="decimal", import_="Decimal")
    runtime_expression = PythonRuntimeExpression.from_import_call(runtime_import, "'0.1'")
    ordinary = DataType(type="Taken", import_=ordinary_import)
    bound = DataType(
        type="Bound",
        python_type=BoundPythonType(PythonTypeBoundName("Bound", "bound", "Bound"), (bound_import,)),
    )
    runtime = DataType(type="condecimal", is_func=True, kwargs={"multiple_of": runtime_expression})
    runtime._set_runtime_expression_imports((runtime_import,))
    model = BaseModel(
        reference=Reference(path="model", name="Model"),
        fields=[
            DataModelField(name="ordinary", data_type=ordinary),
            DataModelField(name="bound", data_type=bound),
            DataModelField(name="runtime", data_type=runtime),
        ],
    )
    aliases, has_python_type, has_runtime_expressions = _ordinary_field_shadow_aliases([model], {"Taken"})
    canonical = {
        ("ordinary", "Taken"): Import(from_="ordinary", import_="Taken", alias="Taken_aliased"),
        ("bound", "Bound"): Import(from_="bound", import_="Bound", alias="Bound_aliased"),
        ("decimal", "Decimal"): Import(from_="decimal", import_="Decimal", alias="Decimal_aliased"),
    }

    _apply_structured_import_aliases(GenerationStore().builder(), [model], canonical)

    assert aliases == {("ordinary", "Taken"): canonical["ordinary", "Taken"]}
    assert has_python_type
    assert has_runtime_expressions
    assert ordinary.import_ is canonical["ordinary", "Taken"]
    assert bound.type == "Bound_aliased"
    assert repr(runtime.kwargs["multiple_of"]) == "Decimal_aliased('0.1')"


@pytest.mark.allow_direct_assert
def test_structured_aliasing_skips_registered_runtime_imports_without_a_collision() -> None:
    """Registered expressions do not rewrite their kwargs when no identity is aliased."""
    import_ = Import(from_="decimal", import_="Decimal")
    expression = PythonRuntimeExpression.from_import_call(import_, "'0.1'")
    data_type = DataType(type="condecimal", is_func=True, kwargs={"multiple_of": expression})
    data_type._set_runtime_expression_imports((import_,))
    model = BaseModel(
        reference=Reference(path="model", name="Model"),
        fields=[DataModelField(name="value", data_type=data_type)],
    )

    _apply_structured_import_aliases(GenerationStore().builder(), [model], {})

    assert data_type.kwargs["multiple_of"] is expression


@pytest.mark.allow_direct_assert
def test_base_class_aliasing_skips_an_already_canonical_binding() -> None:
    """Base class imports update once and remain stable on the late finalization pass."""
    original = Import(from_="external", import_="Base")
    canonical = Import(from_="external", import_="Base", alias="Base_aliased")
    reference = Reference(path="base", name="Base")
    model = BaseModel(reference=Reference(path="model", name="Model"), fields=[], base_classes=[reference])
    model.base_classes[0].import_ = original

    _alias_base_class_imports(model, {("external", "Base"): canonical})
    first = model.base_classes[0].import_
    _alias_base_class_imports(model, {("external", "Base"): canonical})

    assert (first, model.base_classes[0].import_, model.base_classes[0].type) == (canonical, canonical, "Base_aliased")


@pytest.mark.allow_direct_assert
def test_import_binding_name_covers_alias_module_and_from_imports() -> None:
    """All consumers share the effective Python name of an import identity."""
    assert Import(import_="datetime").binding_name == "datetime"
    assert Import(from_="datetime", import_="date").binding_name == "date"
    assert Import(import_="datetime", alias="datetime_module").binding_name == "datetime_module"
