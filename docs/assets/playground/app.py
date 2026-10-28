from __future__ import annotations

import json
import tomllib
import traceback
from typing import TYPE_CHECKING, Any

from tdom import html

from datamodel_code_generator import InputFileType, generate
from datamodel_code_generator.__main__ import EXCLUDED_CONFIG_OPTIONS, generate_cli_command
from datamodel_code_generator.format import Formatter

if TYPE_CHECKING:
    from string.templatelib import Template

SAMPLE_SCHEMA = """{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Pet",
  "type": "object",
  "properties": {
    "id": { "type": "integer" },
    "name": { "type": "string" },
    "tags": {
      "type": "array",
      "items": { "type": "string" }
    }
  },
  "required": ["id", "name"]
}"""

SAMPLE_SCHEMAS = {
    "auto": SAMPLE_SCHEMA,
    "jsonschema": SAMPLE_SCHEMA,
    "openapi": """{
  "openapi": "3.1.0",
  "info": {
    "title": "Pet API",
    "version": "1.0.0"
  },
  "paths": {},
  "components": {
    "schemas": {
      "Pet": {
        "type": "object",
        "properties": {
          "id": { "type": "integer" },
          "name": { "type": "string" },
          "tags": {
            "type": "array",
            "items": { "type": "string" }
          }
        },
        "required": ["id", "name"]
      }
    }
  }
}""",
    "json": """{
  "id": 1,
  "name": "Milo",
  "tags": ["cat", "indoor"]
}""",
    "yaml": """id: 1
name: Milo
tags:
  - cat
  - indoor
""",
    "dict": """{
  "id": 1,
  "name": "Milo",
  "tags": ["cat", "indoor"]
}""",
    "csv": """id,name,age
1,Milo,3
2,Luna,2
""",
    "graphql": """type Pet {
  id: ID!
  name: String!
  tags: [String!]
}
""",
}

UI_METADATA: dict[str, Any] = {"formats": [], "options": [], "groups": []}
PLAYGROUND_FORMATTERS = [Formatter.BUILTIN]
GENERATE_FORCED_OPTIONS = {
    "output": None,
    "formatters": PLAYGROUND_FORMATTERS,
}
CONFIG_FORCED_OPTIONS = {
    "formatters": [formatter.value for formatter in PLAYGROUND_FORMATTERS],
}
CONFIG_TABLE = "tool.datamodel-codegen"
SECURITY_TOOLTIP = (
    "Generation runs locally in your browser with Pyodide. Schemas and options are not uploaded. "
    "Copy Repro URL stores them in the #state URL fragment, which is not sent to the server; "
    "only the optional ?version query is sent as a normal page request. The full shared URL can still be saved "
    "in browser history or services where you paste it."
)


def set_ui_metadata(metadata_json: str) -> None:
    global UI_METADATA  # noqa: PLW0603
    UI_METADATA = json.loads(metadata_json)


def ActionButton(
    *,
    action: str,
    label: str,
    disabled: bool = False,
    children: Template = t"",
    **attrs: Any,
) -> Template:
    return t"""<button type="button" data-action="{action}" disabled={disabled} {attrs}>{label}{children}</button>"""


def InputTypeSelect(*, selected: str, children: Template = t"", **attrs: Any) -> Template:
    options = [
        t"""<option value="{item["value"]}" selected={item["value"] == selected} disabled={not item["browser_supported"]}>{item["label"]}</option>"""
        for item in UI_METADATA["formats"]
    ]
    return t"""
    <label class="input-type-control" {attrs}>
      Input
      <select id="input-type">{options}</select>
    </label>
    """


def VersionSelect(*, children: Template = t"", **attrs: Any) -> Template:
    return t"""
    <label class="version-control" {attrs}>
      Version
      <select id="playground-version" disabled>
        <option value="current" selected>Current build</option>
      </select>
    </label>
    """


def Pane(
    *,
    title: str,
    action: str | None = None,
    action_label: str = "",
    extra: Template = t"",
    children: Template = t"",
) -> Template:
    action_button = t"""<{ActionButton} action="{action}" label="{action_label}" id="{action}" />""" if action else t""
    return t"""
    <div class="pane">
      <div class="pane-title">
        <span>{title}</span>
        <div class="pane-actions">{extra}{action_button}</div>
      </div>
      {children}
    </div>
    """


