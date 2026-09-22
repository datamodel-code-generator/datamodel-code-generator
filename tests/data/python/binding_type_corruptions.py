"""Corrupt actual completed type producers for abnormal binding regressions."""

from __future__ import annotations

from typing import Any

from datamodel_code_generator._python_type_annotation import (
    PythonTypeBoundName,
    PythonTypeName,
    PythonTypeOpaqueText,
    PythonTypeQualifiedName,
    PythonTypeRuntimeSymbol,
    PythonTypeStarred,
    PythonTypeTuple,
)
from datamodel_code_generator._python_type_binding import BoundPythonType
from datamodel_code_generator.imports import Import
from datamodel_code_generator.python_literal import PythonRuntimeExpression
from datamodel_code_generator.reference import Reference


def _argument_value(case: str) -> object:
    match case:
        case "argument-bytes":
            return b"corrupt"
        case "argument-tuple":
            return (1, 2)
        case "argument-set":
            return {1, 2}
        case "argument-frozenset":
            return frozenset((1, 2))
        case "argument-opaque":
            return object()
        case "argument-cycle":
            cycle: list[object] = []
            cycle.append(cycle)
            return cycle
        case "argument-duplicate-marker":
            return PythonRuntimeExpression(Import(import_="Decimal", from_="decimal"), "__dcg_import_binding__ + ", "")
        case _:
            return PythonRuntimeExpression(Import(import_="Decimal", from_="decimal"), "", "('2')")

def corrupt_type(parser: Any, data_type: Any, case: str) -> None:
    """Damage only adopted type state after ordinary rendering has completed."""
    if case.startswith("argument-"):
        data_type.kwargs = {"ge": _argument_value(case)}
    else:
        data_type.kwargs = {}
        data_type.is_func = False
        match case:
            case "qualified-import":
                data_type.type = "conint.Unknown"
            case "invalid-import-name":
                data_type.type = "other.Unknown"
            case _:
                data_type.type = None
                data_type.import_ = None
                match case:
                    case "unsupported-atom":
                        data_type.type = "not_a_builtin"
                    case "none-atom":
                        data_type.type = "None"
                    case "any-atom":
                        data_type.type = "Any"
                    case "frozen-set":
                        data_type.type = "int"
                        data_type.is_frozen_set = True
                    case "null-union":
                        data_type.data_types = [parser.data_type(type="None"), parser.data_type(type="None")]
                    case "ordered-union":
                        data_type.preserve_union_member_order = True
                        data_type.data_types = [parser.data_type(type="str"), parser.data_type(type="bool")]
                    case "missing-reference":
                        data_type.reference = Reference(path="#/unobserved", name="Unobserved")
                    case "mapping-without-value":
                        data_type.is_dict = True
                        data_type.dict_key = parser.data_type(type="int")
                    case "bound-qualified":
                        data_type.python_type = BoundPythonType(PythonTypeQualifiedName(("missing", "Type")), ())
                    case "bound-tuple":
                        data_type.python_type = BoundPythonType(
                            PythonTypeTuple((PythonTypeName("int"), PythonTypeName("str"))), ()
                        )
                    case "bound-starred":
                        data_type.python_type = BoundPythonType(PythonTypeStarred(PythonTypeName("int")), ())
                    case "bound-opaque":
                        data_type.python_type = BoundPythonType(PythonTypeOpaqueText("opaque"), ())
                    case "bound-missing-name":
                        data_type.python_type = BoundPythonType(
                            PythonTypeBoundName("Missing", "missing", "Missing"), ()
                        )
                    case "bound-missing-module":
                        data_type.python_type = BoundPythonType(PythonTypeRuntimeSymbol("missing", ("Type",)), ())
                    case "missing-enum-members":
                        data_type.enum_member_literals = [("Missing", "value")]
                    case "empty-tuple":
                        data_type.is_tuple = True
                        data_type.tuple_item_count = 1
                    case "variadic-tuple":
                        data_type.is_tuple = True
                        data_type.data_types = [parser.data_type(type="int"), parser.data_type(type="...")]
                    case _:
                        pass
