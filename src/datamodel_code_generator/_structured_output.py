"""Structured JSON output payloads for the CLI."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic.json_schema import SkipJsonSchema

from datamodel_code_generator.prompt import PromptPayload

if TYPE_CHECKING:
    from pydantic.json_schema import JsonDict

JSON_SCHEMA_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"


class GeneratedFilePayload(BaseModel):
    """One generated file emitted by --output-format json."""

    model_config = ConfigDict(extra="forbid")

    path: str | None
    content: str


_Absent: TypeAlias = SkipJsonSchema[None]
_NEXT_STEPS = ("implied", "entrypoint", "implementation", "usage")
_TARGET_DESCRIPTION = "The package and runtime dependencies of a server or client generation; absent for models only."


def _absent_when_unset(schema: JsonDict) -> None:
    """Leave the default out of an optional key's schema: the payload omits the key rather than printing null."""
    schema.pop("default")


def _implied(key: str, description: str) -> Any:
    return Field(default=None, alias=key, description=description, json_schema_extra=_absent_when_unset)


class ImpliedPayload(BaseModel):
    """The model settings a server or client target set because the run left them unset, keyed as pyproject.toml keys.

    Each key is absent unless the target set it.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    openapi_scopes: list[str] | _Absent = _implied("openapi-scopes", "The scopes, with api added to the default ones.")
    target_python_version: str | _Absent = _implied("target-python-version", "The target Python version, 3.11.")
    use_missing_sentinel: bool | _Absent = _implied(
        "use-missing-sentinel", "The MISSING sentinel, on for pydantic_v2.dataclass models."
    )
    target_pydantic_version: str | _Absent = _implied(
        "target-pydantic-version", "The target pydantic version, 2.14, for models with the MISSING sentinel."
    )
    use_msgspec_tagged_unions: bool | _Absent = _implied(
        "use-msgspec-tagged-unions", "Tagged unions, on for msgspec.Struct models."
    )
    use_msgspec_nullable_enum_alias: bool | _Absent = _implied(
        "use-msgspec-nullable-enum-alias", "Nullable enum aliases, on for msgspec.Struct models."
    )


class TargetPayload(BaseModel):
    """The package a server or client generation renders and the requirements it needs at run time."""

    model_config = ConfigDict(extra="forbid")

    package: str
    dependencies: list[str]
    implied: ImpliedPayload | _Absent = Field(
        default=None,
        description=(
            "The settings the target needs that the run left unset and the values it gave them, keyed as "
            "pyproject.toml keys them; absent when it implied none."
        ),
        json_schema_extra=_absent_when_unset,
    )


class EntrypointPayload(BaseModel):
    """The entrypoint module of a generated server and the import string of the ASGI application it defines."""

    model_config = ConfigDict(extra="forbid")

    app: str
    path: str


class ImplementationPayload(BaseModel):
    """A module of the user's that a generated server's entrypoint imports, and the class or function it imports.

    Generation writes the module with stubs only while it is missing; `created` says whether this run wrote it.
    """

    model_config = ConfigDict(extra="forbid")

    module: str
    name: str
    path: str
    created: bool


class GeneratedTargetPayload(TargetPayload):
    """The package a server or client generation writes, its runtime requirements, and what to do with it next."""

    readme: str = Field(description="The README of the package, which describes the next steps.")
    entrypoint: EntrypointPayload | _Absent = Field(
        default=None,
        description="The module that serves a generated server's application; absent for a client.",
        json_schema_extra=_absent_when_unset,
    )
    implementation: list[ImplementationPayload] | _Absent = Field(
        default=None,
        description="The modules of the user's that a generated server's entrypoint imports; absent for a client.",
        json_schema_extra=_absent_when_unset,
    )
    usage: str | _Absent = Field(
        default=None,
        description="Code that calls the first operation of a generated client; absent for a server.",
        json_schema_extra=_absent_when_unset,
    )


class GenerationPayload(BaseModel):
    """Structured JSON payload emitted by generation mode --output-format json."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    format: Literal["json"]
    kind: Literal["generation"]
    output: str | None
    files: list[GeneratedFilePayload]
    target: GeneratedTargetPayload | _Absent = Field(
        default=None, description=_TARGET_DESCRIPTION, json_schema_extra=_absent_when_unset
    )


