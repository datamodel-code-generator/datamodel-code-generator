"""A3: private names reached through getattr strings, an f-string prefix, attrgetter and object.__getattribute__.

keywords: name
"""

import operator


def read(obj, suffix):
    return (
        getattr(obj, "_private"),
        getattr(obj, f"_prefix_{suffix}"),
        operator.attrgetter("_other")(obj),
        object.__getattribute__(obj, "_raw"),
        hasattr(obj, "public"),
    )
