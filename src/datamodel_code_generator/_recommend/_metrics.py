"""Class-name metrics for comparing generator configurations on one schema.

The definitions are those of the tuned naming benchmark (multilang research, ``bench-data/tuned/lib.py``), restricted
to datamodel-code-generator's own model metadata, so a trial reports the benchmark's problem names. A name is a problem
when it is numbered, a status code, generic, longer than 40 characters, repeats a word the schema does not repeat,
takes the place of a component's name, or names an inline model like an unrelated component. Unlike the benchmark,
length does not hide the other tests: a numbered name over 40 characters is both numbered and long.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from datamodel_code_generator.model_metadata import ModelMetadata

__all__ = [
    "LONG_NAME_LENGTH",
    "METRICS_VERSION",
    "NameChange",
    "NameChanges",
    "NameMetrics",
    "NameRecord",
    "NameStability",
    "SchemaVocabulary",
    "location_changes",
    "name_metrics",
    "name_records",
    "name_stability",
    "readable_location",
    "schema_vocabulary",
]

METRICS_VERSION: Final = 2
LONG_NAME_LENGTH: Final = 40
_OPERATION_PREFIXES: Final = (
    "paths/",
    "components/parameters",
    "components/responses",
    "components/requestBodies",
    "webhooks",
)
_GENERIC_WORDS: Final = """Type Value Values Item Items Data Object Model Root Inline InlineObject InlineResponse AnyOf
OneOf AllOf Field Property Properties Schema Element Entry Payload Case Variant Additional AdditionalProperty
AdditionalProperties Body Content Inner Json Default Other Kind Result Info Details Config Options Settings Params
Parameters Input Output Response Request Status State Enum Key Member Members Union Choice Alternative Option Record
Struct Elem Obj Anonymous Unnamed Untitled"""
_NON_ALNUM: Final = re.compile(r"[^a-z0-9]")
_NUMBER_WORD: Final = re.compile(r"[a-z0-9](One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten)$")
_GENERIC_NUMBER: Final = re.compile(
    r"(OneOf|AnyOf|AllOf|Inner|Case|Value|Item|Object|Model|Type|Variant|Option|Member|Alternative)\d+", re.IGNORECASE
)
_STATUS_CODE: Final = re.compile(r"(?<![0-9])[1-5][0-9][0-9](?![0-9])")
_SPECIAL: Final = re.compile(r"#-datamodel-code-generator-#-(\w+?)-#-special-#")
_UNNAMED_KINDS: Final = frozenset({"object", "enum"})
_BRANCH_KEYWORDS: Final = frozenset({"oneOf", "anyOf", "allOf"})
_ITEM_KEYWORDS: Final = frozenset({"items", "prefixItems", "additionalItems", "unevaluatedItems"})
_NAMED_KEYWORDS: Final = frozenset({"properties", "patternProperties", "dependentSchemas", "definitions", "$defs"})
_UNION_VARIANT: Final = re.compile(r"(?:^|/)(oneOf|anyOf)/(\d+)(/allOf)?$")
_ROOT_REF: Final = re.compile(r"^(?:.*?\.(?:json|ya?ml))?#?/?")
_TRAILING_DIGITS: Final = re.compile(r"\d+$")
_DIGITS: Final = re.compile(r"\d+")
_DISPLACED_SUFFIX: Final = re.compile(r"(\d+|Model)$")
_WORD: Final = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+")
_DOUBLED_SEGMENT_LENGTH: Final = 4
_COMPONENT_DEPTH: Final = 3
_DEFINITION_DEPTH: Final = 2


def _norm(text: object) -> str:
    return _NON_ALNUM.sub("", str(text).lower())


_GENERIC: Final = frozenset(map(_norm, _GENERIC_WORDS.split()))


@dataclass(frozen=True, slots=True)
class SchemaVocabulary:
    """Normalized words of the input schemas that a class name may take as they are.

    ``words`` holds every key, title, operationId and string enum value, ``components`` the keys of every components
    section (or the definitions of a JSON Schema) and ``top_level`` the keys of its schemas.
    """

    words: frozenset[str]
    operation_ids: frozenset[str]
    components: frozenset[str]
    top_level: frozenset[str]
    joined_words: str

    def contains_doubled(self, segment: str) -> bool:
        """Return whether a schema word contains the segment twice in a row."""
        return _norm(segment) * 2 in self.joined_words


def _collect_words(document: object, words: set[str], operation_ids: set[str]) -> None:
    stack = [document]
    while stack:
        match node := stack.pop():
            case dict():
                for key, value in node.items():
                    words.add(_norm(key))
                    if key in {"title", "operationId"} and isinstance(value, str):
                        words.add(_norm(value))
                        if key == "operationId":
                            operation_ids.add(_norm(value))
                    if key == "enum" and isinstance(value, list):
                        words.update(_norm(item) for item in value if isinstance(item, str))
                    stack.append(value)
            case list():
                stack.extend(node)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _component_names(document: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    """Return the keys of every components section and of its schemas; the definitions of a JSON Schema are both.

    A section that is not a mapping has no keys, so a document generation rejects still has a vocabulary.
    """
    if "components" not in document:
        names = set(map(_norm, _mapping(document.get("definitions", document.get("$defs")))))
        return names, names
    sections = _mapping(document["components"])
    components = {_norm(key) for items in sections.values() for key in _mapping(items)}
    return components, set(map(_norm, _mapping(sections.get("schemas"))))


def schema_vocabulary(document: Mapping[str, Any] | None) -> SchemaVocabulary:
    """Collect the vocabulary of the loaded input document; without one, class names are measured on their own."""
    words: set[str] = set()
    operation_ids: set[str] = set()
    components: set[str] = set()
    top_level: set[str] = set()
    if document is not None:
        _collect_words(document, words, operation_ids)
        components, top_level = _component_names(document)
    return SchemaVocabulary(
        words=frozenset(words),
        operation_ids=frozenset(operation_ids),
        components=frozenset(components),
        top_level=frozenset(top_level),
        joined_words="\n".join(sorted(words)),
    )


@dataclass(frozen=True, slots=True)
class NameRecord:
    """One generated class and the schema location it came from.

    ``path`` is the benchmark's location key, which matches classes across generations; it is taken from the model's
    internal identity path, never written. ``location`` describes the JSON Pointer of the class's schema for people
    (see ``readable_location``).
    """

    name: str
    path: str
    location: str
    members: tuple[str, ...]


def readable_location(tokens: Sequence[str]) -> str:
    """Describe a class's schema location: the named schema or operation, then each inline step after ``>``.

    The tokens are those of the JSON Pointer that ``--emit-model-metadata`` writes as ``source_path``. The named schema
    is ``components/<section>/<key>``, ``definitions/<key>`` or ``$defs/<key>``, an operation ``paths/<path>/<method>``
    or ``webhooks/<name>/<method>``; anything else hangs off the ``(root)`` schema. A property reads as its name, a
    union branch as ``oneOf 0`` and an array item as ``item``.
    """
    split = 0
    match tokens[:1]:
        case ["components" | "paths" | "webhooks"]:
            split = _COMPONENT_DEPTH
        case ["definitions" | "$defs"]:
            split = _DEFINITION_DEPTH
    split = min(split, len(tokens))
    inline: list[str] = []
    index = split
    while index < len(tokens):
        token, following = tokens[index], tokens[index + 1] if index + 1 < len(tokens) else None
        step = 2
        if following is not None and token in _NAMED_KEYWORDS:
            inline.append(following)
        elif following is not None and following.isdigit() and token in _BRANCH_KEYWORDS:
            inline.append(f"{token} {following}")
        elif token in _ITEM_KEYWORDS:
            inline.append("item")
            step = 2 if following is not None and following.isdigit() else 1
        else:
            inline.append(token)
            step = 1
        index += step
    named = "/".join(
        token.removeprefix("/") if position == 1 else token for position, token in enumerate(tokens[:split])
    )
    return " > ".join((named or "(root)", *inline))


def _identity_steps(identity: str) -> list[str]:
    """Return the steps of a model's identity path: its decoded pointer, or its parts after the document."""
    _, separator, fragment = identity.partition("#")
    if separator and fragment.startswith("/"):
        return [part.replace("~1", "/").replace("~0", "~") for part in fragment[1:].split("/")]
    return [part for part in _ROOT_REF.sub("", identity).split("/") if part]