CommandOutputKind: TypeAlias = Literal[
    "pyproject-config",
    "cli-command",
    "deprecations",
    "experimental",
]


class PyprojectConfigOutputPayload(BaseModel):
    """Structured JSON payload emitted by --generate-pyproject-config."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    format: Literal["json"]
    kind: Literal["pyproject-config"]
    content: str
    config: dict[str, Any]


class CliCommandOutputPayload(BaseModel):
    """Structured JSON payload emitted by --generate-cli-command."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    format: Literal["json"]
    kind: Literal["cli-command"]
    content: str
    config: dict[str, Any]
    arguments: list[str]


class DeprecationItemPayload(BaseModel):
    """One deprecation entry emitted by --list-deprecations --output-format json."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    target: str
    message: str
    warning_since: str
    removal_version: str | None
    replacement: str | None
    status: str
    warning_category: str
    note: str | None


class DeprecationsOutputPayload(BaseModel):
    """Structured JSON payload emitted by --list-deprecations."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    format: Literal["json"]
    kind: Literal["deprecations"]
    content: str
    items: list[DeprecationItemPayload]


class ExperimentalItemPayload(BaseModel):
    """One experimental feature entry emitted by --list-experimental --output-format json."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    target: str
    message: str
    since_version: str
    tracking_issue: str | None
    note: str | None


class ExperimentalOutputPayload(BaseModel):
    """Structured JSON payload emitted by --list-experimental."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    format: Literal["json"]
    kind: Literal["experimental"]
    content: str
    items: list[ExperimentalItemPayload]


class CheckDifferencePayload(BaseModel):
    """One generated-output difference emitted by --check or --diff-against."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["changed", "missing", "extra", "added", "removed"]
    path: str
    message: str | None = None
    diff: str | None = None


class CheckOutputPayload(BaseModel):
    """Structured JSON payload emitted by generated-output comparison commands."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    format: Literal["json"]
    kind: Literal["check", "input-diff"]
    success: bool
    content: str
    differences: list[CheckDifferencePayload]
    target: TargetPayload | _Absent = Field(
        default=None, description=_TARGET_DESCRIPTION, json_schema_extra=_absent_when_unset
    )


class BatchJobPayload(BaseModel):
    """One named job result emitted by batch generation."""

    model_config = ConfigDict(extra="forbid")

    name: str
    result: GenerationPayload | CheckOutputPayload


class BatchOutputPayload(BaseModel):
    """Structured JSON payload emitted by --job/--all-jobs with --output-format json."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    format: Literal["json"]
    kind: Literal["batch"]
    jobs: list[BatchJobPayload]


RecommendOptionValue: TypeAlias = bool | str | list[str]


class RecommendInputPayload(BaseModel):
    """The input a recommendation read and whether its schema rules could analyse it."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["file", "url", "stdin", "directory", "input-model"]
    input_file_type: str | None = Field(description="The configured or detected input file type.")
    document: str | None = Field(
        description="The schema format and version, such as OpenAPI 3.1.0; null when not analysed."
    )
    analysed: bool


class RecommendContextPayload(BaseModel):
    """The output model type and target Python version the recommendation chose presets for."""

    model_config = ConfigDict(extra="forbid")

    output_model_type: str
    target_python_version: str


class RecommendNameChangePayload(BaseModel):
    """One class that two candidates name differently at the same schema location."""

    model_config = ConfigDict(extra="forbid")

    location: str = Field(
        description=(
            "Where the class comes from, for people: the document path, then each inline step after '>'. It is not a "
            "JSON Pointer and cannot be used as a --model-name-map key."
        )
    )
    before: str
    after: str


