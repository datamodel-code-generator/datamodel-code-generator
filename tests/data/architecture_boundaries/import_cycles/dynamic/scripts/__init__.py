"""import_module with a literal name is an edge; with a computed name it is not seen.

literal and literal_pair are a cycle for every key; computed and computed_pair are none (the
dynamic-import ratchet key counts the call instead).
"""
