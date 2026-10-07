"""Build browser playground assets from Python sources."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import inspect
import json
import os
import shutil
import sys
import textwrap
from pathlib import Path
from typing import Any, cast

from datamodel_code_generator.arguments import arg_parser
from datamodel_code_generator.cli_options import (
    CLI_OPTION_META,
    CLI_REFERENCE_ROOT,
    OPTION_RELATION_KINDS,
    CLIOptionRelation,
    OptionCategory,
    get_cli_option_doc_path,
    get_option_meta,
)
from datamodel_code_generator.enums import InputFileType
from datamodel_code_generator.input_model import load_model_schema

ROOT = Path(__file__).resolve().parents[1]
PLAYGROUND_ROOT = ROOT / "docs" / "assets" / "playground"
GENERATED_ROOT = PLAYGROUND_ROOT / "generated"
METADATA_PATH = GENERATED_ROOT / "codegen-ui-metadata.json"
MAIN_METADATA_PATH = GENERATED_ROOT / "main" / "codegen-ui-metadata.json"
APP_SHELL_PATH = GENERATED_ROOT / "app-shell.html"
APP_SOURCE_PATH = PLAYGROUND_ROOT / "app.py"
RUNTIME_SOURCE_PATH = PLAYGROUND_ROOT / "runtime.py"
GENERATED_RUNTIME_PATH = GENERATED_ROOT / "runtime.py"
VERSIONS_PATH = GENERATED_ROOT / "playground-versions.json"
DEFAULT_MAIN_ASSET_BASE = "https://dev.datamodel-code-generator.pages.dev/playground/generated/"

BROWSER_SUPPORTED_INPUT_TYPES = {
    InputFileType.Auto.value,
    InputFileType.OpenAPI.value,
    InputFileType.AsyncAPI.value,
    InputFileType.JsonSchema.value,
    InputFileType.MCPTools.value,
    InputFileType.XMLSchema.value,
    InputFileType.Avro.value,
    InputFileType.Json.value,
    InputFileType.Yaml.value,
    InputFileType.Dict.value,
    InputFileType.CSV.value,
    InputFileType.GraphQL.value,
}

# Options the playground supplies through its own UI instead of the options form.
UI_PROVIDED_OPTIONS = {
    "input": "Use the schema editor.",
    "input_file_type": "Use the top input selector.",
    "output": "Output is shown in the browser.",
}

# Capabilities the browser build does not have. These are valid generate() parameters,
# so they cannot be detected from the GenerateConfig field types and are listed explicitly
# (network access, optional extras, and local Python modules). Everything else is classified
# dynamically from the GenerateConfig schema by _option_support() below.
BROWSER_UNAVAILABLE_OPTIONS = {
    "custom_template_dir": "Custom Jinja2 templates are not available in the browser playground.",
    "url": "Remote input URLs are not fetched by the browser playground.",
    "input_model": "Python module imports are not available in the browser playground.",
    "allow_remote_refs": "Resolving remote $refs needs network access.",
    "http_headers": "HTTP requests are not available in the browser playground.",
    "http_backend": "HTTP requests are not available in the browser playground.",
    "http_ignore_tls": "HTTP requests are not available in the browser playground.",
    "http_local_ref_path": "HTTP requests are not available in the browser playground.",
    "http_query_parameters": "HTTP requests are not available in the browser playground.",
    "http_timeout": "HTTP requests are not available in the browser playground.",
    "validation": "Requires the optional 'validation' extra, which is not installed in the browser build.",
    "use_pendulum": "Requires the 'pendulum' package, which is not installed in the browser build.",
    "formatters": "The browser playground always uses the dependency-free builtin formatter.",
    "custom_formatters": "Imports Python formatter modules that are not available in the browser.",
    "custom_formatters_kwargs": "Configures custom formatter modules, which are not available in the browser.",
}

CATEGORY_ORDER = [
    OptionCategory.GENERAL.value,
    OptionCategory.BASE.value,
    OptionCategory.MODEL.value,
    OptionCategory.TEMPLATE.value,
    OptionCategory.FIELD.value,
    OptionCategory.TYPING.value,
    OptionCategory.OPENAPI.value,
    OptionCategory.GRAPHQL.value,
    OptionCategory.TARGET.value,
]


def _long_options(action: argparse.Action) -> list[str]:
    return [option for option in action.option_strings if option.startswith("--")]


def _option_name(action: argparse.Action) -> str | None:
    options = _long_options(action)
    for option in options:
        if not option.startswith("--no-"):
            return option
    return options[0] if options else None


def _negative_name(action: argparse.Action) -> str | None:
    for option in _long_options(action):
        if option.startswith("--no-"):
            return option
    return None


def _choices(action: argparse.Action) -> list[str]:
    if not action.choices:
        return []
    return [getattr(choice, "value", str(choice)) for choice in action.choices]


def _control(action: argparse.Action) -> str:
    store_boolean_actions = (argparse._StoreTrueAction, argparse._StoreFalseAction)  # noqa: SLF001
    if action.nargs in {"+", "*"}:
        return "list"
    if action.choices:
        return "select"
    if isinstance(action, argparse.BooleanOptionalAction):
        return "boolean"
    if isinstance(action, store_boolean_actions):
        return "checkbox"
    if action.type in {int, float}:
        return "number"
    return "text"


def _clean_help(action: argparse.Action) -> str:
    if action.help in {None, argparse.SUPPRESS}:
        return ""
    return str(action.help).replace("%(default)s", str(action.default))


def _option_target_index(actions: list[argparse.Action]) -> dict[str, dict[str, str]]:
    targets: dict[str, dict[str, str]] = {}
    for action in actions:
        if not (name := _option_name(action)):
            continue
        target = {
            "dest": action.dest,
            "config_dest": ARG_FIELD_RENAMES.get(action.dest, action.dest),
        }
        targets[name] = target
        if negative_name := _negative_name(action):
            targets[negative_name] = target
    return targets


def _relation_metadata(relation: CLIOptionRelation, option_targets: dict[str, dict[str, str]]) -> dict[str, Any]:
    item = {"option": relation.option}
    if relation.value is not None:
        item["value"] = relation.value
    if relation.when is not None:
        item["when"] = relation.when
    if relation.message:
        item["message"] = relation.message
    if target := option_targets.get(relation.option):
        item.update(target)
    return item


def _relations_metadata(
    relations: tuple[CLIOptionRelation, ...], option_targets: dict[str, dict[str, str]]
) -> list[dict[str, Any]]:
    return [_relation_metadata(relation, option_targets) for relation in relations]


def _arg_field_renames() -> dict[str, str]:
    """Argparse dests that the CLI passes to generate() under a different name.

    The CLI's run_generate_from_config() renames a few arguments (for example
    ``use_default`` -> ``apply_default_values_for_required_fields``). Parsing that bridge keeps
    the playground in sync automatically instead of hard-coding the renames here.
    """
    from datamodel_code_generator import __main__ as cli_main  # noqa: PLC0415

    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(cli_main.run_generate_from_config)))
    except (OSError, TypeError, SyntaxError):
        return {}
    renames: dict[str, str] = {}
    for node in ast.walk(tree):
        match node:
            case ast.Call(func=ast.Name(id="generate"), keywords=keywords):
                for keyword in keywords:
                    match keyword:
                        case ast.keyword(
                            arg=str() as field, value=ast.Attribute(value=ast.Name(id="config"), attr=source)
                        ):
                            renames[source] = field
    return renames


ARG_FIELD_RENAMES = _arg_field_renames()
# dcg can introspect its own config model: load_model_schema() is the engine behind
# `datamodel-codegen --input-model config.py:GenerateConfig`. We classify each option from the
# JSON Schema it produces, so the playground tracks GenerateConfig without hand-rolled type checks.
_GENERATE_SCHEMA = load_model_schema(
    ["datamodel_code_generator.config:GenerateConfig"],
    InputFileType.JsonSchema,
)
GENERATE_FIELDS: dict[str, Any] = cast("dict[str, Any]", _GENERATE_SCHEMA.get("properties") or {})
_GENERATE_DEFS: dict[str, Any] = cast("dict[str, Any]", _GENERATE_SCHEMA.get("$defs") or {})


def _schema_variants(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten anyOf/oneOf and resolve $ref into concrete, non-null sub-schemas."""
    match schema:
        case {"anyOf": list() as members} | {"oneOf": list() as members}:
            return [variant for member in members for variant in _schema_variants(member)]
        case {"$ref": str() as ref}:
            return _schema_variants(_GENERATE_DEFS.get(ref.rsplit("/", 1)[-1], {}))
        case {"type": "null"}:
            return []
    return [schema]


