"""Review R11: definitions in the except and else branches of a try both bind, each with its guard."""

try:
    import foo
except ImportError:
    def helper():
        return None
else:
    def helper():
        return foo.x
