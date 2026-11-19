"""A6 __main__: imports under match, environment tests, loops and with blocks keep those guards."""

import os
import sys

match sys.argv:
    case [_, "fast"]:
        import fast_path
    case _:
        import slow_path
if os.environ.get("DEBUG"):
    import debugger
if __name__ == "__main__":
    import runner
for _name in ("a",):
    import looped
while False:
    import never
with open(__file__) as _handle:
    import inside_with
