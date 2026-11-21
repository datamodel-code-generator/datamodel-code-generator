"""heki: ignore, bare and with rule codes or names, and noqa that names a heki rule."""

x = load()  # heki: ignore
y = load()  # heki: ignore[EFF101]
z = load()  # heki: ignore[EFF101, async-blocking-call]
w = load()  # noqa: EFF101
v = load()  # heki: ignore[EFF101]  # type: ignore[misc]
