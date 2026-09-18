"""Canonical declaration addresses for the opt-in OpenAPI generation scope."""

from __future__ import annotations

import re
from collections.abc import Sequence  # noqa: TC003 - Public annotations support get_type_hints().
from dataclasses import dataclass
from functools import cached_property, lru_cache
from urllib.parse import quote, unquote

from datamodel_code_generator import SchemaParseError
from datamodel_code_generator._source import YamlValue  # noqa: TC001 - Public annotations support get_type_hints().
from datamodel_code_generator.parser.jsonschema import split_json_pointer
from datamodel_code_generator.reference import (
    SPECIAL_PATH_MARKER,
    ModelResolver,
    Reference,
)

_BAD_PERCENT = re.compile(r"%(?![0-9a-fA-F]{2})")
_BAD_ESCAPE = re.compile(r"~(?![01])")


def pointer_tokens(ref: str) -> tuple[str, ...] | None:
    """Decode a URI fragment once, distinguishing named anchors from pointers."""
    fragment = ref.partition("#")[2]
    msg = f"API_REF_NONCANONICAL_POINTER: {ref!r}"
    if _BAD_PERCENT.search(fragment):
        raise SchemaParseError(msg, path=[ref])
    try:
        decoded = unquote(fragment, errors="strict")
    except UnicodeError as exc:
        raise SchemaParseError(msg, path=[ref], original_error=exc) from exc
    if not decoded:
        return ()
    if not decoded.startswith("/"):
        return None
    if _BAD_ESCAPE.search(decoded):
        raise SchemaParseError(msg, path=[ref])
    return tuple(token.replace("~1", "/").replace("~0", "~") for token in decoded[1:].split("/"))


def check_pointer_interpretation(raw: dict[str, YamlValue], original: str, resolved: str) -> None:
    """Compare declaration addresses, including mappings whose values are aliases."""
    if (tokens := pointer_tokens(original)) is None:
        return
    fragment = original.partition("#")[2]
    if fragment[:3].lower() == "%2f":
        fragment = "/" + fragment[3:]
    if tuple(split_json_pointer(raw, fragment)) != tokens:
        msg = f"API_REF_NONCANONICAL_POINTER: {original!r}"
        raise SchemaParseError(msg, path=[resolved])


def pointer_fragment(tokens: Sequence[str]) -> str:
    """Encode raw declaration tokens without conflating slashes or literal percent signs."""
    return (
        "/" + "/".join(quote(token.replace("~", "~0").replace("/", "~1"), safe="~") for token in tokens)
        if tokens
        else ""
    )


def canonical_ref(ref: str) -> str:
    """Normalize the source portion while preserving generated special paths verbatim."""
    source, marker, generated = ref.partition(SPECIAL_PATH_MARKER)
    if "#" not in source or (tokens := pointer_tokens(source)) is None:
        return ref
    canonical = source.partition("#")[0] + "#" + pointer_fragment(tokens)
    return canonical + marker + generated


@dataclass(frozen=True, slots=True)
class ApiDeclarationId:
    """Identify an actual declaration independently of its generated Python name."""

    document: str
    tokens: tuple[str, ...]


class ApiModelResolver(ModelResolver):
    """Use one canonical reference key for raw declarations and all pointer spellings."""

    @cached_property
    def original_refs(self) -> dict[str, list[str]]:
        """Keep spellings for checks against documents loaded by the existing engine."""
        return {}

    @cached_property
    def loaded_documents(self) -> dict[str, dict[str, YamlValue]]:
        """Borrow only documents already acquired by the model engine."""
        return {}

    def resolve_ref(self, path: Sequence[str] | str) -> str:
        """Canonicalize pointers before the existing file/base/ID resolution."""
        original = path if isinstance(path, str) else self.join_path(tuple(path))
        resolved = canonical_ref(super().resolve_ref(canonical_ref(original)))
        spellings = self.original_refs.setdefault(resolved, [])
        if original not in spellings:
            spellings.append(original)
        if (
            SPECIAL_PATH_MARKER not in original
            and (raw := self.loaded_documents.get(resolved.partition("#")[0])) is not None
        ):
            check_pointer_interpretation(raw, original, resolved)
        return resolved

    def add_ref(self, ref: str, resolved: bool = False) -> Reference:  # noqa: FBT001, FBT002
        """Reserve names from canonical spelling exactly once per declaration."""
        canonical = canonical_ref(ref) if resolved else self.resolve_ref(ref)
        return super().add_ref(canonical, resolved=True)

    @staticmethod
    @lru_cache(maxsize=4096)
    def join_path(path: tuple[str, ...]) -> str:
        """Encode raw tokens after the compiler's document/pointer boundary."""
        for index, part in enumerate(path):
            if "#" not in part:
                continue
            document, _, prefix = part.partition("#")
            document = "/".join((*path[:index], document)) if document else "/".join(path[:index])
            if SPECIAL_PATH_MARKER in part:
                return canonical_ref(document + "#" + "/".join((prefix, *path[index + 1 :])))
            tokens = list(pointer_tokens("#" + prefix) or ())
            for offset, token in enumerate(path[index + 1 :], start=index + 1):
                if SPECIAL_PATH_MARKER in token:
                    return document + "#" + pointer_fragment(tokens) + "/" + "/".join(path[offset:])
                tokens.append(token)
            return document + "#" + pointer_fragment(tokens)
        return "/".join(path) + "#"
