"""Client settings of a package that declares helpers; their option definitions load only when settings name them."""

from __future__ import annotations

from dataclasses import dataclass

from ..client.options import ClientOptions as CoreClientOptions
from ..client.timing import checked_instance
from ..model_codecs.unset import UNSET, Unset
from . import names as protocol_names


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientOptions(CoreClientOptions):
    """Client settings with the options of the package's declared helpers."""

    protocols: protocol_names.ProtocolClientOptions | Unset | None = UNSET

    def __post_init__(self) -> None:
        """Validate the ordinary settings, and the helper options when they are set."""
        CoreClientOptions.__post_init__(self)
        if self.protocols is not None and not isinstance(self.protocols, Unset):
            checked_instance(self.protocols, (protocol_names.ProtocolClientOptions,), ("protocols",))

    def check_helpers(self, helpers: tuple[tuple[str, str], ...], *, asynchronous: bool) -> None:
        """Check helper names, stores and connector mode before creating the native client."""
        if (protocols := self.protocols) is None or isinstance(protocols, Unset):
            return
        from .options import check_helpers  # noqa: PLC0415 - Only set helper options load their definitions.

        check_helpers(protocols, helpers, asynchronous=asynchronous)
