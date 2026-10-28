"""Stable exception objects for abnormal dependency initialization."""
ERRORS = {
    'unexpected_runtime': RuntimeError('unexpected private dependency failure'),
    'unexpected_value': ValueError('unexpected private dependency failure'),
    'unexpected_base': KeyboardInterrupt('unexpected private dependency failure'),
}