def OptionControl(*, option: dict[str, Any], children: Template = t"") -> Template:
    dest = option["dest"]
    control_id = f"option-{dest.replace('_', '-')}"
    choices = option.get("choices") or []
    docs_url = option.get("docs_url") or ""
    note = (
        t"""<span class="option-note">{reason}</span>""" if (reason := option.get("unsupported_reason") or "") else t""
    )
    docs_link = (
        t"""
        <a class="option-doc-link" href="{docs_url}" target="_blank" rel="noreferrer" aria-label="Open documentation for {option["name"]}">
          Docs
        </a>
        """
        if docs_url
        else t""
    )

    match option["control"]:
        case "checkbox":
            control = t"""<input id="{control_id}" type="checkbox" name="{dest}" value="true" data-option="{dest}" />"""
        case "boolean":
            control = t"""
            <select id="{control_id}" name="{dest}" data-option="{dest}">
              <option value="">Default</option>
              <option value="true">True</option>
              <option value="false">False</option>
            </select>
            """
        case "select":
            control = t"""
            <select id="{control_id}" name="{dest}" data-option="{dest}">
              <option value="">Default</option>
              {[t'<option value="{choice}">{choice}</option>' for choice in choices]}
            </select>
            """
        case "number":
            control = t"""<input id="{control_id}" type="number" name="{dest}" data-option="{dest}" />"""
        case "list":
            control = t"""<textarea id="{control_id}" class="option-list" rows="2" name="{dest}" data-option="{dest}"></textarea>"""
        case _:
            control = t"""<input id="{control_id}" type="text" name="{dest}" data-option="{dest}" />"""

    return t"""
    <div class="option-row option-control-{option["control"]}" title="{option["help"]}">
      <label class="option-label" for="{control_id}">
        <code>{option["name"]}</code>
        <span>{option["help"]}</span>
      </label>
      {docs_link}
      <span class="option-widget">{control}</span>
      {note}
    </div>
    """


def OptionGroup(*, group: dict[str, Any], open: bool = False, children: Template = t"") -> Template:
    options = [t"""<{OptionControl} option={option} />""" for option in group["options"] if not option.get("hidden")]
    if not options:
        return t""
    return t"""
    <details class="option-group" open={open}>
      <summary>
        <span>{group["category"]}</span>
        <span>{len(options)}</span>
      </summary>
      <div class="option-list-panel">{options}</div>
    </details>
    """


def OptionsPanel(*, groups: list[dict[str, Any]], children: Template = t"") -> Template:
    visible_count = sum(1 for option in UI_METADATA["options"] if not option.get("hidden"))
    rendered_groups = [
        t"""<{OptionGroup} group={group} open={index < 2} />"""
        for index, group in enumerate(groups)
        if any(not option.get("hidden") for option in group["options"])
    ]
    return t"""
    <section class="options-panel">
      <div class="options-head">
        <div>
          <h2>Options</h2>
          <span class="options-count">{visible_count} available</span>
        </div>
        <input id="option-filter" type="search" placeholder="Filter options..." aria-label="Filter options" autocomplete="off" />
      </div>
      <form id="options-form">{rendered_groups}</form>
    </section>
    """


def ConfigDialog(*, children: Template = t"") -> Template:
    return t"""
    <dialog id="config-dialog" class="config-dialog">
      <form method="dialog" class="config-dialog-body">
        <div class="config-dialog-head">
          <h2>pyproject.toml</h2>
          <{ActionButton} action="close-config" label="Close" />
        </div>
        <textarea id="config-toml" spellcheck="false"></textarea>
        <div class="config-dialog-actions">
          <{ActionButton} action="copy-config" label="Copy TOML" />
          <{ActionButton} action="import-config" label="Import TOML" />
        </div>
      </form>
    </dialog>
    """


