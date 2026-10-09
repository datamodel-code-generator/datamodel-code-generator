"""Map the server and client options of a configuration onto their target, and run it for generate()."""

from __future__ import annotations

import re
from dataclasses import replace
from enum import Enum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from datamodel_code_generator import Error
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, OperationRef
from datamodel_code_generator._process_state import PROCESS_STATE_LOCK
from datamodel_code_generator._target_documents import document_identity

if TYPE_CHECKING:
    from collections.abc import Callable

    from datamodel_code_generator import GeneratedModules, _GenerationInput  # pyright: ignore[reportPrivateUsage]
    from datamodel_code_generator._api_generation import TargetGenerator
    from datamodel_code_generator._api_types import GeneratedArtifact, OperationSelector
    from datamodel_code_generator._client.config import ClientGenerationConfig
    from datamodel_code_generator._client.target import ClientTarget
    from datamodel_code_generator._fastapi.config import FastAPIConfig
    from datamodel_code_generator._fastapi.target import FastAPITarget
    from datamodel_code_generator._target_config import TargetConfig
    from datamodel_code_generator.config import GenerateConfig
    from datamodel_code_generator.json_config import JsonConfigFieldName

_SERVER_CHOICES: Final = ("layout", "handler_mode", "include_request", "body_mode")
_OPERATION_SETTINGS: Final = (
    "server_handler_modes",
    "server_body_modes",
    "server_operation_names",
    "server_parameter_names",
)
_CLIENT_CHOICES: Final = ("signature_style", "body_arguments", "default_base_url", "server_base_url")
_INDEXED: Final = re.compile(r"(operations|resource_names)\[(\d+)\](?:\.(parameter_names|body_field_names)\[(\d+)\])?")


def _plain(value: Any) -> Any:
    """Return the option value an enum member stands for, and any other value unchanged."""
    return value.value if isinstance(value, Enum) else value


def _json(config: Any, field: JsonConfigFieldName) -> Any:
    """Return a JSON-shaped setting validated like its command-line form, or None while it is unset."""
    if (value := getattr(config, field)) is None:
        return None
    from datamodel_code_generator.json_config import JsonConfigError, JsonConfigSpecs  # noqa: PLC0415

    try:
        return JsonConfigSpecs.by_field_name[field].validate({key: _plain(item) for key, item in value.items()})
    except JsonConfigError as error:
        raise Error(str(error)) from error


def target_of(
    config: Any, base: Callable[[str], Path], output: Path | None = None
) -> tuple[TargetGenerator, TargetConfig]:
    """Map the --server-* or --client-* settings of a config onto its target, leaving unset ones at their defaults.

    `base` names the directory the documents a setting's operation references name resolve against, and `output`
    replaces the package directory the config names.
    """
    if config.generate_client is None:
        return _server(config, base, config.server_output if output is None else output)
    return _client(config, base, config.client_output if output is None else output)


def _server(config: Any, base: Callable[[str], Path], output: Path) -> tuple[FastAPITarget, FastAPIConfig]:
    from datamodel_code_generator._fastapi.config import FastAPIConfig, ResponseChoice  # noqa: PLC0415
    from datamodel_code_generator._fastapi.target import FastAPITarget  # noqa: PLC0415

    values: dict[str, Any] = {
        name: _plain(value) for name in _SERVER_CHOICES if (value := getattr(config, f"server_{name}")) is not None
    }
    if (router_names := _json(config, "server_router_names")) is not None:
        values["router_names"] = router_names
    for field in _OPERATION_SETTINGS:
        if (entries := _json(config, field)) is not None:
            root = base(field)
            values[field.removeprefix("server_")] = {_operation(key, root): value for key, value in entries.items()}
    if (responses := _json(config, "server_primary_responses")) is not None:
        root = base("server_primary_responses")
        values["primary_responses"] = {
            _operation(key, root): ResponseChoice(status_code=choice.status_code, media_type=choice.media_type)
            for key, choice in responses.items()
        }
    return FastAPITarget(), FastAPIConfig(
        output=output, package=config.server_package, model_package=config.server_model_package, **values
    )


def _client(config: Any, base: Callable[[str], Path], output: Path) -> tuple[ClientTarget, ClientGenerationConfig]:
    from datamodel_code_generator._client.config import (  # noqa: PLC0415
        ClientGenerationConfig,
        ResourceName,
        operation_configs,
    )
    from datamodel_code_generator._client.target import ClientTarget  # noqa: PLC0415

    values: dict[str, Any] = {
        name: _plain(value) for name in _CLIENT_CHOICES if (value := getattr(config, f"client_{name}")) is not None
    }
    if (names := _json(config, "client_resource_names")) is not None:
        values["resource_names"] = tuple(ResourceName(tag=tag, namespace=name) for tag, name in names.items())
    if (entries := _json(config, "client_operations")) is not None:
        values["operations"] = operation_configs(entries, partial(_operation, base=base("client_operations")))
    return ClientTarget(base("client_protocols")), ClientGenerationConfig(
        output=output,
        package=config.client_package,
        model_package=config.client_model_package,
        transport=_plain(config.generate_client),
        protocols=_json(config, "client_protocols"),
        **values,
    )


def _operation(key: str, base: Path) -> OperationSelector:
    """Read an operation reference: a pointer into the root document, or a document and a pointer joined by #."""
    document, separator, pointer = key.partition("#")
    if not separator:
        return key
    return OperationRef(pointer=pointer, document=document_identity(document, base) if document else None)


