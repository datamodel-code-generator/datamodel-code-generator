"""A package with a dispatch table in ``parser/kinds.py``, an object-map builder in ``parser/maps.py`` and a builder of
``Ref`` in ``model.py``.

``Parser._build_array`` calls the parse methods of the ``enum`` row (held through the shared rows and a wrapper) and of
the default outside the table, ``_build_default`` calls the one of the ``array`` row (held through ``partial``),
``parse_object`` calls ``_shared``, the parse method of another row, and ``shortcut`` and ``alias`` read the table
outside ``parse_site``; ``parameter`` reads a parameter spelled as a parse method. ``via_host`` calls the parse method
and the table's ``choose`` through the host protocol ``KindsHost``, and ``typed`` only asks the table for
``typed_children``. ``build_map`` hands the map to its part ``part``; ``flags`` builds the dict flag through a ``**``
dict display and ``seeded_slot`` asks ``_container_flags`` for the ``dict`` slot outside them, both through the host
protocol ``MapsHost``, which ``MapParser`` implements (heki ties a call through a protocol to a class only where one
implements it), and ``subclass_map`` builds one as a subclass of ``DataType``. ``not_a_map``, ``other_slot`` (another
slot) and ``outside`` (outside ``parser/``) build no map, and ``handed_on`` returns a flag dict that no call takes
there. ``naming.py`` builds a ``Ref`` of ``model.py`` through an alias and by ``model_construct`` outside
``name_model``; ``other.py`` builds a class of its own of that name.
"""
