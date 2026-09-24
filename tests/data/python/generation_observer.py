"""Profiler callback kept outside coverage tracing, like other executable fixtures."""

from __future__ import annotations

import weakref
from collections import Counter

OBSERVED = frozenset({
    "_copy_data_model_field", "_copy_data_type", "_copy_resolved_inherited_field",
    "resolve_ref", "add_ref", "_validate_schema_object", "_get_ref_raw_schema",
    "_load_ref_schema_object", "_cache_ref_data_type_facts", "_generate_module_output",
})
PHASES = frozenset({"_build_generation_parser", "_parse_with_disposal", "_emit_generation"})


class GenerationObserver:
    """Observe actual engine frames without changing methods or evaluating getters."""

    def __init__(self):
        """Initialize call counts, phase order, and weak parser references."""
        self.calls = Counter()
        self.phases = []
        self.references = []

    def record(self, frame, event, arg):
        """Record engine events without retaining live generation frames."""
        if not frame.f_globals.get("__name__", "").startswith("datamodel_code_generator"):
            return
        if frame.f_globals.get("__name__") == "datamodel_code_generator.parser.openapi_contract":
            return
        name = frame.f_code.co_name
        if event == "call":
            if name in OBSERVED:
                self.calls[name] += 1
            if name in PHASES:
                self.phases.append(name)
        elif event == "return" and name == "_build_generation_parser":
            self.references.append(weakref.ref(arg))