def App(
    *,
    schema: str,
    input_type: str,
    children: Template = t"",
) -> Template:
    schema_actions = t"""
      <{InputTypeSelect} selected="{input_type}" />
      <{ActionButton} action="sample" label="Sample" id="sample" />
      <{ActionButton} action="clear-input" label="Clear" id="clear-input" />
    """
    output_actions = t"""
      <{ActionButton} action="copy" label="Copy" id="copy" />
      <{ActionButton} action="clear-output" label="Clear" id="clear-output" />
    """
    return t"""
    <main class="shell">
      <header class="topbar">
        <div class="topbar-title">
          <h1>
            <a href="https://github.com/datamodel-code-generator/datamodel-code-generator" target="_blank" rel="noreferrer">datamodel-code-generator</a>
            <span>Browser</span>
          </h1>
        </div>
        <nav class="docs-nav" aria-label="Documentation">
          <a href="/">Docs</a>
          <a href="/cli-reference/">CLI Reference</a>
        </nav>
        <div class="controls">
          <{VersionSelect} />
          <{ActionButton} action="toggle-header" label="Compact Header" id="toggle-header" />
          <{ActionButton} action="toggle-workspace" label="Hide Editors" id="toggle-workspace" />
          <{ActionButton} action="copy-cli" label="Copy CLI" id="copy-cli" disabled={True} />
          <{ActionButton} action="copy-repro-url" label="Copy Repro URL" id="copy-repro-url" />
          <{ActionButton} action="config" label="pyproject.toml" id="config" disabled={True} />
          <{ActionButton} action="auto-generate" label="Auto Generate: On" id="auto-generate" disabled={True} />
          <{ActionButton} action="generate" label="Generate" disabled={True} id="generate" />
        </div>
        <div class="topbar-meta">
          <p class="tech-note">
            UI rendered with <a href="https://github.com/t-strings/tdom" target="_blank" rel="noreferrer">tdom</a>
            using <a href="https://peps.python.org/pep-0750/" target="_blank" rel="noreferrer">PEP 750 t-strings</a>.
          </p>
          <span
            class="security-note"
            tabindex="0"
            data-tooltip="{SECURITY_TOOLTIP}"
          >
            Security: local execution
          </span>
          <p id="status">Loading Pyodide runtime...</p>
        </div>
      </header>

      <section class="workspace" aria-label="Generator workspace">
        <{Pane} title="Schema" extra={schema_actions}>
          <textarea id="schema" spellcheck="false">{schema}</textarea>
        </{Pane}>

        <div
          class="workspace-splitter"
          role="separator"
          aria-orientation="vertical"
          aria-label="Drag to resize panes (double-click to reset)"
          title="Drag to resize panes (double-click to reset)"
        ></div>

        <{Pane} title="Output" extra={output_actions}>
          <pre id="output" aria-live="polite"></pre>
        </{Pane}>
      </section>

      <{OptionsPanel} groups={UI_METADATA["groups"]} />
      <{ConfigDialog} />
    </main>
    """


def default_input_type() -> str:
    for item in UI_METADATA["formats"]:
        if item["value"] == "jsonschema":
            return "jsonschema"
    return UI_METADATA["formats"][0]["value"] if UI_METADATA["formats"] else "jsonschema"


def render_app(
    schema: str = "",
    input_type: str = "",
) -> str:
    return html(
        t"""<{App}
  schema={schema or SAMPLE_SCHEMA}
  input-type={input_type or default_input_type()}
 />"""
    )


def sample_schema(input_type: str = "") -> str:
    return SAMPLE_SCHEMAS.get(input_type or default_input_type(), SAMPLE_SCHEMA)


def export_ui_options() -> str:
    return json.dumps(UI_METADATA, ensure_ascii=False)


def _options_by_dest() -> dict[str, dict[str, Any]]:
    return {option["dest"]: option for option in UI_METADATA["options"] if option["browser_supported"]}


def _options_by_name() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for option in UI_METADATA["options"]:
        if not option["browser_supported"]:
            continue
        result[option["name"]] = option
        if negative_name := option.get("negative_name"):
            result[negative_name] = option
    return result


def _split_delimited(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).replace(",", "\n").splitlines() if item.strip()]


