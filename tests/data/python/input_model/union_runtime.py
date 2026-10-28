"""External runtime inputs and observations for union-annotation regressions."""

from .union_annotations import CustomValue


def callback(value):
    return value


VALUES = {
    "integer": 7,
    "callable": callback,
    "none": None,
    "string": "word",
    "custom": CustomValue(),
    "list": [7, callback],
    "callable_list": [callback],
    "mapping": {"items": [7, callback]},
    "nested": {"value": callback},
    "callback_mapping": {"handler": callback},
}

CASES = {
    "IntFirst": ["integer", "callable", "none"],
    "CallableFirst": ["integer", "callable", "none"],
    "PipeIntFirst": ["integer", "callable", "none"],
    "PipeCallableFirst": ["integer", "callable", "none"],
    "NoneFirst": ["integer", "callable", "none"],
    "NoneMiddle": ["integer", "callable", "none"],
    "NoneLast": ["integer", "callable", "none"],
    "OptionalCallableFirst": ["integer", "callable", "none"],
    "MultipleCallables": ["integer", "callable", "none"],
    "MultipleUnsupported": ["integer", "custom", "callable", "none"],
    "ListUnion": ["list", "callable_list", "none"],
    "UnionList": ["integer", "callable_list", "none"],
    "NestedContainer": ["mapping", "none"],
    "AnnotatedUnion": ["integer", "callable", "string", "none"],
    "OrdinaryUnion": ["integer", "string", "none", "callable"],
    "NestedModels": ["nested", "none"],
    "DuplicateSerializable": ["integer", "callable", "none"],
    "BooleanSchemaContainer": ["integer", "callback_mapping", "none"],
    "DocumentationControl": ["string", "none"],
}


def describe(value):
    if callable(value):
        return {"callable_result": value(7)}
    if isinstance(value, CustomValue):
        return {"custom_type": type(value).__name__}
    if isinstance(value, dict):
        return {key: describe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [describe(item) for item in value]
    return value