def _field_kind(config_dest: str, control: str) -> tuple[bool, str | None]:
    """Return ``(is_path, value_kind)`` for a generate() field from its JSON Schema.

    object / additionalProperties -> a dict (inline JSON, or ``key=value`` for list widgets);
    array -> a list; a string with ``format: path`` -> a local path (unsupported in the browser).
    """
    for variant in _schema_variants(GENERATE_FIELDS[config_dest]):
        match variant:
            case {"type": "object"} | {"additionalProperties": _}:
                return False, "key_value" if control == "list" else "json"
            case {"type": "array"}:
                return False, "collection"
            case {"format": "path"}:
                return True, None
    return False, None


def _option_support(dest: str, config_dest: str, control: str) -> tuple[str, str | None]:
    """Return ``(unsupported_reason, value_kind)`` for an option, derived from GenerateConfig.

    An option is unsupported when it is not a generate() parameter, needs a filesystem path, or
    is a known missing browser capability. Mapping/collection fields get a coercion hint so the
    playground can turn the form string into the right type.
    """
    if reason := UI_PROVIDED_OPTIONS.get(dest) or BROWSER_UNAVAILABLE_OPTIONS.get(dest):
        return reason, None
    if config_dest not in GENERATE_FIELDS:
        return "CLI-only action: not a code-generation option, so it cannot run in the browser.", None
    is_path, value_kind = _field_kind(config_dest, control)
    if is_path:
        return "Needs a path on the local filesystem, which the browser playground does not have.", None
    return "", value_kind


