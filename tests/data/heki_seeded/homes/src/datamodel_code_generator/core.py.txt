class Rule:
    def method(self) -> None:
        pass


TABLE = {}


def decide() -> Rule:
    return Rule()


class Failure(Exception):
    pass


class Detail(Failure):
    pass
