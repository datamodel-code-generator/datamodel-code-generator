class Parser:
    def __init__(self) -> None:
        self._count = 0

    def _reset(self) -> None:
        self._count = 0


def outside_the_concern(parser: Parser) -> int:
    return parser._count
