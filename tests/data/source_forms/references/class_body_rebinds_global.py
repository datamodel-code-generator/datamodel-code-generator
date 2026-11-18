"""Review R35, S1: _limit = _limit in a class body reads the module's _limit, so the module binding is read."""

_limit = 3


class C:
    _limit = _limit
