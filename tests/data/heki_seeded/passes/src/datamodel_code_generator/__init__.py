"""A pipeline whose passes write a field's ``read_only`` (``METADATA``, final after ``MODULES``) and two entries of
its ``extras`` through a store operation given the key: ``init`` (``REQUIRED``) and ``kw_only`` (a presentation row of
``KEYWORD_ONLY``, final at B2); one enters a phase, and the parser method the module hook calls writes a fact."""
