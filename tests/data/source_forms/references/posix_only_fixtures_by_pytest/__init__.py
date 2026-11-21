"""Rule posix-only-call: a test reaches the fixtures pytest gives it from its ``conftest.py`` files.

A test is given the fixtures it asks for by parameter, the ``usefixtures`` of its decorator, class or module, and the
``autouse`` fixtures in scope, from the ``conftest.py`` of its folder and of the folders above it (the nearest
definition of a name wins). A fixture that asks for another fixture reaches what that one reaches. A test marked with a
capability that Windows lacks, and a conftest fixture no test of the folder is given, are no sites.
"""
