"""A6 I2: the typing spec's re-export forms (X as X, from M import X as X, from . import X) and plain imports.

module: pkg
path: pkg/__init__.py
questions: imports
"""

import json as json
import os.path as path
from collections import Counter as Counter
from collections import OrderedDict
from . import sibling
from . import other as other
from . import renamed as alias
from .sub import Thing as Thing
