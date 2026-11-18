"""#7123 Counter, A1 M1: each import form binds a different name (the first part, the alias, the imported name)."""

import os
import os.path
import xml.etree.ElementTree
import json as _json
from collections import Counter
from collections import OrderedDict as Ordered


def f():
    import shutil
    from itertools import chain as _chain
    return os, xml, _json, Counter, Ordered, shutil, _chain
