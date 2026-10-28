"""Application code that builds on the generated client and that regeneration never touches."""

from __future__ import annotations

from pets import Client
from pets.resources.pets import PetsResource
from pets_models import Pet


class AuditedPets(PetsResource):
    """The pets operations the application reads through."""

    def first_pet(self) -> Pet | None:
        """Return the first pet of a one-pet page."""
        pets = self.list_pets(limit=1)
        return pets.root[0] if pets.root else None


class AppClient(Client):
    """The application's client, with its own pets resource."""

    @property
    def audited_pets(self) -> AuditedPets:
        """Return the pets operations the application reads through."""
        return AuditedPets(self._core)

    def retire(self, pet_id: int) -> None:
        """Archive one pet."""
        self.archive.archive_pet(petId=pet_id)
