"""Pure decision functions: frozen fact records in, frozen results out.

Parser passes and output backends call them through adapters that read the live objects, so each decision has one
home and reads only the facts its record names.
"""
