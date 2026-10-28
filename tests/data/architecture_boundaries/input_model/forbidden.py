from datamodel_code_generator import DataModelType

OUTPUT_FAMILY = DataModelType.MsgspecStruct


def rewrite_schema(schema):
    from datamodel_code_generator.parser.mcp import _rewrite_schema_refs

    return _rewrite_schema_refs(schema, {}, prefix="local_")


def rewrite_shared_schema(schema):
    from datamodel_code_generator._json_schema import _rewrite_schema_refs

    return _rewrite_schema_refs(schema, {}, prefix="local_")