def name_records(metadata: ModelMetadata | None, identities: Sequence[str]) -> tuple[NameRecord, ...]:
    """Return the classes of a model metadata payload in generation order, matched by their identity paths."""
    records: list[NameRecord] = []
    for model, identity in zip(metadata["models"] if metadata is not None else (), identities, strict=True):
        steps: list[str] = []
        for part in _identity_steps(identity):
            special = _SPECIAL.search(part)
            kind = () if special is None or special.group(1) in _UNNAMED_KINDS else (special.group(1),)
            steps.extend(step for step in (_SPECIAL.sub("", part), *kind) if step)
        records.append(
            NameRecord(
                name=model["class_name"],
                path="/".join(steps),
                location=readable_location(model["source_path"] or ()),
                members=tuple(field["original_name"] or field["name"] for field in model["fields"]),
            )
        )
    return tuple(records)


def _classify(name: str, vocabulary: SchemaVocabulary, all_names: frozenset[str]) -> frozenset[str]:
    """Return the problem classes of a name: long, and at most one of numbered, statuscode and generic."""
    normalized = _norm(name)
    if normalized in vocabulary.components:
        return frozenset()
    kinds = {"long"} if len(name) > LONG_NAME_LENGTH else set()
    if normalized not in vocabulary.words and (kind := _number_class(name, normalized, vocabulary, all_names)):
        kinds.add(kind)
    return frozenset(kinds)


