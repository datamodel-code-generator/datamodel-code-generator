"""Reads, and writes, the object that ``SchemaGraphBuilder.schema`` shares: each write is a finding, the read none.

``write_through_a_helper`` hands the shared object to a function that writes it, a finding too; ``write_a_parameter``
writes a parameter that no call of the package gives the shared object, which heki does not follow.
"""

from datamodel_code_generator.parser._schema_graph import Schema, SchemaGraphBuilder


def read(graph: SchemaGraphBuilder) -> str:
    return graph.schema().title


def write(graph: SchemaGraphBuilder) -> None:
    graph.schema().title = "written"


def write_through_a_helper(graph: SchemaGraphBuilder) -> None:
    _rename(graph.schema())


def _rename(schema: Schema) -> None:
    schema.title = "written"


def write_a_parameter(schema: Schema) -> None:
    schema.title = "written"
