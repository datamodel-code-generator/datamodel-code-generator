r"""Unicode rules of the target Python version for the names the generator writes.

Which characters an identifier may hold, what `\w` matches, which characters are numeric, how case mappings work and
how identifiers normalize all follow the Unicode version of a Python interpreter, and each Python version updates
Unicode. The generator decides them by `--target-python-version` from tables generated from the interpreter of every
target (`_target_unicode_data`), so the names it writes are the ones the target's own interpreter would give, whatever
interpreter it runs on. ASCII text, which every version treats alike, takes the running interpreter's rules; other text
reads the tables, which load on first use.

Case mappings and normalization of a character no target changes come from the running interpreter; the tables
rebuild them only for text holding a character some target changes or the target lacks, by the rules of CPython's
`str` methods and of Unicode Standard Annex #15.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left, bisect_right
from functools import cache, cached_property
from itertools import chain
from typing import TYPE_CHECKING, Final, NamedTuple

from datamodel_code_generator._format_types import PythonVersion, PythonVersionMin

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

__all__ = (
    "NEWEST_TARGET",
    "HostIdentifiers",
    "capitalize",
    "casefold",
    "host_identifiers",
    "is_identifier",
    "is_nfkc",
    "is_numeric",
    "is_upper",
    "lower",
    "nfc",
    "nfkc",
    "replace_non_word",
    "target_pattern",
    "title",
    "upper",
)

_ASCII_NON_WORD: Final = re.compile(r"\W")
_WORD_ESCAPE: Final = re.compile(r"(?<!\\)\\w")
_IDENTIFIER_COMMENT: Final = "(?#identifier)"
_SUPERSCRIPT_DIGITS: Final = "¹²³⁴⁵⁶⁷⁸⁹"
_SIGMA: Final = "Σ"
_SMALL_SIGMA: Final = "\u03c3"
_FINAL_SIGMA: Final = "ς"
_LOWERCASE: Final = 1
_UPPERCASE: Final = 2
_TITLECASE: Final = 4
_CASE_IGNORABLE: Final = 8
_CASED: Final = _LOWERCASE | _UPPERCASE | _TITLECASE
_ASCII_END: Final = 0x80
_HANGUL_SYLLABLE: Final = 0xAC00
_HANGUL_LEADING: Final = 0x1100
_HANGUL_VOWEL: Final = 0x1161
_HANGUL_TRAILING: Final = 0x11A7
_HANGUL_LEADING_COUNT: Final = 19
_HANGUL_VOWEL_COUNT: Final = 21
_HANGUL_TRAILING_COUNT: Final = 28
_HANGUL_SYLLABLE_COUNT: Final = 11172
_PLACEHOLDER_RANGES: Final = {2: ((0x0780, 0x07A5), (0x07CA, 0x07EA)), 3: ((0x4E00, 0x9FFC),), 4: ((0x20000, 0x2A6DD),)}
"""Letters lent to the running interpreter for identifier characters it does not know, by UTF-8 length: Thaana and
N'Ko, CJK ideographs and CJK Extension B, which every target has without case or normalization data."""


class _Case(NamedTuple):
    upper: str
    lower: str
    title: str
    fold: str
    flags: int


class _Normalization(NamedTuple):
    canonical: str
    compatibility: str
    combining: int


def _escape(code_point: int) -> str:
    return re.escape(chr(code_point)) if code_point < _ASCII_END else chr(code_point)


def _ranges(texts: Iterable[str]) -> list[tuple[int, int]]:
    """Return the inclusive code point ranges of table strings."""
    ranges: list[tuple[int, int]] = []
    for text in texts:
        for token in text.split():
            start, _, end = token.partition("-")
            ranges.append((int(start, 16), int(end or start, 16)))
    return sorted(ranges)


def _without(ranges: list[tuple[int, int]], code_points: Iterable[int]) -> list[tuple[int, int]]:
    """Return ranges without the given code points."""
    points = sorted(code_points)
    result: list[tuple[int, int]] = []
    for start, end in ranges:
        first = start
        for point in points[bisect_left(points, start) : bisect_right(points, end)]:
            if first < point:
                result.append((first, point - 1))
            first = point + 1
        if first <= end:
            result.append((first, end))
    return result


