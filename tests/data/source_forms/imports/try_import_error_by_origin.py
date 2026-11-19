"""Review R23, N3: a try is an import fallback only when its body imports and a handler catches the import error."""

try:
    import fast
except Exception:
    fast = None
try:
    value = int("x")
except ValueError:
    value = 0
try:
    import local_error
except ImportError:
    local_error = None


def shadowing():
    class ImportError(Exception): ...

    try:
        import shadowed
    except ImportError:
        shadowed = None
    return shadowed
