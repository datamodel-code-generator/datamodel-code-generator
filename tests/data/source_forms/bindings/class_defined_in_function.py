"""#7123 GraphQLParserConfig: a class defined inside the function that builds it is local there."""


def _define_format_parser_configs():
    class GraphQLParserConfig:
        graphql_no_typename: bool = False

        def describe(self):
            return GraphQLParserConfig

    return {"graphql": GraphQLParserConfig}
