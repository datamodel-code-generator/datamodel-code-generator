"""Observe original engine calls without retaining graphs or installing method hooks."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from tests.data.python.generation_observer import OBSERVED

if TYPE_CHECKING:
    from types import FrameType

_CAPTURE_MODULES = frozenset({
    "datamodel_code_generator.parser.openapi_contract",
    "datamodel_code_generator.parser.openapi_contract_store",
    "datamodel_code_generator._openapi_generation",
    "datamodel_code_generator._generation_contract",
})
_ENGINE_CALLS = OBSERVED | frozenset({
    "get_ref_data_type",
    "parse_obj",
    "parse_raw_obj",
    "parse_ref",
    "_get_ref_body",
    "type_hint",
    "base_type_hint",
    "imports",
    "render",
    "represented_default",
    "_get_field_data",
    "_get_constructor_default_info",
    "_get_meta_string",
    "_get_field_render_plan",
    "_builtin_template_data",
    "all_fields",
    "iter_all_fields",
    "refresh",
    "refresh_now",
    "current_facts",
    "replace_data_type_ref",
    "replace_field_type",
    "replace_nested_data_type",
    "redirect_reference_users",
    "redirect_model_reference_users",
    "_get_rw_model_variant_reference",
})


class BindingEngineObserver:
    """Count original leaf/guard/getter calls separately from capture wrappers."""

    def __init__(self) -> None:
        """Keep counts only, without live frames or parser references."""
        self.calls: Counter[tuple[str, str]] = Counter()

    def record(self, frame: FrameType, event: str, _arg: object) -> None:
        """Record calls from original model-engine modules, including inherited bodies."""
        module = frame.f_globals.get("__name__", "")
        if (
            event == "call"
            and module.startswith("datamodel_code_generator")
            and module not in _CAPTURE_MODULES
            and frame.f_code.co_name in _ENGINE_CALLS
        ):
            self.calls[module, frame.f_code.co_name] += 1