def _coerce_option_value(option: dict[str, Any], value: Any) -> Any:
    """Coerce a form value by widget type. Used for the CLI command text."""
    if value == "" or value is None or value == []:
        return None

    result: Any
    match option["control"], value:
        case ("checkbox" | "boolean", True | "true"):
            result = True
        case ("checkbox" | "boolean", False | "false"):
            result = False
        case ("checkbox" | "boolean", _):
            result = None
        case ("list", list()):
            result = value
        case ("list", _):
            result = _split_delimited(value)
        case ("number", _):
            text = str(value)
            result = float(text) if "." in text else int(text)
        case _:
            result = value
    return result


def _coerce_for_generate(option: dict[str, Any], value: Any) -> Any:
    """Coerce a form value into the type generate() expects.

    value_kind is derived from the generate() field type (see build_playground_assets.py), so the
    string a widget produces is turned into a dict/list/etc. independent of the widget itself.
    """
    if (coerced := _coerce_option_value(option, value)) is None:
        return None
    match option.get("value_kind"):
        case "json":
            try:
                return json.loads(coerced) if isinstance(coerced, str) else coerced
            except json.JSONDecodeError:
                return None
        case "key_value":
            pairs = (item.partition("=") for item in _split_delimited(coerced))
            return {key: val for key, sep, val in pairs if sep and key} or None
        case "collection":
            return coerced if isinstance(coerced, list) else _split_delimited(coerced)
    return coerced


def _coerce_for_config(option: dict[str, Any], value: Any) -> Any:
    """Coerce a form value into a structured config value without renaming its CLI key."""
    return _coerce_for_generate(option, value)


def _relation_source_matches(value: Any, relation: dict[str, Any]) -> bool:
    value = getattr(value, "value", value)
    if "when" not in relation:
        return bool(value)
    expected = relation["when"]
    if isinstance(expected, list):
        return value in expected
    return value == expected


def _apply_generate_option_implications(options: dict[str, Any]) -> None:
    options_by_name = _options_by_name()
    changed = True
    while changed:
        changed = False
        for option in UI_METADATA["options"]:
            if not (relations := option.get("implies")):
                continue
            source_key = option.get("config_dest", option["dest"])
            if source_key not in options:
                continue
            for relation in relations:
                if not _relation_source_matches(options[source_key], relation):
                    continue
                if not (target := options_by_name.get(relation["option"])):
                    continue
                target_key = relation.get("config_dest", target.get("config_dest", target["dest"]))
                target_value = relation.get("value", True)
                if options.get(target_key) == target_value:
                    continue
                options[target_key] = target_value
                changed = True


def _normalize_options(
    options: dict[str, Any], *, include_forced: bool = True, for_config: bool = False, for_generate: bool = False
) -> dict[str, Any]:
    # for_generate=True keys by the generate() field name; for_config=True keeps the CLI dest but
    # emits structured values for TOML import/export. The default path is for CLI command text.
    options_by_dest = _options_by_dest()
    normalized: dict[str, Any] = {}
    for key, value in options.items():
        if (option := options_by_dest.get(key)) is None:
            continue
        if for_generate:
            normalized_value = _coerce_for_generate(option, value)
        elif for_config:
            normalized_value = _coerce_for_config(option, value)
        else:
            normalized_value = _coerce_option_value(option, value)
        if normalized_value is None or normalized_value == []:
            continue
        normalized[option.get("config_dest", key) if for_generate else key] = normalized_value
    if for_generate:
        _apply_generate_option_implications(normalized)
    if include_forced:
        normalized.update(GENERATE_FORCED_OPTIONS)
    return normalized


def _config_options(options_json: str = "{}", input_type: str = "") -> dict[str, Any]:
    options = json.loads(options_json) if options_json else {}
    normalized_options = _normalize_options(options, include_forced=False, for_config=True)
    normalized_options.update(CONFIG_FORCED_OPTIONS)
    if input_type and input_type != "auto":
        normalized_options["input_file_type"] = input_type
    return normalized_options


