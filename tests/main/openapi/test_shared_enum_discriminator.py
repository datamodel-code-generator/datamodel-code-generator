"""Enum-member discriminator tags stay the selected member when another union shares the variant.

With ``--use-enum-values-in-discriminator``, a later union must not replace ``Literal[PetType.cat]`` with the schema
path, and an unmapped union must not invent a member such as ``Kind.Cat``.
"""

from __future__ import annotations

import sys
import warnings
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from tests.main.conftest import _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

_GENERATE = (
    "--use-enum-values-in-discriminator",
    "--output-model-type",
    "pydantic_v2.BaseModel",
    "--formatters",
    "builtin",
    "--disable-timestamp",
)
_OPENAPI = "openapi: '3.0.0'\ncomponents:\n  schemas:\n"
_PETS = """\
    PetType:
      type: string
      enum:
        - cat
        - dog
    Cat:
      type: object
      required:
        - pet_type
      properties:
        pet_type:
          $ref: '#/components/schemas/PetType'
        meow:
          type: string
    Dog:
      type: object
      required:
        - pet_type
      properties:
        pet_type:
          $ref: '#/components/schemas/PetType'
        bark:
          type: string
    Pet:
      oneOf:
        - $ref: '#/components/schemas/Cat'
        - $ref: '#/components/schemas/Dog'
      discriminator:
        propertyName: pet_type
        mapping:
          cat: '#/components/schemas/Cat'
          dog: '#/components/schemas/Dog'
"""
_OWNER = """\
    Owner:
      type: object
      properties:
        pet:
          nullable: true
          oneOf:
            - $ref: '#/components/schemas/Cat'
            - $ref: '#/components/schemas/Dog'
          discriminator:
            propertyName: pet_type
            mapping:
              cat: '#/components/schemas/Cat'
              dog: '#/components/schemas/Dog'
"""
_ANIMALS = """\
    Kind:
      type: string
      enum:
        - cat
        - dog-breed
    Animal:
      type: object
      required:
        - kind
      properties:
        kind:
          $ref: '#/components/schemas/Kind'
    Cat:
      allOf:
        - $ref: '#/components/schemas/Animal'
      properties:
        meow:
          type: string
    Dog:
      allOf:
        - $ref: '#/components/schemas/Animal'
      properties:
        bark:
          type: string
"""
_MAPPED = """\
    Mapped:
      oneOf:
        - $ref: '#/components/schemas/Cat'
        - $ref: '#/components/schemas/Dog'
      discriminator:
        propertyName: kind
        mapping:
          cat: '#/components/schemas/Cat'
          dog-breed: '#/components/schemas/Dog'
"""
_UNMAPPED = """\
    Unmapped:
      oneOf:
        - $ref: '#/components/schemas/Cat'
        - $ref: '#/components/schemas/Dog'
      discriminator:
        propertyName: kind
"""


def _generate(tmp_path: Path, output_file: Path, name: str, schema: str, *, ignore_tag_conflict: bool = False) -> None:
    """Generate one module from a schema written in the test's temporary directory."""
    source = tmp_path / f"{name}.yaml"
    source.write_text(_OPENAPI + schema, encoding="utf-8")
    with warnings.catch_warnings():
        if ignore_tag_conflict:
            # The unmapped union's schema-path tag differs from the mapping. Generation keeps that diagnostic.
            warnings.filterwarnings(
                "ignore",
                message=r".*discriminated unions that map 'kind' to different tags",
                category=UserWarning,
            )
        run_main_and_assert(
            input_path=source,
            output_path=output_file,
            input_file_type="openapi",
            extra_args=_GENERATE,
        )


def _parsed_name(model: Any, payload: dict[str, object]) -> str:
    """Return the variant a union selects, ``None`` for an empty value, or ``rejected``."""
    try:
        value = model.model_validate(payload)
    except ValidationError:
        return "rejected"
    if (root := getattr(value, "root", None)) is not None:
        return type(root).__name__
    pet = getattr(value, "pet", None)
    return "None" if pet is None else type(pet).__name__