def _class(ranges: Iterable[tuple[int, int]]) -> str:
    """Return the body of a regular expression class matching code point ranges."""
    return "".join(_escape(start) if start == end else f"{_escape(start)}-{_escape(end)}" for start, end in ranges)


def _host_case(character: str) -> _Case:
    """Return the running interpreter's case data of a character no target changes."""
    cased = character.islower() or character.istitle()
    sigma = ("A" + _SIGMA + character).lower()[1] if cased else ("A" + character + _SIGMA).lower()[-1]
    flags = (
        _LOWERCASE * character.islower()
        | _UPPERCASE * character.isupper()
        | _TITLECASE * (character.istitle() and not character.isupper())
        | _CASE_IGNORABLE * (sigma == _FINAL_SIGMA)
    )
    return _Case(character.upper(), character.lower(), character.title(), character.casefold(), flags)


class _TargetUnicode:
    """The Unicode data of one target, each table built on first use."""

    def __init__(self, target: PythonVersion) -> None:
        from datamodel_code_generator import _target_unicode_data as data  # noqa: PLC0415

        self._data = data
        self._index = data.TARGETS.index(target.value)

    def _set(self, name: str) -> list[tuple[int, int]]:
        texts: tuple[str, ...] = getattr(self._data, name)
        return _ranges(texts[: self._index + 1])

    @cached_property
    def identifier(self) -> re.Pattern[str]:
        """Match an identifier."""
        return re.compile(f"[{_class(self._set('IDENTIFIER_START'))}][{_class(self._set('IDENTIFIER_CONTINUE'))}]*")

    @cached_property
    def identifier_character(self) -> re.Pattern[str]:
        """Match a character an identifier may hold after its first."""
        return re.compile(f"[{_class(self._set('IDENTIFIER_CONTINUE'))}]")

    @cached_property
    def word(self) -> str:
        r"""Return the class body of `\w`, which the target matches."""
        return _class(self._set("WORD"))

    @cached_property
    def non_word(self) -> re.Pattern[str]:
        r"""Match a superscript digit or a character `\w` does not match."""
        return re.compile(f"[{_SUPERSCRIPT_DIGITS}]|[^{self.word}]")

    @cached_property
    def numeric(self) -> re.Pattern[str]:
        """Match a numeric character."""
        return re.compile(f"[{_class(self._set('NUMERIC'))}]")

    @cached_property
    def assigned(self) -> list[tuple[int, int]]:
        """Return the ranges of the characters the target has."""
        return self._set("ASSIGNED")

    @cached_property
    def added(self) -> re.Pattern[str]:
        """Match a character the first target does not have, whose compositions the tables list."""
        return re.compile(f"[^{_class(_ranges(self._data.ASSIGNED[:1]))}]")

    @cached_property
    def unassigned(self) -> re.Pattern[str]:
        """Match a character the target does not have."""
        return re.compile(f"[^{_class(self.assigned)}]")

    @cached_property
    def case(self) -> dict[str, _Case]:
        """Return the case data of each character whose case data differs between targets."""
        cases: dict[str, _Case] = {}
        for code_point, changes in self._data.CASE_CHANGES.items():
            character = chr(code_point)
            case = _Case(character, character, character, character, 0)
            for index, upper, lower, title, fold, flags in changes:
                if index > self._index:
                    break
                case = _Case(upper or character, lower or character, title or character, fold or character, flags)
            cases[character] = case
        return cases

    @cached_property
    def case_special(self) -> re.Pattern[str]:
        """Match a character whose case data differs between targets or that the target does not have."""
        return re.compile(f"[^{_class(_without(self.assigned, map(ord, self.case)))}]")

    @cached_property
    def normalization(self) -> dict[str, _Normalization]:
        """Return the normalization data of each character whose data differs between targets."""
        items: dict[str, _Normalization] = {}
        for code_point, changes in self._data.NORMALIZATION_CHANGES.items():
            character = chr(code_point)
            item = _Normalization(character, character, 0)
            for index, canonical, compatibility, combining in changes:
                if index > self._index:
                    break
                item = _Normalization(canonical or character, compatibility or character, combining)
            items[character] = item
        return items

    @cached_property
    def compositions(self) -> dict[tuple[str, str], str]:
        """Return the canonical compositions of the target that some target lacks."""
        return {
            (chr(first), chr(second)): chr(composite)
            for index, first, second, composite in self._data.COMPOSITION_CHANGES
            if index <= self._index
        }

    @cached_property
    def normalization_special(self) -> re.Pattern[str]:
        """Match a character whose normalization differs between targets or that the target does not have."""
        code_points = {
            *map(ord, self.normalization),
            *(code_point for _, *pair, _ in self._data.COMPOSITION_CHANGES for code_point in pair),
        }
        return re.compile(f"[^{_class(_without(self.assigned, code_points))}]")

    def case_of(self, character: str) -> _Case:
        """Return the case data of a character."""
        if (case := self.case.get(character)) is not None:
            return case
        if self.unassigned.match(character):
            return _Case(character, character, character, character, 0)
        return _host_case(character)

    @staticmethod
    def lower_at(value: str, cases: list[_Case], index: int) -> str:
        """Return the lowercase of a character of text, applying the final sigma rule as `str.lower()` does."""
        if value[index] != _SIGMA:
            return cases[index].lower
        before = index - 1
        while before >= 0 and cases[before].flags & _CASE_IGNORABLE:
            before -= 1
        final = before >= 0 and bool(cases[before].flags & _CASED)
        if final and index + 1 < len(value):
            after = index + 1
            while after < len(value) and cases[after].flags & _CASE_IGNORABLE:
                after += 1
            final = after == len(value) or not cases[after].flags & _CASED
        return _FINAL_SIGMA if final else _SMALL_SIGMA

    def normalization_of(self, character: str) -> _Normalization:
        """Return the normalization data of a character."""
        if (item := self.normalization.get(character)) is not None:
            return item
        if self.unassigned.match(character):
            return _Normalization(character, character, 0)
        return _Normalization(
            unicodedata.normalize("NFD", character),
            unicodedata.normalize("NFKD", character),
            unicodedata.combining(character),
        )

    def primary_composite(self, first: str, second: str) -> str | None:
        """Return the character two characters compose to, if any."""
        if (composite := self.compositions.get((first, second))) is not None:
            return composite
        leading, vowel = ord(first) - _HANGUL_LEADING, ord(second) - _HANGUL_VOWEL
        if 0 <= leading < _HANGUL_LEADING_COUNT and 0 <= vowel < _HANGUL_VOWEL_COUNT:
            return chr(_HANGUL_SYLLABLE + (leading * _HANGUL_VOWEL_COUNT + vowel) * _HANGUL_TRAILING_COUNT)
        syllable, trailing = ord(first) - _HANGUL_SYLLABLE, ord(second) - _HANGUL_TRAILING
        if 0 <= syllable < _HANGUL_SYLLABLE_COUNT and not syllable % _HANGUL_TRAILING_COUNT:
            return chr(ord(first) + trailing) if 0 < trailing < _HANGUL_TRAILING_COUNT else None
        if self.added.match(first) or self.added.match(second):
            return None
        composed = unicodedata.normalize("NFC", first + second)
        if len(composed) == 1 and unicodedata.decomposition(composed) == f"{ord(first):04X} {ord(second):04X}":
            return composed
        return None

    def normalize(self, value: str, *, compatibility: bool) -> str:
        """Return the NFC or NFKC form of text: decompose, order combining marks, then compose."""
        characters = [
            character
            for original in value
            for character in (
                self.normalization_of(original).compatibility
                if compatibility
                else self.normalization_of(original).canonical
            )
        ]
        classes = [self.normalization_of(character).combining for character in characters]
        start = 0
        while start < len(characters):
            end = start
            while end < len(characters) and classes[end]:
                end += 1
            if end > start:
                order = sorted(range(start, end), key=classes.__getitem__)
                characters[start:end] = [characters[position] for position in order]
                classes[start:end] = [classes[position] for position in order]
            start = end + 1
        composed: list[str] = []
        starter: int | None = None
        last_class = -1
        for character, combining in zip(characters, classes, strict=True):
            if (
                starter is not None
                and (last_class == -1 or 0 < last_class < combining)
                and (composite := self.primary_composite(composed[starter], character)) is not None
            ):
                composed[starter] = composite
                continue
            if combining:
                last_class = combining
            else:
                starter, last_class = len(composed), -1
            composed.append(character)
        return "".join(composed)


