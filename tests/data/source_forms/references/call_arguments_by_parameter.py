"""#7026 sep=, A3 action=: the argument a call passes by position or keyword; *args and **kwargs give UNKNOWN.

keywords: sep action
"""

import warnings


def cut(ref, args, options):
    warnings.filterwarnings(action="ignore")
    warnings.filterwarnings("default", category=DeprecationWarning)
    return (
        ref.split(sep="#", maxsplit=1),
        ref.rsplit("#", 1),
        str.split(ref, "#"),
        ref.split(maxsplit=1),
        ref.split(*args),
        ref.split(**options),
    )
