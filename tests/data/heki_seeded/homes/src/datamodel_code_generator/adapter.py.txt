from .core import TABLE, Rule, decide


def site() -> None:
    decide()


class Site:
    def nested(self) -> None:
        decide()


def typed(rule: Rule) -> object:
    return TABLE


def bypass(value: object) -> bool:
    return isinstance(value, Rule) and decide() is not None


def run_through_helper() -> Rule:
    return _helper()


def _helper() -> Rule:
    return decide()