NEWEST_TARGET: Final = max(PythonVersion, key=lambda version: version.version_key)
"""The newest target: its rules accept every identifier some target accepts."""


@cache
def _unicode(target: PythonVersion | None) -> _TargetUnicode:
    return _TargetUnicode(target or PythonVersionMin)


def is_identifier(value: str, target: PythonVersion | None) -> bool:
    """Return whether text is an identifier in the target, as `str.isidentifier()` on its interpreter."""
    return value.isidentifier() if value.isascii() else _unicode(target).identifier.fullmatch(value) is not None


def replace_non_word(value: str, target: PythonVersion | None) -> str:
    r"""Replace each superscript digit and each character `\w` does not match in the target with an underscore."""
    return _ASCII_NON_WORD.sub("_", value) if value.isascii() else _unicode(target).non_word.sub("_", value)


def is_numeric(character: str, target: PythonVersion | None) -> bool:
    """Return whether a character is numeric in the target, as `str.isnumeric()` on its interpreter."""
    return character.isnumeric() if character.isascii() else _unicode(target).numeric.match(character) is not None


@cache
def target_pattern(source: str, target: PythonVersion | None, flags: int = 0) -> re.Pattern[str]:
    r"""Compile a regular expression whose `\w` and `(?#identifier)` match as in the target.

    `\w` matches what the target's `\w` matches, and the comment `(?#identifier)` an identifier of the target.
    """
    unicode = _unicode(target)
    word = f"[{unicode.word}]"
    source = _WORD_ESCAPE.sub(lambda _: word, source).replace(_IDENTIFIER_COMMENT, f"(?:{unicode.identifier.pattern})")
    return re.compile(source, flags)