def _option_metadata(action: argparse.Action, option_targets: dict[str, dict[str, str]]) -> dict[str, Any] | None:
    name = _option_name(action)
    if not name:
        return None

    meta = CLI_OPTION_META.get(name) or get_option_meta(name)
    control = _control(action)
    config_dest = ARG_FIELD_RENAMES.get(action.dest, action.dest)
    unsupported_reason, value_kind = _option_support(action.dest, config_dest, control)
    browser_supported = not unsupported_reason
    option_metadata = {
        "name": name,
        "negative_name": _negative_name(action),
        "dest": action.dest,
        "config_dest": config_dest,
        "value_kind": value_kind,
        "label": name.removeprefix("--"),
        "category": meta.category.value if meta else OptionCategory.GENERAL.value,
        "docs_url": get_cli_option_doc_path(name, root=f"/{CLI_REFERENCE_ROOT}", extension="/"),
        "control": control,
        "choices": _choices(action),
        "help": _clean_help(action),
        "browser_supported": browser_supported,
        "unsupported_reason": unsupported_reason,
        "deprecated": bool(meta and meta.deprecated),
        "deprecated_message": meta.deprecated_message if meta else None,
        "hidden": not browser_supported,
    }
    if meta:
        for relation_kind in OPTION_RELATION_KINDS:
            if relations := _relations_metadata(getattr(meta, relation_kind), option_targets):
                option_metadata[relation_kind] = relations
    return option_metadata


def _input_formats() -> list[dict[str, Any]]:
    formats = []
    for item in InputFileType:
        supported = item.value in BROWSER_SUPPORTED_INPUT_TYPES
        formats.append({
            "name": item.name,
            "value": item.value,
            "label": item.name,
            "browser_supported": supported,
            "unsupported_reason": "" if supported else "Not available in the browser playground.",
        })
    return formats


def find_package_wheel() -> str | None:
    """Return the generated package wheel filename when the deploy build created one."""
    wheels = sorted(GENERATED_ROOT.glob("datamodel_code_generator-*.whl"), key=lambda path: path.stat().st_mtime)
    return wheels[-1].name if wheels else None


def build_metadata() -> dict[str, Any]:
    """Return playground metadata derived from the current CLI parser."""
    category_values = {category.value for category in OptionCategory}
    category_order_values = set(CATEGORY_ORDER)
    if category_values != category_order_values:
        missing = sorted(category_values - category_order_values)
        extra = sorted(category_order_values - category_values)
        message = f"CATEGORY_ORDER mismatch: missing={missing}, extra={extra}"
        raise ValueError(message)

    actions = arg_parser._actions  # noqa: SLF001
    option_targets = _option_target_index(actions)
    options = []
    for action in actions:
        option = _option_metadata(action, option_targets)
        if option is not None:
            options.append(option)
    category_indexes = {category: index for index, category in enumerate(CATEGORY_ORDER)}
    options.sort(key=lambda option: (category_indexes.get(option["category"], len(category_indexes)), option["name"]))
    groups = [
        {
            "category": category,
            "options": [option for option in options if option["category"] == category],
        }
        for category in CATEGORY_ORDER
        if any(option["category"] == category for option in options)
    ]
    metadata = {
        "formats": _input_formats(),
        "options": options,
        "groups": groups,
    }
    if package_wheel := find_package_wheel():
        metadata["package_wheel"] = package_wheel
    return metadata


def _json_env_list(name: str) -> list[dict[str, Any]]:
    if not (raw_versions := os.environ.get(name)):
        return []
    try:
        versions = json.loads(raw_versions)
    except json.JSONDecodeError as exc:
        msg = f"{name} is not valid JSON: {exc}"
        raise ValueError(msg) from exc
    if not isinstance(versions, list) or not all(isinstance(version, dict) for version in versions):
        msg = f"{name} must be a JSON array of objects"
        raise TypeError(msg)
    return cast("list[dict[str, Any]]", versions)


def _extra_versions() -> list[dict[str, Any]]:
    return _json_env_list("PLAYGROUND_EXTRA_VERSIONS_JSON")


def _release_versions() -> list[dict[str, Any]]:
    return _json_env_list("PLAYGROUND_RELEASES_JSON")