def _number_class(name: str, normalized: str, vocabulary: SchemaVocabulary, all_names: frozenset[str]) -> str | None:
    if _GENERIC_NUMBER.search(name) or _NUMBER_WORD.search(name):
        return "numbered"
    if _STATUS_CODE.search(name):
        return "statuscode"
    stem = _TRAILING_DIGITS.sub("", normalized)
    if _TRAILING_DIGITS.search(name) and (
        stem in vocabulary.words or stem in vocabulary.components or stem in all_names or stem in _GENERIC
    ):
        return "numbered"
    return "generic" if stem in _GENERIC else None


def _is_component_location(parts: list[str]) -> bool:
    return (parts[:2] == ["components", "schemas"] and len(parts) == _COMPONENT_DEPTH) or (
        parts[0] in {"definitions", "$defs"} and len(parts) == _DEFINITION_DEPTH
    )


def _origin(path: str) -> str:
    if path.startswith(_OPERATION_PREFIXES):
        return "operation"
    return "component" if not path or _is_component_location(path.split("/")) else "inline"


def _doubled_segment(name: str) -> str | None:
    words = _WORD.findall(name)
    lowered = [word.lower() for word in words]
    for size in range(1, len(words) // 2 + 1):
        for start in range(len(words) - 2 * size + 1):
            if (
                lowered[start : start + size] == lowered[start + size : start + 2 * size]
                and len(segment := "".join(words[start : start + size])) >= _DOUBLED_SEGMENT_LENGTH
            ):
                return segment
    return None


@dataclass(frozen=True, slots=True)
class NameMetrics:
    """Counts over the generated class names of one configuration.

    ``problems`` counts each class once, and ``other_problems`` the problem names that are neither numbered nor doubled.
    A name over 40 characters counts in ``long`` and also in ``numbered``, ``statuscode`` or ``generic`` when it is one.
    """

    types: int
    component: int
    inline: int
    operation: int
    readable: int
    numbered: int
    statuscode: int
    generic: int
    long: int
    doubled: int
    doubled_from_spec: int
    other_problems: int
    displaced: int
    inline_like_component: int
    union_variants: int
    union_variants_parent_numbered: int
    union_variants_numbered: int
    median_length: int
    max_length: int
    problems: int

    @property
    def problems_percent(self) -> float:
        """Return the problem names as a percentage of the classes, rounded to one decimal."""
        return round(100 * self.problems / self.types, 1) if self.types else 0.0


def name_metrics(records: Sequence[NameRecord], vocabulary: SchemaVocabulary) -> NameMetrics:
    """Measure the class names of one configuration against the vocabulary of its input."""
    all_names = frozenset(_norm(record.name) for record in records)
    classes = [_classify(record.name, vocabulary, all_names) for record in records]
    counts = Counter(kind for kinds in classes for kind in kinds)
    origins = Counter(_origin(record.path) for record in records)
    problems = {index for index, kinds in enumerate(classes) if kinds}
    numbered = {index for index, kinds in enumerate(classes) if "numbered" in kinds}
    doubled: list[int] = []
    doubled_from_spec = 0
    for index, record in enumerate(records):
        if (segment := _doubled_segment(record.name)) is None:
            continue
        if vocabulary.contains_doubled(segment):
            doubled_from_spec += 1
        else:
            doubled.append(index)
            problems.add(index)
    component_names: dict[str, str] = {}
    for record in records:
        if _is_component_location(parts := record.path.split("/")):
            component_names[_norm(parts[-1])] = record.name
    inline_like = [
        index
        for index, record in enumerate(records)
        if record.path.count("/") > (3 if record.path.startswith("components") else 1)
        and (stem := _norm(_TRAILING_DIGITS.sub("", record.name))) in vocabulary.top_level
        and component_names.get(stem) != record.name
    ]
    displaced = [
        name
        for key, name in component_names.items()
        if _norm(name) != key and (stem := _DISPLACED_SUFFIX.sub("", name)) and _norm(stem) == key
    ]
    displaced_names = set(displaced)
    problems.update(inline_like)
    problems.update(index for index, record in enumerate(records) if record.name in displaced_names)
    variants = [index for index, record in enumerate(records) if _UNION_VARIANT.search(record.path)]
    parent_numbered = sum(
        1
        for index in variants
        if _TRAILING_DIGITS.search(name := records[index].name)
        and _norm(_TRAILING_DIGITS.sub("", name))
        in _norm(records[index].path.split("/oneOf/")[0].split("/anyOf/")[0].split("/")[-1])
    )
    lengths = sorted(len(record.name) for record in records)
    return NameMetrics(
        types=len(records),
        component=origins["component"],
        inline=origins["inline"],
        operation=origins["operation"],
        readable=sum(1 for kinds in classes if not kinds),
        numbered=counts["numbered"],
        statuscode=counts["statuscode"],
        generic=counts["generic"],
        long=counts["long"],
        doubled=len(doubled),
        doubled_from_spec=doubled_from_spec,
        other_problems=len(problems - {*doubled, *numbered}),
        displaced=len(displaced),
        inline_like_component=len(inline_like),
        union_variants=len(variants),
        union_variants_parent_numbered=parent_numbered,
        union_variants_numbered=sum(1 for index in variants if index in numbered),
        median_length=lengths[len(lengths) // 2] if lengths else 0,
        max_length=lengths[-1] if lengths else 0,
        problems=len(problems),
    )


@dataclass(frozen=True, slots=True)
class NameChange:
    """One class renamed at a schema location."""

    location: str
    before: str
    after: str


@dataclass(frozen=True, slots=True)
class NameChanges:
    """How class names differ between two generations, matched by the identity paths of their models.

    Only locations with one class on both sides are matched; ``renumbered`` counts renames that change digits only.
    The examples come from the largest families of numbered names before, then by location.
    """

    matched: int
    renamed: int
    renumbered: int
    removed: int
    added: int
    examples: tuple[NameChange, ...]


def location_changes(before: Sequence[NameRecord], after: Sequence[NameRecord], *, examples: int = 3) -> NameChanges:
    """Compare the class names two generations give each schema location."""
    before_records: defaultdict[str, list[NameRecord]] = defaultdict(list)
    after_records: defaultdict[str, list[NameRecord]] = defaultdict(list)
    for record in before:
        before_records[record.path].append(record)
    for record in after:
        after_records[record.path].append(record)
    matched = [
        path for path, records in before_records.items() if len(records) == 1 and len(after_records.get(path, ())) == 1
    ]
    renamed = [
        (before_records[path][0], after_records[path][0])
        for path in matched
        if before_records[path][0].name != after_records[path][0].name
    ]
    families = Counter(_TRAILING_DIGITS.sub("", record.name) for record in before)
    chosen = sorted(renamed, key=lambda pair: (-families[_TRAILING_DIGITS.sub("", pair[0].name)], pair[0].location))
    return NameChanges(
        matched=len(matched),
        renamed=len(renamed),
        renumbered=sum(1 for old, new in renamed if _DIGITS.sub("", old.name) == _DIGITS.sub("", new.name)),
        removed=len(before_records.keys() - after_records.keys()),
        added=len(after_records.keys() - before_records.keys()),
        examples=tuple(NameChange(old.location, old.name, new.name) for old, new in chosen[:examples]),
    )


@dataclass(frozen=True, slots=True)
class NameStability:
    """How the class names of one configuration move between two versions of the input.

    Classes are matched by the identity paths of their models and, independently, by the set of their field names when
    that set is unique on both sides and has at least two names.
    """

    fingerprints_matched: int
    fingerprints_renamed: int
    fingerprints_renumbered: int
    locations: NameChanges


def _fingerprint(record: NameRecord) -> frozenset[str] | None:
    return frozenset(map(_norm, record.members)) if len(record.members) >= 2 else None  # noqa: PLR2004


def name_stability(old: Sequence[NameRecord], new: Sequence[NameRecord], *, examples: int = 3) -> NameStability:
    """Compare the class names one configuration gives a previous and a current input."""
    old_names: defaultdict[frozenset[str], list[str]] = defaultdict(list)
    new_names: defaultdict[frozenset[str], list[str]] = defaultdict(list)
    for records, names in ((old, old_names), (new, new_names)):
        for record in records:
            if (fingerprint := _fingerprint(record)) is not None:
                names[fingerprint].append(record.name)
    common = [key for key, names in old_names.items() if len(names) == 1 and len(new_names.get(key, ())) == 1]
    renamed = [(old_names[key][0], new_names[key][0]) for key in common if old_names[key][0] != new_names[key][0]]
    return NameStability(
        fingerprints_matched=len(common),
        fingerprints_renamed=len(renamed),
        fingerprints_renumbered=sum(
            1 for before, after in renamed if _DIGITS.sub("", before) == _DIGITS.sub("", after)
        ),
        locations=location_changes(old, new, examples=examples),
    )
