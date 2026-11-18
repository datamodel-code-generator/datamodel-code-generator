"""Review R28: a match at module level binds module names under case guards."""

import sys

match sys.argv:
    case [_, "run", *args]:
        mode = "run"
    case _:
        mode = None
