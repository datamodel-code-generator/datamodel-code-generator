"""noqa comments: bare, with codes, any case and spacing; a noqa inside a string is no comment.

codes: F401 unused-import
"""

import os  # noqa
import sys  # noqa: F401
import json  #NOQA:F401,E501
import re  # noqa : F401
import abc  # noqa:E501 F401
TEXT = "# noqa: F401"
import ast  # noqa-not-a-directive
