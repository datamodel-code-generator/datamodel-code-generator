"""Write a msgspec union member naming a type alias of a set as its list where the union then holds one array (#6327).

msgspec decodes a union that holds at most one array-like type (``list``, ``tuple``, ``set``). With
--use-unique-items-as-set, an anyOf or oneOf member written as a type alias of a uniqueItems set, ``V: TypeAlias =
set[int]``, sits beside the list another member writes, ``V | list[int]``, and the union fails on every decode. The
decision writes such a member, at that union only, as the list of the alias's set where that list is the type the
other array-like members write: the union then writes that type once (``list[int]``) and decodes. The member leaves
the node that holds it beside a member that stays, which the union writes, so a nested union writes the list once too.
An alias that writes anything beside its set (a ``Meta()`` argument), or a union whose array-like members stay apart,
keeps the member, as msgspec refuses the union either way; the alias keeps its set wherever it is written. Under
--use-unique-items-as-set-by-value the decision on the final types reads the members picked here as the list the union
writes, so a default the union takes stays the list (``listed_members``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, cast

from datamodel_code_generator._container_kinds import ARRAYS, CONTAINERS
from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator.parser._phases import DecisionPass, DecisionWriter

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence

    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.model._views import DataTypeView, FieldView, ModelView
    from datamodel_code_generator.parser._phases import FinalFacts

__all__ = ["LIST_SET_ALIAS_MEMBERS", "ListedMember", "SetAliasMembersWriter", "listed_members"]

ListedMember: TypeAlias = "tuple[DataTypeView, DataTypeView | None]"
"""A union member the decision picks, with the set of its alias it is written as the list of, or None where it leaves
the node that holds it, beside a member that stays."""


class SetAliasMembersWriter(DecisionWriter, Protocol):
    """The write of the decision: write members of a union as the lists of the sets their type aliases write."""

    def write_members_as_lists(self, model: ModelView, field: FieldView, members: Sequence[ListedMember]) -> None:
        """Write each node of ``members``, in the type of ``field`` of ``model``, as its set's list, or leave it out.

        The field gets a copy of its type tree with the lists; the nodes and the sets stay as other fields see them.
        """


def _alias_set(
    model: ModelView, meta_keys: Collection[str], is_set: Callable[[DataTypeView], bool]
) -> DataTypeView | None:
    """Return the set a type alias writes as its value alone (``is_set``), or None.

    The alias writes nothing beside it where its field holds none of ``meta_keys``, the metadata and constraints
    msgspec writes as ``Meta()`` arguments, and neither the set nor a node that holds it alone admits None.
    """
    if model.form != "alias" or len(model.fields) != 1:
        return None
    field = model.fields[0]
    declared = {} if (constraints := cast("Any", field.constraints)) is None else constraints.model_dump()
    if any(value is not None and key in meta_keys for key, value in (*field.extras.items(), *declared.items())):
        return None
    value = field.data_type
    while not value.admits_none and _joins_union(value) and len(value.data_types) == 1:
        value = value.data_types[0]
    return value if is_set(value) and not value.admits_none else None


def _written(data_type: DataTypeView, *, as_list: bool = False) -> tuple[object, ...]:
    """Return the facts a type is written from, its nested types' too; ``as_list`` reads a set as the list of its items.

    Two types with the same facts write the same hint.
    """
    return (
        data_type.type,
        None if (reference := data_type.reference) is None else model_key_of(reference),
        data_type.is_dict,
        data_type.is_list or as_list,
        data_type.is_set and not as_list,
        data_type.is_frozen_set,
        data_type.is_mapping,
        data_type.is_sequence,
        data_type.is_tuple,
        data_type.tuple_item_count,
        data_type.is_custom_type,
        data_type.is_func,
        data_type.kwargs,
        data_type.python_type,
        (*data_type.literals,),
        (*data_type.enum_member_literals,),
        data_type.strict,
        data_type.discriminator,
        data_type.admits_none,
        tuple(_written(nested) for nested in data_type.data_types),
        None if (key := data_type.dict_key) is None else _written(key),
    )


def _joins_union(data_type: DataTypeView) -> bool:
    """Return whether a node writes no text of its own, so its members are members of the union that holds it."""
    return data_type.reference is None and data_type not in CONTAINERS and bool(data_type.data_types)


def _members(holder: DataTypeView) -> Iterator[tuple[DataTypeView, DataTypeView]]:
    """Yield the members of the union of ``holder``'s types, those of the unions it joins too, each with its holder."""
    for member in holder.data_types:
        if _joins_union(member):
            yield from _members(member)
        else:
            yield holder, member