class RecommendNameChangesPayload(BaseModel):
    """How the class names of two candidates differ, matching classes by schema location."""

    model_config = ConfigDict(extra="forbid")

    matched: int = Field(description="Locations with one class in both candidates.")
    renamed: int
    renumbered: int = Field(description="Renamed classes whose names differ only in digits.")
    removed: int = Field(description="Locations with a class only before.")
    added: int = Field(description="Locations with a class only after.")
    examples: list[RecommendNameChangePayload] = Field(
        description="Renamed classes from the largest families of numbered names before, then by location."
    )


class RecommendNameMetricsPayload(BaseModel):
    """Counts over the class names one candidate generates; problems counts each class once."""

    model_config = ConfigDict(extra="forbid")

    types: int = Field(description="Generated classes.")
    component: int
    inline: int
    operation: int
    readable: int
    numbered: int
    statuscode: int
    generic: int
    long: int = Field(description="Names longer than 40 characters that are not a component key.")
    doubled: int = Field(description="Names repeating a word the schema does not repeat.")
    doubled_from_spec: int
    other_problems: int = Field(description="Problem names that are neither numbered nor doubled.")
    displaced: int = Field(description="Components whose name another class took.")
    inline_like_component: int
    union_variants: int
    union_variants_parent_numbered: int
    union_variants_numbered: int
    median_length: int
    max_length: int
    problems: int
    problems_percent: float


class RecommendNameStabilityPayload(BaseModel):
    """How one candidate's class names move between --diff-against and the input."""

    model_config = ConfigDict(extra="forbid")

    fingerprints_matched: int = Field(description="Classes matched by a unique set of at least two field names.")
    fingerprints_renamed: int
    fingerprints_renumbered: int
    locations: RecommendNameChangesPayload


class RecommendCandidatePayload(BaseModel):
    """One compared configuration and the class names it generates, or the error that rejected it."""

    model_config = ConfigDict(extra="forbid")

    label: str
    preset: str | None = Field(description="The preset the candidate starts from; null for the configuration as set.")
    set: dict[str, RecommendOptionValue] = Field(description="Options added on top of the preset, as config values.")
    arguments: list[str]
    selectable: bool = Field(description="False for the configuration as set without the recommended preset.")
    error: str | None
    dropped_constraint_warnings: int | None = Field(
        description=(
            "Fields that dcg warns the candidate generates without some of their constraints; ranked after the class "
            "names. Null when the candidate was rejected."
        )
    )
    metrics: RecommendNameMetricsPayload | None
    stability: RecommendNameStabilityPayload | None


class RecommendTrialPayload(BaseModel):
    """The candidates full mode generated and compared, and the one it selected."""

    model_config = ConfigDict(extra="forbid")

    metrics_version: int
    candidates: list[RecommendCandidatePayload]
    ranking: list[str] = Field(description="Candidate labels, best first.")
    selected: str | None = Field(description="The best selectable candidate; null when every one was rejected.")
    current: str = Field(description="The candidate that runs the configuration as set.")
    migration: RecommendNameChangesPayload | None = Field(
        description="Class names the selected candidate changes from the current one."
    )
    dropped: int = Field(description="Candidates beyond the limit of eight that were not generated.")
    remeasured: int = Field(
        description=(
            "The last candidates: the selected configuration without or with one option, generated to measure each "
            "option on it when it is not the rules' recommendation; never selected."
        )
    )


class RecommendOptionTrialPayload(BaseModel):
    """The candidates without and with one option, and the class names it changes."""

    model_config = ConfigDict(extra="forbid")

    before: str = Field(description="Label of the candidate without the option.")
    after: str = Field(description="Label of the candidate with the option.")
    changes: RecommendNameChangesPayload


class RecommendOptionPayload(BaseModel):
    """One rule outcome: the config values it sets, the CLI arguments for them, and why."""

    model_config = ConfigDict(extra="forbid")

    rule: str
    decision: str
    set: dict[str, RecommendOptionValue] = Field(description="Config fields and values, as pyproject.toml spells them.")
    arguments: list[str] = Field(
        description="The CLI arguments of the values; for not_recommended, the options the rule advises against."
    )
    reason: str
    contested_reason: str | None = Field(
        description="Why the schema also argues against the values; set only for consider entries."
    )
    caveats: list[str]
    evidence: list[str]
    trial: RecommendOptionTrialPayload | None = Field(
        description=(
            "What full mode measured for the option; null in quick mode, or when a candidate it needs was rejected "
            "or left out."
        )
    )


