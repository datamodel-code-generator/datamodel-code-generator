"""Dict schemas for --input-model tests."""

USER_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
    },
    "required": ["name", "age"],
}

OPENAPI_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {},
    "components": {
        "schemas": {
            "User": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                },
                "required": ["name", "age"],
            }
        }
    },
}

JSON_COERCIBLE_SCHEMA = {
    "title": "JsonCoercible",
    "type": "object",
    "properties": {1: {"type": "string"}},
}

NON_JSON_SCHEMA = {
    "type": "object",
    "x-non-json": {"value"},
}
