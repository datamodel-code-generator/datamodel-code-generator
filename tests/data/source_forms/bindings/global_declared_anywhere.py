"""A6 config builder: global applies to the whole function wherever it stands, and binds at module level."""

_CONFIGS = {}


def _define():
    if not _CONFIGS:
        global JsonConfig, _LATE

    class JsonConfig:
        strict = True

    _LATE = 1
    _CONFIGS["json"] = JsonConfig


def reader():
    return JsonConfig, _LATE
