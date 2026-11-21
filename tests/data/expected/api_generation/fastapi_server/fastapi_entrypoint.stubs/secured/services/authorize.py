"""Authorize the secured operations of secured; secured.main imports authorize from this module.

datamodel-code-generator wrote this module once, with a stub, and never overwrites it.
"""

from __future__ import annotations

from secured import Credentials, RequirementSets


def authorize(requirement_sets: RequirementSets, credentials: Credentials) -> object:
    """Return the principal of the credentials, or raise HTTPException to refuse the request.

    A secured operation passes its requirement sets whose every scheme presented a credential, in declaration order,
    and the credentials by scheme name; its service method receives the principal as its principal argument.
    """
    raise NotImplementedError
