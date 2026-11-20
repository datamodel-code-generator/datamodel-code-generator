"""C3 (scripts/_change_units.py): without the future import a function's annotations may run; a local one never does.

segments: yes
"""


def convert(value: int = 1) -> int:
    local: int = value
    return local