def _unions(data_type: DataTypeView, *, joined: bool = False) -> Iterator[DataTypeView]:
    """Yield the nodes of a type tree whose types are the members of a union, but those a union above joins."""
    holds = not data_type.is_tuple and len(data_type.data_types) > 1
    if holds and not joined:
        yield data_type
    for nested in data_type.data_types:
        yield from _unions(nested, joined=(holds or joined) and _joins_union(nested))


class _Aliases:
    """The type aliases of the live models, by key, as the members of a union name them."""

    __slots__ = ("live", "sets")

    def __init__(
        self, live_models: Iterable[ModelView], is_set: Callable[[DataTypeView], bool], meta_keys: Collection[str]
    ) -> None:
        self.live: Mapping[ModelKey, ModelView] = {model_key_of(model.reference): model for model in live_models}
        self.sets = {key: found for key, model in self.live.items() if (found := _alias_set(model, meta_keys, is_set))}

    def holds_array(self, key: ModelKey) -> bool:
        """Return whether the value of the alias ``key`` may write an array-like member of a union that names it.

        A member of its value that names another alias may, as it is read no further.
        """
        if (model := self.live.get(key)) is None or model.form != "alias" or not model.fields:
            return False
        value = model.fields[0].data_type
        members = (member for _, member in _members(value)) if _joins_union(value) else (value,)
        return any(map(self._array, members))

    def _array(self, data_type: DataTypeView) -> bool:
        """Return whether a member of an alias's value is array-like or names an alias."""
        if (reference := data_type.reference) is None:
            return data_type in ARRAYS
        return (model := self.live.get(model_key_of(reference))) is not None and model.form == "alias"

    def listed(self, holder: DataTypeView) -> list[ListedMember]:
        """Return the members of the union of ``holder`` that name an alias of a set the union writes as a list.

        They are picked where each such list is the type every other array-like member writes, one of which writes
        it itself: the union then holds that one array-like type. Otherwise the union keeps its members.
        A member leaves the node that holds it beside a member that stays, as the union writes its list already;
        the first of the members of a node that would hold none is written as its alias's list.
        """
        picked: list[tuple[DataTypeView, DataTypeView, DataTypeView]] = []
        forms: list[tuple[object, ...]] = []
        written = False
        for parent, member in _members(holder):
            if (reference := member.reference) is None:
                if member in ARRAYS:
                    forms.append(_written(member))
                    written = True
            elif (alias_set := self.sets.get(key := model_key_of(reference))) is not None and not member.admits_none:
                picked.append((parent, member, alias_set))
                forms.append(_written(alias_set, as_list=True))
            elif self.holds_array(key):
                return []
        if not (picked and written and all(form == forms[0] for form in forms)):
            return []
        listed: list[ListedMember] = []
        for parent in {id(parent): parent for parent, _, _ in picked}.values():
            members = [(member, alias_set) for holding, member, alias_set in picked if holding is parent]
            stays = len(parent.data_types) > len(members)
            listed.extend(
                (member, None if stays or index else alias_set) for index, (member, alias_set) in enumerate(members)
            )
        return listed


def listed_members(
    live_models: Sequence[ModelView], is_set: Callable[[DataTypeView], bool], meta_keys: Collection[str]
) -> list[tuple[ModelView, FieldView, list[ListedMember]]]:
    """Return, for each field of ``live_models``, the union members ``_Aliases.listed`` picks, where it picks any.

    ``is_set`` tells whether the node of an alias's value is a set: the decision on the final types
    (``parser/_unique_sets.py``) reads it from its plans, to write the defaults as the listed unions write them.
    ``meta_keys`` are the keys of a field the output writes as ``Meta()`` arguments (``DecisionOptions``).
    """
    aliases = _Aliases(live_models, is_set, meta_keys)
    if not aliases.sets:
        return []
    return [
        (model, field, listed)
        for model in live_models
        for field in model.fields
        if (listed := [member for holder in _unions(field.data_type) for member in aliases.listed(holder)])
    ]


def _list_set_alias_members(facts: FinalFacts, writer: SetAliasMembersWriter) -> None:
    """Write the members of the unions of every live model's fields that ``_Aliases.listed`` picks.

    Every pick is read before the first write, so a union reads the aliases' sets as their models write them.
    """
    for model, field, listed in listed_members(
        facts.live_models, lambda node: node.is_set, facts.options.meta_field_keys
    ):
        writer.write_members_as_lists(model, field, listed)


LIST_SET_ALIAS_MEMBERS = DecisionPass(
    {
        "name": "list_set_alias_members",
        "issue": "#6327",
    },
    SetAliasMembersWriter,
    _list_set_alias_members,
    when=lambda parser: (
        parser.use_unique_items_as_set and not parser._output_model_context.decodes_value_unions  # noqa: SLF001
    ),
)
