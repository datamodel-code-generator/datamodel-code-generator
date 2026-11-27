"""A package whose ``core.py`` holds the homes: read by name, by module attribute and through a re-export.

``adapter.py`` holds the sites ``site`` and ``Site``, ``whole.py`` is a site as a whole module. ``bypass`` reads
``Rule`` and calls ``decide`` outside them, and ``run_through_helper`` hands the call to a private helper. ``typed``
names ``Rule`` in an annotation and reads ``TABLE``, a constant home. ``raises.py`` raises and catches the exception
homes ``Failure`` and ``Detail`` (a subclass of it), and ``through.py`` reads ``Rule`` in a type alias under ``if
TYPE_CHECKING:``, where it imports ``Rule``.
"""

from .core import decide
