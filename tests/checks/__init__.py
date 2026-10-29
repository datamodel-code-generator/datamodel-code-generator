"""Debug checks of the test suite, which wrap the generator's internals instead of living in them.

Each area has one environment variable, ``DATAMODEL_CODE_GENERATOR_CHECK_<AREA>``: unset, empty or ``0`` turns it off,
as in production; ``1`` raises at the first violation, with a message that starts with the variable; ``report:<path>``
appends every violation to ``<path>`` as one JSON line and goes on. A parse reads every variable once, when its pipeline
starts (``parse.run_steps``), and rejects any other value; the wrappers ask ``areas.mode(area)``, which is None
outside a parse.

``installer.install`` wraps the attributes that ``seams.SEAMS`` names. ``plugin.py`` installs the checks in the test
process and, through ``child/sitecustomize.py``, which wraps each module as it is imported, in every Python a test
starts; ``python -m tests.checks`` runs ``datamodel-codegen`` with them.
"""
