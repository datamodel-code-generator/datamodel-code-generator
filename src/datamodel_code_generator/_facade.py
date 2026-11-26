"""``generate()``, the public entry point: a server or client target, or the models of one generation run.

It hands a selected target to ``_target_selection.py``, whose targets run the models through
``_generation.py``, so it lives apart from that core. The package root re-exports it under its historical
module.
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Unpack,
)

from datamodel_code_generator._generation import (
    _generate_with_atomic_remote_update,
    _prepare_generate_facade_config,
    _run_generation,
    _uses_legacy_process_state,
)
from datamodel_code_generator._paths import working_directory
from datamodel_code_generator._process_state import PROCESS_STATE_LOCK

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from urllib.parse import ParseResult

    from datamodel_code_generator._shared_types import GeneratedModules
    from datamodel_code_generator._types.generate_config_dict import GenerateConfigDict
    from datamodel_code_generator.config import (
        GenerateConfig,
    )


def generate(
    input_: Path | str | ParseResult | Mapping[str, Any] | list[Any],
    *,
    config: GenerateConfig | None = None,
    **options: Unpack[GenerateConfigDict],
) -> str | GeneratedModules | None:
    """Generate Python data models from schema definitions or structured data.

    This is the main entry point for code generation. Supports OpenAPI, AsyncAPI,
    JSON Schema, GraphQL, XML Schema, Protocol Buffers, Avro, and raw data formats
    (JSON, YAML, Dict, CSV) as input.

    HTTP(S) URL inputs and references select their backend lazily on first use. The default ``HTTPBackend.AUTO``
    policy selects stable `httpx` when its client module is installed and selects experimental `httpx2` only when
    that module is absent. Explicit choices and paired dependency errors do not fall back. File URL reference
    joining uses a dependency-free local fast path.

    ``generate_server`` or ``generate_client`` also generates a server or client package with the models (experimental),
    configured by the ``server_*`` or ``client_*`` options; they take the values of their command-line options, with
    JSON options as mappings. Without the selector those options have no effect.

    Args:
        input_: The input source (Path file input, string content, URL, dict,
            list of file paths, or MCP tools list when input_file_type is
            InputFileType.MCPTools).
        config: A GenerateConfig object with all options. Cannot be used together with **options.
        **options: Individual options matching GenerateConfig fields. Cannot be used together with config.

    Returns:
        - When output is a Path: None (writes to file system)
        - When output is None and single module: str (generated code)
        - When output is None and multiple modules: GeneratedModules (dict mapping
          module path tuples to generated code strings)
        - When output is None and a server or client is generated: GeneratedModules with every model and package
          file, each under the path its import path implies

    Raises:
        ValueError: If both config and **options are provided.
    """
    if config is not None and options:
        msg = "Cannot specify both 'config' and keyword arguments. Use one or the other."
        raise ValueError(msg)

    if config is None:
        from datamodel_code_generator.config import GenerateConfig as _GenerateConfig  # noqa: PLC0415
        from datamodel_code_generator.config import _rebuild_generate_config  # noqa: PLC0415

        _rebuild_generate_config()
        config = _GenerateConfig.model_validate(options)
    if config.generate_server is not None or config.generate_client is not None:
        from datamodel_code_generator._target_selection import generate_selected_target  # noqa: PLC0415

        return generate_selected_target(input_, config)
    config = _prepare_generate_facade_config(config)

    from datamodel_code_generator._lock_plan import owns_lock_update  # noqa: PLC0415

    atomic_remote_update = owns_lock_update(config) and (
        config.output is not None or config.emit_model_metadata is not None
    )
    if config.output is not None and _uses_legacy_process_state(config):
        with PROCESS_STATE_LOCK:
            return (_generate_with_atomic_remote_update if atomic_remote_update else _run_generation)(
                input_, config, working_directory(), use_output_cwd=atomic_remote_update or config.output is not None
            )
    with PROCESS_STATE_LOCK:
        caller_cwd = working_directory()
    return (_generate_with_atomic_remote_update if atomic_remote_update else _run_generation)(
        input_, config, caller_cwd, use_output_cwd=False
    )


_ROOT_MODULE = "datamodel_code_generator"
generate.__module__ = _ROOT_MODULE
