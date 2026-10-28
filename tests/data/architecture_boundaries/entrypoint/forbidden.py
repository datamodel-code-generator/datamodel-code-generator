PRIVATE_ATTRIBUTE = "_formatter_cwd"


def mutate_parser(parser, retry_parser, worker_parser_instance):
    parser._diagnostic_source_path = None
    parser._dispose()
    parser.__dict__.pop("_formatter_cwd", None)
    worker_parser_instance._formatter_cwd = None
    return getattr(retry_parser, PRIVATE_ATTRIBUTE, None)