class RecommendBaselinePayload(BaseModel):
    """The dated preset the recommendation starts from."""

    model_config = ConfigDict(extra="forbid")

    preset: str
    family: str
    configured: bool = Field(description="Whether the configuration already selects this preset.")
    covers: list[RecommendOptionPayload] = Field(description="Rule outcomes the preset already sets.")


class RecommendDisagreementPayload(BaseModel):
    """A configured value the recommendation keeps although the rules suggest another value."""

    model_config = ConfigDict(extra="forbid")

    field: str
    kind: Literal["explicit", "preset", "newer-preset"] = Field(
        description=(
            "explicit: the configuration sets the field; preset: the configured preset sets it and no option "
            "undoes it; newer-preset: a newer dated preset of the same family exists."
        )
    )
    configured: Any
    recommended: Any
    rule: str | None


class RecommendOptionsPayload(BaseModel):
    """Structured JSON payload emitted by --recommend-options --output-format json (experimental)."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    format: Literal["json"]
    kind: Literal["recommend-options"]
    mode: Literal["quick", "full"]
    rules_revision: str
    calibrated_presets: str
    input: RecommendInputPayload
    context: RecommendContextPayload
    signals: dict[str, int]
    facts: dict[str, str]
    baseline: RecommendBaselinePayload
    recommendations: list[RecommendOptionPayload]
    left_out: list[RecommendOptionPayload] = Field(
        description="Options the rules recommend that the candidate full mode selected does not take."
    )
    consider: list[RecommendOptionPayload]
    validation: list[RecommendOptionPayload] = Field(
        description=(
            "Options for checks the schema declares that the models leave out by default; the user decides, and they "
            "are never in arguments or config."
        )
    )
    policy: list[RecommendOptionPayload] = Field(
        description=(
            "Options that depend on what input the project accepts rather than on the schema, such as strict types; "
            "never in arguments or config."
        )
    )
    not_recommended: list[RecommendOptionPayload]
    disagreements: list[RecommendDisagreementPayload]
    trial: RecommendTrialPayload | None = Field(description="The candidates full mode compared; null in quick mode.")
    notes: list[str]
    arguments: list[str] = Field(
        description=(
            "The recommended command's arguments, without the program name; empty when there is no command to run, "
            "such as for a Swagger 2.0 document typed as OpenAPI."
        )
    )
    config: dict[str, Any] | None = Field(
        description=(
            "The pyproject.toml keys to write, nested under their table; null when they stay on the command line or "
            "there is no command to run."
        )
    )
    config_complete: bool = Field(
        description="Whether config is a complete table, or only the keys to add or change in an existing one."
    )
    content: str


CommandOutputPayload: TypeAlias = (
    PyprojectConfigOutputPayload | CliCommandOutputPayload | DeprecationsOutputPayload | ExperimentalOutputPayload
)
StructuredOutputPayload: TypeAlias = Annotated[
    GenerationPayload
    | PyprojectConfigOutputPayload
    | CliCommandOutputPayload
    | DeprecationsOutputPayload
    | ExperimentalOutputPayload
    | CheckOutputPayload
    | BatchOutputPayload
    | PromptPayload
    | RecommendOptionsPayload,
    Field(discriminator="kind"),
]

_DEPRECATION_ITEMS_ADAPTER = TypeAdapter(list[DeprecationItemPayload])
_EXPERIMENTAL_ITEMS_ADAPTER = TypeAdapter(list[ExperimentalItemPayload])
_STRUCTURED_OUTPUT_ADAPTER = TypeAdapter(StructuredOutputPayload)


def _dump_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def _dump_payload(payload: GenerationPayload | CheckOutputPayload) -> str:
    """Dump a payload, leaving out the target of a model-only run and the keys a server's or client's target lacks."""
    return _dump_json(payload.model_dump(mode="json", by_alias=True, exclude=_absent(payload)))


def _absent(payload: GenerationPayload | CheckOutputPayload) -> dict[str, Any] | set[str]:
    """Return the keys a payload leaves out: its target for a model-only run, or the keys its target lacks."""
    if (target := payload.target) is None:
        return {"target"}
    absent: dict[str, Any] = {name: True for name in _NEXT_STEPS if getattr(target, name, None) is None}
    if (implied := target.implied) is not None:
        absent["implied"] = {name for name in ImpliedPayload.model_fields if getattr(implied, name) is None}
    return {"target": absent}


def batch_job_json(job: BatchJobPayload) -> dict[str, Any]:
    """Return a job's entry of the batch payload, leaving out what its payload leaves out."""
    return job.model_dump(mode="json", by_alias=True, exclude={"result": _absent(job.result)})


