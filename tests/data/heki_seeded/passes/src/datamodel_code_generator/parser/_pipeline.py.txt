from collections.abc import Callable


def late(function: Callable[..., None]) -> Callable[..., None]:
    return function


class PassSpec:
    def __init__(self, name: str, run: Callable[..., None]) -> None:
        self.name, self.run = name, run


class HookCall:
    def __init__(self, name: str, run: Callable[..., None]) -> None:
        self.name, self.run = name, run
