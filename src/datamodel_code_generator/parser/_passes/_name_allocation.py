"""Class and field names that passes after L1 allocate, one scheme per pass.

The parse resolver (``Parser.model_resolver``) names models at L1. A pass that renames models or fields later asks
``scheme_resolver`` for a resolver of its scheme, and writes the names it allocates through
``GenerationStore.rename_model`` or the field rename of the store:

* ``module`` (L3 ``structure.py:replace_duplicate_name_in_module``): a name taken in the module gets the suffix
  ``Model`` (row ``legacy-module-suffix-model``: the ``--duplicate-name-suffix`` map is not read), and a name is kept
  as written only under a custom class name generator.
* ``flattened-module`` (L3 repair ``Parser._flatten_invalid_modules``): a name taken gets the suffix of the
  ``--duplicate-name-suffix`` map or ``Model``, and a name is always kept as written.
* ``field-type`` (L5 ``modules.py:change_field_name``, ``rename_named_type``, ``rename_fields_hiding_types``): a name
  taken is numbered after the name without its number suffix; field names follow ``--snake-case-field``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, Literal

from datamodel_code_generator.reference import ModelResolver

if TYPE_CHECKING:
    from datamodel_code_generator._format_types import PythonVersion

__all__ = ["NameScheme", "scheme_resolver"]

NameScheme = Literal["module", "flattened-module", "field-type"]

_LEGACY_MODULE_SUFFIX: Final = "Model"


def _keep_name(name: str) -> str:
    return name


def scheme_resolver(  # noqa: PLR0913
    scheme: NameScheme,
    *,
    target_python_version: PythonVersion | None,
    exclude_names: set[str] | None = None,
    keeps_names: bool = False,
    duplicate_name_suffix_map: dict[str, str] | None = None,
    snake_case_field: bool = False,
) -> ModelResolver:
    """Return a resolver that allocates names under ``scheme`` around ``exclude_names``.

    ``keeps_names`` is read by ``module``, ``duplicate_name_suffix_map`` by ``flattened-module`` and
    ``snake_case_field`` by ``field-type``.
    """
    options: dict[str, Any] = {"snake_case_field": snake_case_field, "remove_suffix_number": True}
    match scheme:
        case "module":
            options = {
                "duplicate_name_suffix": _LEGACY_MODULE_SUFFIX,
                "custom_class_name_generator": _keep_name if keeps_names else None,
            }
        case "flattened-module":
            options = {
                "duplicate_name_suffix": _LEGACY_MODULE_SUFFIX,
                "custom_class_name_generator": _keep_name,
                "duplicate_name_suffix_map": duplicate_name_suffix_map,
            }
        case _:  # "field-type": the resolver defaults
            pass
    return ModelResolver(exclude_names=exclude_names, target_python_version=target_python_version, **options)