def _cli_options(options_json: str = "{}", input_type: str = "") -> dict[str, Any]:
    options = json.loads(options_json) if options_json else {}
    normalized_options = _normalize_options(options, include_forced=False)
    normalized_options.update(CONFIG_FORCED_OPTIONS)
    if input_type and input_type != "auto":
        normalized_options["input_file_type"] = input_type
    return normalized_options


def _format_toml_value(value: Any) -> str:
    match value:
        case bool():
            return "true" if value else "false"
        case int() | float():
            return str(value)
        case str():
            return json.dumps(value, ensure_ascii=False)
        case list() | tuple():
            return f"[{', '.join(_format_toml_value(item) for item in value)}]"
        case dict():
            items = ", ".join(
                f"{json.dumps(str(key), ensure_ascii=False)} = {_format_toml_value(item)}"
                for key, item in sorted(value.items())
            )
            return f"{{ {items} }}"
        case _:
            return json.dumps(str(value), ensure_ascii=False)


def export_config_toml(options_json: str = "{}", input_type: str = "") -> str:
    option_lines = [
        f"{key.replace('_', '-')} = {_format_toml_value(value)}"
        for key, value in sorted(_config_options(options_json, input_type).items())
        if key not in EXCLUDED_CONFIG_OPTIONS
    ]
    return "\n".join((f"[{CONFIG_TABLE}]", *option_lines)) + "\n"


def build_cli_options(options_json: str = "{}", input_type: str = "") -> str:
    return generate_cli_command(_cli_options(options_json, input_type)).strip()


def _json_result(**payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _config_table(data: dict[str, Any]) -> dict[str, Any]:
    tool_config = data.get("tool", {}).get("datamodel-codegen")
    return tool_config if isinstance(tool_config, dict) else data


def _form_option_value(option: dict[str, Any], value: Any) -> Any:
    """Convert a TOML value back into the text shape used by the option form."""
    match option.get("value_kind"), value:
        case "key_value", dict():
            return "\n".join(f"{key}={item}" for key, item in value.items())
        case "json", dict() | list():
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        case "collection", list() | tuple() | set():
            separator = "\n" if option["control"] == "list" else ","
            return separator.join(str(item) for item in value)
    return value


def _is_browser_formatter(value: Any) -> bool:
    return _split_delimited(value) == [Formatter.BUILTIN.value]


def import_config_toml(config_toml: str) -> str:
    try:
        config = _config_table(tomllib.loads(config_toml))
        if not isinstance(config, dict):
            return _json_result(ok=False, error=f"[{CONFIG_TABLE}] must be a table")

        options_by_dest = _options_by_dest()
        input_values = {item["value"] for item in UI_METADATA["formats"] if item["browser_supported"]}
        imported_options: dict[str, Any] = {}
        ignored: list[str] = []
        input_type = ""

        for raw_key, value in config.items():
            key = str(raw_key).replace("-", "_")
            match key:
                case "profiles":
                    ignored.append(str(raw_key))
                case "input_file_type" if (text_value := str(value)) in input_values:
                    input_type = text_value
                case "input_file_type":
                    ignored.append(str(raw_key))
                case "formatters" if _is_browser_formatter(value):
                    pass
                case _ if key in EXCLUDED_CONFIG_OPTIONS or key not in options_by_dest:
                    ignored.append(str(raw_key))
                case _:
                    imported_options[key] = _form_option_value(options_by_dest[key], value)

        return _json_result(ok=True, inputType=input_type, options=imported_options, ignored=ignored)
    except Exception as exc:
        return _json_result(ok=False, error=str(exc))


def generate_in_browser(schema: str, input_type: str, options_json: str = "{}") -> str:
    try:
        options = json.loads(options_json) if options_json else {}
        result = generate(
            schema,
            input_file_type=InputFileType(input_type),
            **_normalize_options(options, for_generate=True),
            disable_timestamp=True,
        )
    except Exception:
        return json.dumps({"ok": False, "error": traceback.format_exc()})

    if isinstance(result, dict):
        sections = []
        for path, content in sorted(result.items()):
            name = "/".join(path) if isinstance(path, tuple) else str(path)
            sections.append(f"# file: {name}\n{content}")
        result = "\n\n".join(sections)
    return json.dumps({"ok": True, "output": result or ""})