@pytest.mark.allow_direct_assert("Literal[Enum.member] accepts that member and rejects the raw string at runtime")
def test_single(output_file: Path, tmp_path: Path) -> None:
    """One union over Cat and Dog emits each variant's enum member and accepts that member."""
    _generate(tmp_path, output_file, "single", _PETS)
    with _generated_model(output_file, "shared_enum_single", "Pet") as pet:
        pet_type = sys.modules[pet.__module__].PetType
        assert _parsed_name(pet, {"pet_type": pet_type.cat, "meow": "m"}) == "Cat"
        assert _parsed_name(pet, {"pet_type": pet_type.dog, "bark": "w"}) == "Dog"
        assert _parsed_name(pet, {"pet_type": "cat", "meow": "m"}) == "rejected"
        assert _parsed_name(pet, {"pet_type": "bird"}) == "rejected"


@pytest.mark.allow_direct_assert("Literal[Enum.member] accepts that member and rejects the raw string at runtime")
def test_repeated(output_file: Path, tmp_path: Path) -> None:
    """A second union over the same Cat and Dog keeps their enum members, including a nullable field."""
    _generate(tmp_path, output_file, "repeated", _PETS + _OWNER)
    with _generated_model(output_file, "shared_enum_repeated", "Pet") as pet:
        module = sys.modules[pet.__module__]
        pet_type = module.PetType
        owner = module.Owner
        cat = {"pet_type": pet_type.cat, "meow": "m"}
        dog = {"pet_type": pet_type.dog, "bark": "w"}
        assert _parsed_name(pet, cat) == "Cat"
        assert _parsed_name(pet, dog) == "Dog"
        assert _parsed_name(pet, {"pet_type": "cat", "meow": "m"}) == "rejected"
        assert _parsed_name(pet, {"pet_type": "bird"}) == "rejected"
        assert _parsed_name(owner, {"pet": cat}) == "Cat"
        assert _parsed_name(owner, {"pet": dog}) == "Dog"
        assert _parsed_name(owner, {"pet": {"pet_type": "bird"}}) == "rejected"
        assert _parsed_name(owner, {"pet": None}) == "None"
        assert _parsed_name(owner, {}) == "None"


def _kind_results(output_file: Path, module_name: str) -> tuple[str, ...]:
    """Return what each union does with ``Kind.cat``, ``Kind.dog_breed``, the raw value, and an unknown tag."""
    with _generated_model(output_file, module_name, "Mapped") as mapped:
        module = sys.modules[mapped.__module__]
        kind = module.Kind
        payloads = (
            {"kind": kind.cat, "meow": "m"},
            {"kind": kind.dog_breed, "bark": "w"},
            {"kind": "dog-breed", "bark": "w"},
            {"kind": "bird"},
        )
        return tuple(_parsed_name(union, payload) for union in (mapped, module.Unmapped) for payload in payloads)


_KIND_ACCEPTED = ("Cat", "Dog", "rejected", "rejected", "Cat", "Dog", "rejected", "rejected")


@pytest.mark.allow_direct_assert("Literal[Kind.dog_breed] accepts that member and rejects the raw string at runtime")
def test_mapped_first(output_file: Path, tmp_path: Path) -> None:
    """An explicit mapping visited before an unmapped union keeps the sanitized enum members."""
    _generate(tmp_path, output_file, "mapped_first", _ANIMALS + _MAPPED + _UNMAPPED, ignore_tag_conflict=True)
    assert _kind_results(output_file, "shared_enum_mapped_first") == _KIND_ACCEPTED


@pytest.mark.allow_direct_assert("Literal[Kind.dog_breed] accepts that member and rejects the raw string at runtime")
def test_unmapped_first(output_file: Path, tmp_path: Path) -> None:
    """An unmapped union visited first does not invent a member the later mapping then has to repair."""
    _generate(tmp_path, output_file, "unmapped_first", _ANIMALS + _UNMAPPED + _MAPPED, ignore_tag_conflict=True)
    assert _kind_results(output_file, "shared_enum_unmapped_first") == _KIND_ACCEPTED