def keyed(error: Exception, config: Any) -> Exception:
    """Name the operation and resource client settings in an error by the keys they were given under.

    A parameter name is named by its location and name, and a body field name by its media type and property.
    """
    if not isinstance(error, APIGenerationError) or config.generate_client is None:
        return error
    keys = {
        "operations": list(config.client_operations or ()),
        "resource_names": list(config.client_resource_names or ()),
    }

    def named(item: Diagnostic) -> Diagnostic:
        if (path := item.option_path) is None or (found := _INDEXED.match(path)) is None:
            return item
        spelled = f"{found[1]}[{(key := keys[found[1]][int(found[2])])!r}]"
        if (member := found[3]) is not None:
            names = config.client_operations[key][member]
            members = (
                [f"[{name!r}]" for name in names]
                if member == "parameter_names"
                else [f"[{media!r}][{name!r}]" for media, fields in names.items() for name in fields]
            )
            spelled += f".{member}{members[int(found[4])]}"
        return replace(item, option_path=f"{spelled}{path[found.end() :]}")

    if (diagnostics := tuple(map(named, error.diagnostics))) == error.diagnostics:
        return error
    from datamodel_code_generator._client.config import OPTION_PREFIX  # noqa: PLC0415

    return APIGenerationError(diagnostics, option_prefix=OPTION_PREFIX)


def generate_selected_target(input_: _GenerationInput, config: GenerateConfig) -> GeneratedModules | None:
    """Generate the models and the server or client package a config selects, as generate() does for models.

    With an output, every file is written together and nothing is returned. Without one, nothing but the model
    metadata and a remote lock update is written, and every model and package file is returned under the path its
    import path implies, as a write run in the working directory would generate it.
    """
    from datamodel_code_generator.base_config import _missing_target_options  # noqa: PLC0415

    if (missing := _missing_target_options(config, output=config.output is not None)) is not None:
        raise Error(missing)
    with PROCESS_STATE_LOCK:
        cwd = Path.cwd()
    bases = config._target_document_bases or {}  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    try:
        if config.output is not None:
            _generate(input_, config, cwd, bases)
            return None
        return _render(input_, config, cwd, bases)
    except APIGenerationError as error:
        if (named := keyed(error, config)) is error:
            raise
        raise named from None


def _generate(input_: _GenerationInput, config: GenerateConfig, cwd: Path, bases: dict[str, Path]) -> None:
    from datamodel_code_generator._api_generation import generate_target  # noqa: PLC0415

    generator, target = target_of(config, lambda field: bases.get(field, cwd))
    generate_target(input_, model_config=config, config=target, generator=generator)


def _render(input_: _GenerationInput, config: GenerateConfig, cwd: Path, bases: dict[str, Path]) -> GeneratedModules:
    """Render the target under a private directory of the working directory and publish only the side files."""
    from tempfile import TemporaryDirectory  # noqa: PLC0415

    from datamodel_code_generator._api_generation import plan_target, publish_target  # noqa: PLC0415

    selected = "server" if config.generate_client is None else "client"
    with TemporaryDirectory(prefix=".datamodel-codegen-", dir=cwd) as directory:
        root = Path(directory)
        package = getattr(config, f"{selected}_package")
        generator, target = target_of(config, lambda field: bases.get(field, cwd), root.joinpath(*package.split(".")))
        models = root.joinpath(*target.model_package.split("."))
        updates: dict[str, Any] = {"output": models}
        for field in ("settings_path", "http_local_ref_path"):
            if (path := getattr(config, field)) is not None and not path.expanduser().is_absolute():
                updates[field] = cwd / path.expanduser()
        try:
            planned = plan_target(
                input_,
                model_config=config.model_copy(update=updates),
                config=target,
                generator=generator,
                models_module=True,
                shown_root=root,
            )
        except APIGenerationError as error:
            if (staged := _staged(error, root.relative_to(cwd).as_posix())) is error:
                raise
            raise staged from None
        artifacts = planned.project.artifacts
        publish_target(
            replace(
                planned,
                project=replace(
                    planned.project,
                    artifacts=tuple(item for item in artifacts if item.kind in {"model_metadata", "remote_lock"}),
                ),
            )
        )
        return {
            item.path.relative_to(root).parts: _text(item, config.encoding)
            for item in artifacts
            if item.kind in {"model", "target"}
        }


def _staged(error: APIGenerationError, root: str) -> APIGenerationError:
    """Name the files of a failed render by their paths under the private directory, as the returned keys name them."""
    prefix = f"{root}/"
    diagnostics = tuple(
        replace(item, artifact_path=path.removeprefix(prefix))
        if (path := item.artifact_path) is not None and path.startswith(prefix)
        else item
        for item in error.diagnostics
    )
    return error if diagnostics == error.diagnostics else APIGenerationError(diagnostics)


def _text(artifact: GeneratedArtifact, encoding: str) -> str:
    """Read a rendered file as its written file reads: Python files in the model encoding, other text as UTF-8."""
    from io import BytesIO, TextIOWrapper  # noqa: PLC0415

    python = artifact.kind == "model" or artifact.path.suffix in {".py", ".pyi"}
    return TextIOWrapper(BytesIO(artifact.content), encoding=encoding if python else "utf-8").read()
