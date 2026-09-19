"""Attempt-owned source borrowing for optional OpenAPI contract consumers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator._generation_contract import (
    BindingCaptureError,
    SourceDocument,
    SourceDocumentId,
    SourceLocation,
)

if TYPE_CHECKING:
    from datamodel_code_generator._source import YamlValue


class SourceLease:
    """Borrow only documents obtained by the model engine, until planning finishes.

    Consumers may read borrowed nodes during planning but must project the values
    they need before closing. This object never loads, validates, copies, or resolves
    a document, and it is deliberately excluded from immutable contract batches.
    """

    def __init__(self) -> None:
        """Create an empty lease without retaining an input or parser."""
        self._documents: list[SourceDocument] = []
        self._raw: list[dict[str, YamlValue]] = []
        self._by_uri: dict[str, SourceDocumentId] = {}
        self._closed = False

    def _check_open(self) -> None:
        if self._closed:
            msg = "Source lease is closed"
            raise RuntimeError(msg)

    def register(self, uri: str, document: dict[str, YamlValue]) -> SourceDocumentId:
        """Borrow an actual loader result without conflating URI or node identity."""
        self._check_open()
        if (existing := self._by_uri.get(uri)) is not None:
            # Deferred pointer processing can reload the same source into another
            # mapping. Document identity is its loader key; producer frames retain
            # the actual raw node used by each validation call separately.
            return existing
        identity = SourceDocumentId(len(self._documents))
        self._documents.append(SourceDocument(identity, uri))
        self._raw.append(document)
        self._by_uri[uri] = identity
        return identity

    def documents(self) -> tuple[SourceDocument, ...]:
        """Return document values in actual observation order without raw mappings."""
        self._check_open()
        return tuple(self._documents)

    def document_id(self, uri: str) -> SourceDocumentId | None:
        """Look up an already borrowed document without resolving alternate URIs."""
        self._check_open()
        return self._by_uri.get(uri)

    def borrow(self, location: SourceLocation) -> YamlValue:
        """Read an original plain JSON pointer from an already borrowed document."""
        self._check_open()
        msg = "Source location does not identify an observed node"
        if not 0 <= location.document < len(self._raw):
            raise BindingCaptureError(msg)
        value: YamlValue = self._raw[location.document]
        if not location.pointer:
            return value
        if not location.pointer.startswith("/"):
            msg = "Source locations require a plain JSON pointer"
            raise BindingCaptureError(msg)
        for token in location.pointer[1:].split("/"):
            key = token.replace("~1", "/").replace("~0", "~")
            try:  # Share lookup error conversion.
                match value:
                    case dict():
                        value = value[key]
                    case list() if key == "0" or (key.isascii() and key.isdecimal() and not key.startswith("0")):
                        value = value[int(key)]
                    case _:
                        raise BindingCaptureError(msg)
            except (KeyError, IndexError, ValueError):
                raise BindingCaptureError(msg) from None
        return value

    def close(self) -> None:
        """Release all borrowed mappings; repeated cleanup remains harmless."""
        self._closed = True
        self._raw.clear()
        self._documents.clear()
        self._by_uri.clear()
