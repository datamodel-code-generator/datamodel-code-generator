"""#7017, A3: writes through a name (subscript, augmented subscript, method, attribute, del) are uses of the binding."""

JUSTIFIED_VIOLATIONS = {"g": ("a",)}
JUSTIFIED_VIOLATIONS["g"] += ("b",)
JUSTIFIED_VIOLATIONS["h"] = ("c",)
JUSTIFIED_VIOLATIONS.update({"i": ("d",)})
JUSTIFIED_VIOLATIONS.setdefault("j", ())
del JUSTIFIED_VIOLATIONS["h"]
ITEMS = []
ITEMS.append(1)
REGISTRY = type("Registry", (), {})
REGISTRY.entry = 1
FROZEN = (1, 2)


def read():
    return JUSTIFIED_VIOLATIONS["g"], FROZEN
