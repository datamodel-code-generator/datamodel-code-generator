"""type: ignore, ty: ignore and pyright: ignore, bare and with codes."""

x: int = "a"  # type: ignore
y: int = "b"  # type: ignore[assignment]
z: int = "c"  # ty: ignore[invalid-assignment, other]
w: int = "d"  # pyright: ignore[reportAssignmentType]
v: int = "e"  # noqa: E501  # type: ignore[assignment]
