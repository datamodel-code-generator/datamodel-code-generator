"""#7026 (both threads): module-level imports under exclusive version branches, elif included, and in a function."""

import sys

if sys.version_info >= (3, 12):
    import versioned
elif sys.version_info[:2] == (3, 11):
    import middle
else:
    import legacy

if sys.hexversion < 0x30C0000 and sys.version_info.major == 3:
    import both


def later():
    import versioned

    return versioned
