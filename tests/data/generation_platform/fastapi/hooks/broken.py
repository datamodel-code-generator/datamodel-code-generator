"""A hook file that fails while it loads, before any hook is called."""

message = "The hook file could not load its settings"
raise LookupError(message)
