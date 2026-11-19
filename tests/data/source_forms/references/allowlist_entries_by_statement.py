"""SD (step 2, allowlist entries): a table's entries with their lines, read through its binding, uses and statements.

`constant()` gives values without lines, folds a set's repeated members and is unreadable once the table is written
through; a check that counts entries reads the binding's value, the subscript stores and method calls among its uses,
and the statement at each use's line.
questions: bindings, extents
constants: JUSTIFIED FROZEN EXEMPT
"""

JUSTIFIED = {
    "g": ("a", "b"),
}
JUSTIFIED["h"] = (
    "c",
    "d",
)
JUSTIFIED["g"] += ("e",)
FROZEN = frozenset({
    "x",
    "x",
})
EXEMPT = ["y"]
EXEMPT.extend(["z"])