def _case_rule(
    value: str,
    target: PythonVersion | None,
    method: Callable[[str], str],
    rule: Callable[[_TargetUnicode, str], str],
) -> str:
    if value.isascii() or (unicode := _unicode(target)).case_special.search(value) is None:
        return method(value)
    return rule(unicode, value)


def _upper(unicode: _TargetUnicode, value: str) -> str:
    return "".join(unicode.case_of(character).upper for character in value)


def _lower(unicode: _TargetUnicode, value: str) -> str:
    cases = [unicode.case_of(character) for character in value]
    return "".join(unicode.lower_at(value, cases, index) for index in range(len(value)))


def _title(unicode: _TargetUnicode, value: str) -> str:
    cases = [unicode.case_of(character) for character in value]
    parts: list[str] = []
    previous_cased = False
    for index, case in enumerate(cases):
        parts.append(unicode.lower_at(value, cases, index) if previous_cased else case.title)
        previous_cased = bool(case.flags & _CASED)
    return "".join(parts)


def _capitalize(unicode: _TargetUnicode, value: str) -> str:
    cases = [unicode.case_of(character) for character in value]
    return "".join(unicode.lower_at(value, cases, index) if index else case.title for index, case in enumerate(cases))


def _casefold(unicode: _TargetUnicode, value: str) -> str:
    return "".join(unicode.case_of(character).fold for character in value)


def upper(value: str, target: PythonVersion | None) -> str:
    """Return text in uppercase, as `str.upper()` on the target's interpreter."""
    return _case_rule(value, target, str.upper, _upper)


def lower(value: str, target: PythonVersion | None) -> str:
    """Return text in lowercase, as `str.lower()` on the target's interpreter."""
    return _case_rule(value, target, str.lower, _lower)


def title(value: str, target: PythonVersion | None) -> str:
    """Return text in titlecase, as `str.title()` on the target's interpreter."""
    return _case_rule(value, target, str.title, _title)


def capitalize(value: str, target: PythonVersion | None) -> str:
    """Return text capitalized, as `str.capitalize()` on the target's interpreter."""
    return _case_rule(value, target, str.capitalize, _capitalize)


def casefold(value: str, target: PythonVersion | None) -> str:
    """Return text case-folded, as `str.casefold()` on the target's interpreter."""
    return _case_rule(value, target, str.casefold, _casefold)


def is_upper(value: str, target: PythonVersion | None) -> bool:
    """Return whether text is uppercase, as `str.isupper()` on the target's interpreter."""
    if value.isascii() or (unicode := _unicode(target)).case_special.search(value) is None:
        return value.isupper()
    flags = [unicode.case_of(character).flags for character in value]
    if len(flags) == 1:
        return bool(flags[0] & _UPPERCASE)
    return not any(flag & (_LOWERCASE | _TITLECASE) for flag in flags) and any(flag & _UPPERCASE for flag in flags)


