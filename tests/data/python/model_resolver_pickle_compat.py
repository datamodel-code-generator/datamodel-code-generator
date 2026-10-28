"""Exercise ModelResolver pickles across the public resolver-state rename."""

from __future__ import annotations

import argparse
import base64
import pickle
from pathlib import Path
from typing import Any

import datamodel_code_generator.reference as reference
from datamodel_code_generator.reference import ModelType, Reference


class LegacyModelResolver:
    """Minimal earlier-release consumer for a current ModelResolver pickle."""

    def default_class_name_generator(self, name: str) -> str:
        """Resolve class names through the earlier public mapping."""
        return self.field_name_resolvers[ModelType.CLASS].get_valid_name(name, upper_camel=True)

    def get_valid_field_name(
        self,
        name: str,
        excludes: set[str] | None = None,
        model_type: ModelType = ModelType.PYDANTIC,
    ) -> str:
        """Resolve field names through the earlier public mapping."""
        return self.field_name_resolvers[model_type].get_valid_name(name, excludes)

    def add_reference(self, path: str, name: str) -> None:
        """Simulate the earlier resolver's set-backed reference cache update."""
        self.references[path] = Reference(path=path, original_name=name, name=name)
        self._reference_names_cache.add(name)

    def delete_reference(self, path: str) -> None:
        """Simulate the earlier resolver's set-backed reference cache removal."""
        reference = self.references.pop(path)
        self._reference_names_cache.discard(reference.name)


class LegacySlottedModelResolver(LegacyModelResolver):
    """Earlier public state layout with a slot collected by the default pickler."""

    __slots__ = ("slot_marker",)


def _report(direction: str, resolver: Any) -> None:
    mapping = resolver.field_name_resolvers
    state_layout = "private" if "_field_name_resolvers" in vars(resolver) else "public"
    print(direction)
    print(f"state layout: {state_layout}")
    print(f"mapping: {' '.join(model_type.name for model_type in mapping)}")
    print(f"resolver types: {' '.join(type(item).__name__ for item in mapping.values())}")
    print(f"class field: {resolver.get_valid_field_name('3name', model_type=ModelType.CLASS)}")
    print(f"pydantic field: {resolver.get_valid_field_name('schema')}")
    if isinstance(resolver, LegacyModelResolver):
        resolver.add_reference("pickle-compat#", "PickleCompat")
        resolver.delete_reference("pickle-compat#")
        print(f"cache mutation: {'PickleCompat' not in resolver._reference_names_cache}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "direction",
        choices=(
            "main-to-current",
            "main-slotted-to-current",
            "current-to-main",
            "current-slotted-to-main",
        ),
    )
    parser.add_argument("pickle_path", type=Path)
    args = parser.parse_args()

    if args.direction in {"main-to-current", "main-slotted-to-current"}:
        payload = base64.b64decode(args.pickle_path.read_bytes())
        resolver = pickle.loads(payload)
        direction = "main -> current" if args.direction == "main-to-current" else "main slotted -> current"
        _report(direction, resolver)
        if args.direction == "main-slotted-to-current":
            print(f"slot marker: {resolver.slot_marker}")
        return

    reference.ModelResolver = LegacyModelResolver
    if args.direction == "current-slotted-to-main":
        from tests.data.python import model_resolver_pickle_types

        model_resolver_pickle_types.SlottedModelResolver = LegacySlottedModelResolver
        resolver = pickle.loads(args.pickle_path.read_bytes())
        _report("current slotted -> main", resolver)
        print(f"slot marker: {resolver.slot_marker}")
        return
    _report("current -> main", pickle.loads(args.pickle_path.read_bytes()))


if __name__ == "__main__":
    main()
