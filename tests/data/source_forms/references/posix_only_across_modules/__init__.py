"""G13 posix-only-call: a test reaches what exists on POSIX only through the scripts it calls.

A top-level function or class reaches a use in its own code or in what its names reach, in any module: a call, an
attribute of an imported module and a reference passed on (``pool.submit(run)``) all count, an object the model cannot
follow does not. A dispatching ``main`` reaches every subcommand.
"""