def _schema_with_metadata(schema: dict[str, Any], *, title: str, description: str) -> dict[str, Any]:
    schema = dict(schema)
    schema.setdefault("title", title)
    schema.setdefault("description", description)
    return {"$schema": JSON_SCHEMA_DRAFT_2020_12, **schema}


def generation_output_json(
    files: list[GeneratedFilePayload], *, output: str | None, target: GeneratedTargetPayload | None = None
) -> str:
    return _dump_payload(
        GenerationPayload(version=1, format="json", kind="generation", output=output, files=files, target=target)
    )


def command_output_json(
    kind: CommandOutputKind,
    content: str,
    *,
    config: dict[str, Any] | None = None,
    items: list[dict[str, Any]] | None = None,
    arguments: list[str] | None = None,
) -> str:
    payload: CommandOutputPayload
    if kind == "pyproject-config":
        payload = PyprojectConfigOutputPayload(
            version=1,
            format="json",
            kind=kind,
            content=content,
            config=config or {},
        )
    elif kind == "cli-command":
        payload = CliCommandOutputPayload(
            version=1,
            format="json",
            kind=kind,
            content=content,
            config=config or {},
            arguments=arguments or [],
        )
    elif kind == "deprecations":
        payload = DeprecationsOutputPayload(
            version=1,
            format="json",
            kind=kind,
            content=content,
            items=_DEPRECATION_ITEMS_ADAPTER.validate_python(items or []),
        )
    else:
        payload = ExperimentalOutputPayload(
            version=1,
            format="json",
            kind=kind,
            content=content,
            items=_EXPERIMENTAL_ITEMS_ADAPTER.validate_python(items or []),
        )
    return _dump_json(payload.model_dump(mode="json"))


def check_output_json(
    *,
    success: bool,
    content: str,
    differences: list[CheckDifferencePayload],
    kind: Literal["check", "input-diff"] = "check",
    target: TargetPayload | None = None,
) -> str:
    return _dump_payload(
        CheckOutputPayload(
            version=1,
            format="json",
            kind=kind,
            success=success,
            content=content,
            differences=differences,
            target=target,
        )
    )


def generation_output_json_schema() -> str:
    return _dump_json(
        _schema_with_metadata(
            GenerationPayload.model_json_schema(mode="serialization"),
            title="GenerationPayload",
            description="Structured JSON payload emitted by generation mode --output-format json.",
        )
    )


def structured_output_json_schema() -> str:
    return _dump_json(
        _schema_with_metadata(
            _STRUCTURED_OUTPUT_ADAPTER.json_schema(mode="serialization"),
            title="StructuredOutputPayload",
            description="Schema for all structured JSON outputs emitted by the CLI.",
        )
    )


def recommend_options_json_schema() -> str:
    return _dump_json(
        _schema_with_metadata(
            RecommendOptionsPayload.model_json_schema(mode="serialization"),
            title="RecommendOptionsPayload",
            description="Structured JSON payload emitted by --recommend-options --output-format json.",
        )
    )
