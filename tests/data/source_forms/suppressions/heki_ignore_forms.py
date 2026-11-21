"""heki: ignore, bare and with rule codes or names, and noqa that names a heki rule by code or name."""

x = load()  # heki: ignore
y = load()  # heki: ignore[EFF101]
z = load()  # heki: ignore[EFF101, async-blocking-call]
w = load()  # noqa: EFF101
v = load()  # heki: ignore[EFF101]  # type: ignore[misc]
u = load()  # noqa: write-owner-violation
t = load()  # noqa: PLC0415 - MUT201
s = load()  # heki: ignore[MUT201
r = load()  # heki: ignore[]
