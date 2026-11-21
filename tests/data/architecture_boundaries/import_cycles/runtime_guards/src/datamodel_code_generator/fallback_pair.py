"""Import fallback back unless that fails."""

try:
    from datamodel_code_generator import fallback
except ImportError:
    fallback = None
