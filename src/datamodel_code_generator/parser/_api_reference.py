"""Canonical declaration addresses for the opt-in OpenAPI generation scope and for --read-escaped-ref-pointers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence  # noqa: TC003 - Public annotations support get_type_hints().
from functools import cached_property, lru_cache
from types import MappingProxyType
from typing import ClassVar, Literal, TypeAlias
from urllib.parse import quote

from typing_extensions import override

from datamodel_code_generator import SchemaParseError
from datamodel_code_generator._json_pointer import (
    decode_token,
    escape_token,
    fragment_pointer,
    is_pointer_fragment,
    pointer_opening,
    reference_tokens,
)
from datamodel_code_generator._source import YamlValue  # noqa: TC001 - Public annotations support get_type_hints().
from datamodel_code_generator._url_redaction import redact_reference
from datamodel_code_generator.parser._schema_reading import split_json_pointer
from datamodel_code_generator.python_literal import stable_repr
from datamodel_code_generator.reference import (
    SPECIAL_PATH_MARKER,
    ModelResolver,
    Reference,
    _is_special_path,
)


def pointer_tokens(ref: str, *, strict: bool = True) -> tuple[str, ...] | None:
    """Read a URI fragment (``reference_tokens``), distinguishing named anchors from pointers.

    ``strict`` refuses a ``%`` that starts no octet, octets that are no UTF-8 and a ``~`` that starts no escape;
    otherwise they are read as the characters they spell.
    """
    try:
        return reference_tokens(ref.partition("#")[2], strict=strict)
    except ValueError as exc:
        msg = f"API_REF_NONCANONICAL_POINTER: {stable_repr(redact_reference(ref))}"
        raise SchemaParseError(msg, path=[ref], original_error=exc) from exc


def check_pointer_interpretation(raw: dict[str, YamlValue], original: str, resolved: str) -> None:
    """Compare declaration addresses, including mappings whose values are aliases."""
    if (tokens := pointer_tokens(original)) is None:
        return
    fragment = original.partition("#")[2]
    pointer = fragment if (opening := pointer_opening(fragment)) is None else "/" + fragment[opening:]
    if tuple(split_json_pointer(raw, pointer)) != tokens:
        msg = f"API_REF_NONCANONICAL_POINTER: {stable_repr(redact_reference(original))}"
        raise SchemaParseError(msg, path=[resolved])


def pointer_fragment(tokens: Sequence[str]) -> str:
    """Encode raw declaration tokens without conflating slashes or literal percent signs."""
    return "/" + "/".join(quote(escape_token(token), safe="~") for token in tokens) if tokens else ""


KeyForm: TypeAlias = Literal["api", "fragment"]
"""How a canonical reference reads and writes a pointer: row ``api``, the ``api`` scope's, reads it strictly and
percent-encodes every character but the unreserved ones and ``~`` (``pointer_fragment``); row ``fragment``, that of
--read-escaped-ref-pointers, reads it leniently and writes it in the fewest characters that read back
(``fragment_pointer``), so a pointer whose names hold no ``~``, ``/`` or ``%`` keeps its text."""
_KEY_POINTERS: dict[KeyForm, Callable[[Sequence[str]], str]] = {"api": pointer_fragment, "fragment": fragment_pointer}


def canonical_ref(ref: str, *, form: KeyForm = "api", strict: bool | None = None) -> str:
    """Normalize the source portion while preserving generated special paths verbatim (``pointer_tokens``).

    The pointer is read strictly in row ``api`` and leniently in row ``fragment``, unless ``strict`` says otherwise.
    """
    source, marker, generated = ref.partition(SPECIAL_PATH_MARKER)
    if (
        "#" not in source
        or (tokens := pointer_tokens(source, strict=form == "api" if strict is None else strict)) is None
    ):
        return ref
    canonical = source.partition("#")[0] + "#" + _KEY_POINTERS[form](tokens)
    return canonical + marker + generated


def canonical_path(path: tuple[str, ...], *, form: KeyForm = "api") -> str:
    """Join a parse path into its canonical reference: the components after the document's pointer are raw tokens.

    The component that holds ``#`` ends the document and starts the pointer, read as a URI fragment
    (``pointer_tokens``); a generated special path is kept verbatim. Row ``fragment`` joins it as
    ``ModelResolver.join_path`` does, without a ``/`` before a component that opens with ``#``.
    """
    encode = _KEY_POINTERS[form]
    for index, part in enumerate(path):
        if "#" not in part:
            continue
        document, _, prefix = part.partition("#")
        document = "/".join((*path[:index], document)) if document else "/".join(path[:index])
        if _is_special_path(part):
            return canonical_ref(document + "#" + "/".join((prefix, *path[index + 1 :])), form=form)
        tokens = list(pointer_tokens("#" + prefix, strict=form == "api") or ())
        for offset, token in enumerate(path[index + 1 :], start=index + 1):
            if _is_special_path(token):
                generated = "/" + "/".join(path[offset:])
                return (
                    document
                    + "#"
                    + encode(tokens)
                    + (generated.replace("/#", "#") if form == "fragment" else generated)
                )
            tokens.append(token)
        return document + "#" + encode(tokens)
    return "/".join(path) + "#"


class _WrittenPathKeysResolver(ModelResolver):
    """A model resolver of --read-escaped-ref-pointers, whose ``--model-name-map`` path keys match in any spelling.

    A key that names a path names it by its canonical reference in the resolver's key form, read leniently, so every
    key that spells one pointer names its model: each counts as found, and the first of them, in the order of the
    map, renames it, as the first of the keys that name one model by its path and by its name does.
    """

    _written_key_form: ClassVar[KeyForm] = "fragment"
    _written_keys_of: Mapping[str, str] | None = None
    _written_keys: Mapping[str, tuple[str, ...]] = MappingProxyType({})

    @override
    def _written_path_keys(self, key: str) -> tuple[str, ...]:
        """Return the ``--model-name-map`` keys that name a canonical path key, in the order of the map."""
        if self._written_keys_of is not self.model_name_map:
            self._written_keys_of = self.model_name_map
            written: dict[str, tuple[str, ...]] = {}
            for name in self.model_name_map:
                canonical = canonical_ref(name, form=self._written_key_form, strict=False)
                written[canonical] = (*written.get(canonical, ()), name)
            self._written_keys = written
        return self._written_keys.get(key, (key,))


class EscapedPointerModelResolver(_WrittenPathKeysResolver):
    """The model resolver of --read-escaped-ref-pointers: a pointer is keyed by its tokens, as the api scope keys it.

    A ``$ref`` that writes ``~0``, ``~1`` or a percent-encoded octet and the declaration it names share one key
    (``canonical_ref``), read leniently: an escape that is not canonical stands for the characters it spells. A parse
    path holds raw tokens after its document's pointer (``canonical_path``). A model a reference adds is named after the
    decoded last token of its pointer, as the declaration that the pointer names is.
    """

    @override
    def resolve_ref(self, path: Sequence[str] | str) -> str:
        """Canonicalize pointers before and after the file, base and identifier resolution."""
        original = path if isinstance(path, str) else self.join_path(tuple(path))
        return canonical_ref(super().resolve_ref(canonical_ref(original, form="fragment")), form="fragment")

    @override
    def add_ref(self, ref: str, resolved: bool = False) -> Reference:
        """Add the reference at the canonical key of ``ref``."""
        return super().add_ref(
            canonical_ref(ref, form="fragment") if resolved else self.resolve_ref(ref), resolved=True
        )

    @override
    def _add_ref_at(self, path: str, ref: str) -> Reference:
        """Return the Reference at the canonical ``path``, named after the decoded last token of ``ref``."""
        return super()._add_ref_at(canonical_ref(path, form="fragment"), ref)

    @override
    def ref_original_name(self, ref: str, path: str) -> str:
        """Return the name a reference derives: the last token of its pointer, decoded."""
        name = super().ref_original_name(ref, path)
        return decode_token(name) if (fragment := ref.partition("#")[2]) and is_pointer_fragment(fragment) else name

    @staticmethod
    @override
    @lru_cache(maxsize=4096)
    def join_path(path: tuple[str, ...]) -> str:
        """Join a parse path into its canonical reference (``canonical_path``)."""
        return canonical_path(path, form="fragment")


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

    @override
    def resolve_ref(self, path: Sequence[str] | str) -> str:
        """Canonicalize pointers before the existing file/base/ID resolution."""
        original = path if isinstance(path, str) else self.join_path(tuple(path))
        resolved = canonical_ref(super().resolve_ref(canonical_ref(original)))
        spellings = self.original_refs.setdefault(resolved, [])
        if original not in spellings:
            spellings.append(original)
        if (
            not _is_special_path(original)
            and (raw := self.loaded_documents.get(resolved.partition("#")[0])) is not None
        ):
            check_pointer_interpretation(raw, original, resolved)
        return resolved

    @override
    def add_ref(self, ref: str, resolved: bool = False) -> Reference:
        """Reserve names from canonical spelling exactly once per declaration."""
        canonical = canonical_ref(ref) if resolved else self.resolve_ref(ref)
        return super().add_ref(canonical, resolved=True)

    @override
    def _add_ref_at(self, path: str, ref: str) -> Reference:
        """Reserve names from canonical spelling exactly once per declaration, whatever the reference spells."""
        canonical = canonical_ref(path)
        return super()._add_ref_at(canonical, canonical)

    @staticmethod
    @override
    @lru_cache(maxsize=4096)
    def join_path(path: tuple[str, ...]) -> str:
        """Encode raw tokens after the compiler's document/pointer boundary (``canonical_path``)."""
        return canonical_path(path)


class EscapedPointerApiModelResolver(_WrittenPathKeysResolver, ApiModelResolver):
    """The model resolver of the api scope under --read-escaped-ref-pointers, which keys pointers as the scope does."""

    _written_key_form: ClassVar[KeyForm] = "api"


ESCAPED_POINTER_RESOLVERS: Mapping[type[ModelResolver], type[ModelResolver]] = MappingProxyType({
    ModelResolver: EscapedPointerModelResolver,
    ApiModelResolver: EscapedPointerApiModelResolver,
})
"""The model resolver --read-escaped-ref-pointers takes in place of a parser's own (``_model_resolver_class``)."""