def _json_file_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return cast("dict[str, Any]", data)


def _wheel_install(metadata: dict[str, Any]) -> dict[str, Any] | None:
    if not (package_wheel := metadata.get("package_wheel")):
        return None
    return {
        "type": "wheel",
        "url": package_wheel,
        "deps": False,
    }


def _deploy_kind() -> str:
    match os.environ.get("PLAYGROUND_DEPLOY_BRANCH", ""):
        case branch if branch.startswith("pr-"):
            return "preview"
        case "dev":
            return "main"
        case "main":
            return "production"
    return "current"


def _current_version(
    metadata: dict[str, Any],
    *,
    version_id: str,
    label: str,
    kind: str,
) -> dict[str, Any]:
    version = {
        "id": version_id,
        "label": label,
        "kind": kind,
        "asset_base": "./generated/",
    }
    if source_ref := os.environ.get("PLAYGROUND_SOURCE_REF"):
        version["source_ref"] = source_ref

    if install := _wheel_install(metadata):
        version["install"] = install
    else:
        version["install"] = {
            "type": "requirement",
            "requirement": "datamodel-code-generator",
            "deps": False,
        }
    version["app"] = "runtime.py"
    return version


def _main_version(metadata: dict[str, Any], *, local: bool) -> dict[str, Any]:
    if local:
        return _current_version(metadata, version_id="main", label="main", kind="main")
    version = {
        "id": "main",
        "label": "main",
        "kind": "main",
        "asset_base": os.environ.get("PLAYGROUND_MAIN_ASSET_BASE") or DEFAULT_MAIN_ASSET_BASE,
        "app": "runtime.py",
    }
    if install := _wheel_install(_json_file_dict(MAIN_METADATA_PATH)):
        version["install"] = install
    return version


def _release_version(version: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    checkout_ref = os.environ.get("PLAYGROUND_CHECKOUT_REF")
    result = dict(version)
    if result.get("id") == checkout_ref and (install := _wheel_install(metadata)):
        result["install"] = install
    result.setdefault("asset_base", "./generated/")
    result.setdefault("app", "runtime.py")
    return result


def _version_list(metadata: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    releases = [_release_version(version, metadata) for version in _release_versions()]
    match _deploy_kind():
        case "preview":
            current = _current_version(metadata, version_id="current", label="Preview build", kind="preview")
            return current["id"], [current, *releases, _main_version(metadata, local=False), *_extra_versions()]
        case "main":
            main = _main_version(metadata, local=True)
            return main["id"], [main, *releases, *_extra_versions()]
        case "production":
            main = _main_version(metadata, local=False)
            default_id = releases[0]["id"] if releases else main["id"]
            return default_id, [*releases, main, *_extra_versions()]

    current = _current_version(
        metadata,
        version_id=os.environ.get("PLAYGROUND_VERSION_ID", "current"),
        label=os.environ.get("PLAYGROUND_VERSION_LABEL", "Current build"),
        kind=os.environ.get("PLAYGROUND_VERSION_KIND", "current"),
    )
    return os.environ.get("PLAYGROUND_DEFAULT_VERSION", current["id"]), [current, *_extra_versions()]


def build_versions(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return runtime version entries for the browser playground."""
    default_id, versions = _version_list(metadata)

    return {
        "schema_version": 1,
        "default": os.environ.get("PLAYGROUND_DEFAULT_VERSION", default_id),
        "versions": versions,
    }


def _load_playground_app() -> Any:
    spec = importlib.util.spec_from_file_location("playground_app", APP_SOURCE_PATH)
    if spec is None or spec.loader is None:
        message = "Could not load playground app module"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    """Write generated playground assets."""
    metadata = build_metadata()
    metadata_json = json.dumps(metadata, indent=2, ensure_ascii=False) + "\n"
    GENERATED_ROOT.mkdir(parents=True, exist_ok=True)
    METADATA_PATH.write_text(metadata_json, encoding="utf-8")
    shutil.copyfile(RUNTIME_SOURCE_PATH, GENERATED_RUNTIME_PATH)

    app = _load_playground_app()
    app.set_ui_metadata(metadata_json)
    APP_SHELL_PATH.write_text(
        app.render_app() + "\n",
        encoding="utf-8",
    )
    VERSIONS_PATH.write_text(
        json.dumps(build_versions(metadata), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Generated {METADATA_PATH.relative_to(ROOT)}")
    print(f"Generated {GENERATED_RUNTIME_PATH.relative_to(ROOT)}")
    print(f"Generated {APP_SHELL_PATH.relative_to(ROOT)}")
    print(f"Generated {VERSIONS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    if sys.version_info < (3, 14):
        message = "Playground assets must be built with Python 3.14+ for t-string syntax."
        raise SystemExit(message)
    main()