def _normalized(value: str, target: PythonVersion | None, *, compatibility: bool) -> str:
    if value.isascii():
        return value
    if (unicode := _unicode(target)).normalization_special.search(value) is None:
        return unicodedata.normalize("NFKC" if compatibility else "NFC", value)
    return unicode.normalize(value, compatibility=compatibility)


def nfc(value: str, target: PythonVersion | None) -> str:
    """Return the NFC form of text in the target."""
    return _normalized(value, target, compatibility=False)


def nfkc(value: str, target: PythonVersion | None) -> str:
    """Return the NFKC form of text in the target, which Python gives an identifier when it parses it."""
    return _normalized(value, target, compatibility=True)


def is_nfkc(value: str, target: PythonVersion | None) -> bool:
    """Return whether text is in NFKC form in the target."""
    return value.isascii() or nfkc(value, target) == value


class HostIdentifiers:
    """Source with the identifier characters the running interpreter does not know swapped for letters it knows.

    Generated code holds identifiers of the target, which may be newer than the running interpreter. Each character an
    identifier of the newest target may hold and the running interpreter does not know is swapped for a letter of the
    same UTF-8 length that every target has and neither the source nor its NFKC form uses, so offsets and lengths stay
    put and the swap reverses exactly. A string the running interpreter parsed from the source takes its characters
    back before anything spells it again, since a lent letter prints where a joiner it stands for does not.
    """

    __slots__ = ("_given_back", "_lent", "_pools", "_used", "source")

    def __init__(self, source: str, unknown: Iterable[str]) -> None:
        """Lend letters for the unknown characters of source."""
        self._used = set(source)
        self._used.update(
            nfkc("".join(sorted(character for character in self._used if not character.isascii())), NEWEST_TARGET)
        )
        self._pools: dict[int, Iterator[str]] = {}
        self._lent: dict[int, str] = {}
        self._given_back: dict[int, str] = {}
        for character in unknown:
            self._lend(character)
        self.source = source.translate(self._lent)

    def _lend(self, character: str) -> None:
        if ord(character) in self._lent:
            return
        length = len(character.encode())
        placeholder = next(self._pools.setdefault(length, _placeholders(length, self._used)))
        self._lent[ord(character)] = placeholder
        self._given_back[ord(placeholder)] = character

    def restore_text(self, text: str) -> str:
        """Return text taken from the source with each lent letter given back."""
        return text.translate(self._given_back)

    def restore_name(self, name: str) -> str:
        """Return a name the running interpreter parsed from the source in the NFKC form the target gives it.

        The newest tables give the target's form for every identifier of an older target too.
        """
        return nfkc(name.translate(self._given_back), NEWEST_TARGET)

    def parsed_name(self, name: str) -> str:
        """Return a name the running interpreter parsed from the source as the target parses it, still lent."""
        if not any(ord(character) in self._given_back for character in name):
            return name
        target_name = self.restore_name(name)
        self._used.update(target_name)
        for character in _unknown(target_name):
            self._lend(character)
        return target_name.translate(self._lent)


def _placeholders(length: int, used: set[str]) -> Iterator[str]:
    """Return the letters of a UTF-8 length that can be lent, in a fixed order: those the source does not use."""
    code_points = chain.from_iterable(range(start, end + 1) for start, end in _PLACEHOLDER_RANGES[length])
    return (character for character in map(chr, code_points) if character not in used)


def _unknown(text: str) -> list[str]:
    """Return the characters of text an identifier of the newest target may hold that the running interpreter lacks."""
    newest = _unicode(NEWEST_TARGET).identifier_character
    return sorted(
        character
        for character in set(text)
        if not character.isascii() and newest.match(character) and not ("a" + character).isidentifier()
    )


def host_identifiers(source: str) -> HostIdentifiers | None:
    """Return source with letters lent for the identifier characters the running interpreter does not know.

    Return None when the running interpreter knows every character.
    """
    if source.isascii() or not (unknown := _unknown(source)):
        return None
    return HostIdentifiers(source, unknown)
