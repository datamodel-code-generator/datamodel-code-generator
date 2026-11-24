"""Import fallback back unless that fails."""

try:
    from scripts import fallback
except ImportError:
    fallback = None
