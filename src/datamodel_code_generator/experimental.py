"""Central registry for experimental features."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from textwrap import fill
from typing import Literal

from datamodel_code_generator._registry_render import _render_registry_json, _render_registry_table

ExperimentalFeatureKind = Literal["input-format", "formatter", "cli-option", "python-api", "behavior", "extra"]
ExperimentalFeatureFormat = Literal["table", "json", "markdown"]
ExperimentalFeatureId = Literal[
    "behavior.batch-generation-jobs",
    "behavior.remote-reference-lock",
    "cli-option.generate-schema-validators",
    "cli-option.generate-server",
    "cli-option.install-skill",
    "cli-option.schema-validator-type",
    "cli-option.use-missing-sentinel",
    "cli-option.use-type-alias",
    "cli-option.use-type-alias-type",
    "extra.httpx2",
    "input-format.asyncapi",
    "input-format.avro",
    "input-format.mcp-tools",
    "input-format.protobuf",
    "input-format.xmlschema",
    "formatter.builtin",
    "python-api.fastapi-server",
]


@dataclass(frozen=True)
class ExperimentalFeature:
    """Structured metadata for an experimental feature entry."""

    id: ExperimentalFeatureId
    kind: ExperimentalFeatureKind
    target: str
    message: str
    since_version: str
    tracking_issue: str | None = None
    note: str | None = None


EXPERIMENTAL_FEATURES: dict[ExperimentalFeatureId, ExperimentalFeature] = {
    "behavior.batch-generation-jobs": ExperimentalFeature(
        id="behavior.batch-generation-jobs",
        kind="behavior",
        target="[tool.datamodel-codegen.jobs], --job, --all-jobs",
        message=(
            "Named batch jobs are experimental; their configuration schema, batch output, and transactional/watch "
            "execution contracts may change."
        ),
        since_version="0.72.3",
        note=(
            "Define named generation jobs in [tool.datamodel-codegen.jobs] and select them with --job or "
            "--all-jobs. Each selected job has its own input and output, and the full selection is validated "
            "before generation begins."
        ),
    ),
    "behavior.remote-reference-lock": ExperimentalFeature(
        id="behavior.remote-reference-lock",
        kind="behavior",
        target="datamodel-codegen.lock, --lockfile, --update-lock, and --locked",
        message=(
            "Remote reference integrity locking is experimental: the lock document schema and request-identity "
            "compatibility may evolve, but integrity mismatches remain fail-closed and credentials are never persisted."
        ),
        since_version="0.72.3",
        note=(
            "The lock stores opaque SHA-256 request-identity digests and SHA-256 body digests, never response "
            "bodies or request values directly. Each saved display origin contains only the scheme, host, and "
            "explicit port—never a path, query, or request headers."
        ),
    ),
    "extra.httpx2": ExperimentalFeature(
        id="extra.httpx2",
        kind="extra",
        target="datamodel-code-generator[httpx2]",
        message="The HTTPX2-backed HTTP client is experimental and may change as compatibility is validated.",
        since_version="0.71.1",
        note=(
            "datamodel-code-generator[http] remains the stable HTTPX backend and is not deprecated; "
            "datamodel-code-generator[httpx2] is experimental. The default HTTP backend policy is auto: stable "
            "httpx is selected when its client module is installed, including when both pairs are installed, and "
            "experimental httpx2 is selected only when that module is absent. Use --http-backend httpx2 or "
            "HTTPBackend.HTTPX2 to require the experimental pair. Explicit selections and paired dependency "
            "errors do not fall back."
        ),
    ),
    "cli-option.generate-schema-validators": ExperimentalFeature(
        id="cli-option.generate-schema-validators",
        kind="cli-option",
        target="--generate-schema-validators",
        message=(
            "Schema-derived runtime validators are experimental and may change as JSON Schema coverage is expanded."
        ),
        since_version="0.66.1",
        note=(
            "The option currently targets Pydantic v2 BaseModel output and covers selected object-level rules such as "
            "patternProperties, required-only oneOf/anyOf/not groups, and simple if/then/else required-property "
            "conditions."
        ),
    ),
    "cli-option.generate-server": ExperimentalFeature(
        id="cli-option.generate-server",
        kind="cli-option",
        target="--generate-server fastapi, --target-config, --target-output, and --diagnostics-json",
        message=(
            "FastAPI server generation from the CLI is experimental; its options, target configuration file, "
            "generated package, and diagnostics may change."
        ),
        since_version="0.83.1",
        note=(
            "--generate-server fastapi generates the models at --output and a server package from the flat TOML "
            "file --target-config names; --target-output overrides the file's output, --check compares without "
            "writing, and --diagnostics-json writes the target diagnostics as JSON."
        ),
    ),
    "cli-option.install-skill": ExperimentalFeature(
        id="cli-option.install-skill",
        kind="cli-option",
        target="--install-skill, --skill-scope, and --overwrite-skill",
        message=(
            "Bundled Agent Skill installation is experimental; supported clients, scopes, and installed skill "
            "contents may change."
        ),
        since_version="0.76.0",
        note=(
            "Use --install-skill with codex or claude-code. --skill-scope selects a project or user installation, "
            "and --overwrite-skill safely replaces an existing regular skill directory."
        ),
    ),
    "cli-option.schema-validator-type": ExperimentalFeature(
        id="cli-option.schema-validator-type",
        kind="cli-option",
        target="--schema-validator-type",
        message=(
            "Schema-derived runtime validator backend selection is experimental and may change as validation "
            "backends are added."
        ),
        since_version="0.66.1",
        note=(
            "The only currently implemented backend is 'pydantic-v2', which preserves the existing generated "
            "Pydantic v2 validator behavior."
        ),
    ),
    "cli-option.use-missing-sentinel": ExperimentalFeature(
        id="cli-option.use-missing-sentinel",
        kind="cli-option",
        target="--use-missing-sentinel",
        message=(
            "Pydantic MISSING sentinel output is experimental because it depends on "
            "pydantic.experimental.missing_sentinel."
        ),
        since_version="0.66.1",
        note=(
            "The option requires Pydantic v2 BaseModel output and a target Pydantic version that supports the "
            "MISSING sentinel."
        ),
    ),
    "cli-option.use-type-alias": ExperimentalFeature(
        id="cli-option.use-type-alias",
        kind="cli-option",
        target="--use-type-alias",
        message="Type alias output is experimental and may change as Python typing support evolves.",
        since_version="0.36.0",
        note=(
            "The option replaces root model classes with type aliases where possible. Pydantic v2 output may use "
            "TypeAliasType or Python 3.12 type statements depending on the target Python version."
        ),
    ),
    "cli-option.use-type-alias-type": ExperimentalFeature(
        id="cli-option.use-type-alias-type",
        kind="cli-option",
        target="--use-type-alias-type",
        message="Runtime TypeAliasType output is experimental and may change as Python typing support evolves.",
        since_version="0.71.1",
        note=(
            "The option implies --use-type-alias and selects TypeAliasType for Python 3.10 and 3.11. "
            "Python 3.12 and newer continue to use native type statements."
        ),
    ),
    "input-format.asyncapi": ExperimentalFeature(
        id="input-format.asyncapi",
        kind="input-format",
        target="--input-file-type asyncapi",
        message="AsyncAPI input support is experimental and may change as real-world usage is validated.",
        since_version="0.59.0",
        note="The parser focuses on message payload model generation from AsyncAPI documents.",
    ),
    "input-format.avro": ExperimentalFeature(
        id="input-format.avro",
        kind="input-format",
        target="--input-file-type avro",
        message="Apache Avro schema input support is experimental and may change as real-world usage is validated.",
        since_version="0.59.0",
        note="The parser generates Python models from Avro schemas; it does not provide Avro runtime validation.",
    ),
    "input-format.mcp-tools": ExperimentalFeature(
        id="input-format.mcp-tools",
        kind="input-format",
        target="--input-file-type mcp-tools",
        message="MCP tool schema profile input support is experimental and may change as MCP schemas evolve.",
        since_version="0.60.0",
        note=(
            "The input is converted from MCP tool inputSchema/outputSchema entries into JSON Schema definitions before "
            "model generation."
        ),
    ),
    "input-format.protobuf": ExperimentalFeature(
        id="input-format.protobuf",
        kind="input-format",
        target="--input-file-type protobuf",
        message="Protocol Buffers input support is experimental and may change as real-world usage is validated.",
        since_version="0.59.0",
        note=(
            "The parser generates Python models from .proto schemas; it does not provide protobuf runtime validation "
            "or gRPC code generation."
        ),
    ),
    "input-format.xmlschema": ExperimentalFeature(
        id="input-format.xmlschema",
        kind="input-format",
        target="--input-file-type xmlschema",
        message="XML Schema input support is experimental and may change as real-world usage is validated.",
        since_version="0.59.0",
        note="The parser focuses on model generation from XSD documents, not full XML instance validation.",
    ),
    "formatter.builtin": ExperimentalFeature(
        id="formatter.builtin",
        kind="formatter",
        target="--formatters builtin",
        message="The internal formatter is experimental and may change as generated-output coverage is expanded.",
        since_version="0.59.0",
        note="The formatter is designed for generated model modules and is not a general-purpose Python formatter.",
    ),
    "python-api.fastapi-server": ExperimentalFeature(
        id="python-api.fastapi-server",
        kind="python-api",
        target="datamodel_code_generator.fastapi and datamodel_code_generator.api_types",
        message=(
            "The FastAPI server target is experimental; its entry points, settings, context, generated package, "
            "and served OpenAPI document may change."
        ),
        since_version="0.83.1",
        note=(
            "generate_fastapi and render_fastapi generate the models and a FastAPI server package from one OpenAPI "
            "document with the api scope. The package declares a service Protocol for each router group, builds "
            "routers from the services you implement in your own modules, and serves the source document's metadata "
            "through install_openapi."
        ),
    ),
}


def iter_experimental_features() -> tuple[ExperimentalFeature, ...]:
    """Return all experimental features in stable display order."""
    return tuple(sorted(EXPERIMENTAL_FEATURES.values(), key=lambda item: (item.kind, item.target)))


def experimental_feature_as_dict(feature: ExperimentalFeature) -> dict[str, str | None]:
    """Serialize an experimental feature entry to primitive values."""
    return asdict(feature)


def render_experimental_features_json() -> str:
    """Render all experimental features as JSON."""
    return _render_registry_json(experimental_feature_as_dict(feature) for feature in iter_experimental_features())


def render_experimental_features_table() -> str:
    """Render all experimental features as a plain text table with readable notes."""
    features = iter_experimental_features()
    table = _render_registry_table([
        [
            "ID",
            "Kind",
            "Target",
            "Since",
            "Tracking",
        ],
        *[
            [
                feature.id,
                feature.kind,
                feature.target,
                feature.since_version,
                feature.tracking_issue or "-",
            ]
            for feature in features
        ],
    ])
    notes = [
        f"{feature.id}:\n{fill(note, width=100, initial_indent='  ', subsequent_indent='  ', break_on_hyphens=False)}"
        for feature in features
        if (note := feature.note) is not None
    ]
    return "\n".join([
        *(line.rstrip() for line in table.splitlines()),
        "",
        "Notes:",
        *notes,
        "",
    ])


def render_experimental_features_markdown(*, include_header: bool = True) -> str:
    """Render all experimental features as Markdown."""
    lines: list[str] = []
    if include_header:
        lines.extend([
            "# Experimental Features",
            "",
            "<!-- Generated by scripts/build_experimental_docs.py. Do not edit manually. -->",
            "",
            "This page lists features that are available but still experimental.",
            "",
        ])
    lines.extend([
        "| ID | Kind | Target | Since | Tracking |",
        "|----|------|--------|-------|----------|",
    ])
    for feature in iter_experimental_features():
        tracking = feature.tracking_issue or "-"
        lines.append(f"| `{feature.id}` | {feature.kind} | `{feature.target}` | {feature.since_version} | {tracking} |")
    lines.extend(("", "## Details", ""))
    for feature in iter_experimental_features():
        lines.extend([
            f"### `{feature.id}`",
            "",
            f"- **Kind:** {feature.kind}",
            f"- **Target:** `{feature.target}`",
            f"- **Since:** {feature.since_version}",
        ])
        if feature.tracking_issue:
            lines.append(f"- **Tracking:** {feature.tracking_issue}")
        lines.extend([
            "",
            feature.message,
            "",
        ])
        if feature.note:
            lines.extend([feature.note, ""])
    return "\n".join(lines).rstrip() + "\n"


def render_experimental_features(format_: ExperimentalFeatureFormat) -> str:
    """Render experimental features in the requested format."""
    if format_ == "json":
        return render_experimental_features_json() + "\n"
    if format_ == "markdown":
        return render_experimental_features_markdown()
    return render_experimental_features_table()


def render_release_note_experimental_features(version: str) -> str:
    """Render release-note text for experimental features introduced in a version."""
    introduced = [item for item in iter_experimental_features() if item.since_version == version]

    lines: list[str] = []
    if introduced:
        lines.extend(["## Experimental Features", ""])
        lines.extend(f"- `{item.target}` is experimental. {item.message}" for item in introduced)
        lines.append("")

    return "\n".join(lines)
