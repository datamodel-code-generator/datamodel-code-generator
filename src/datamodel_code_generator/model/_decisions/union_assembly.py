"""Union assembly: which members a union holds, in which order, and whether it writes None itself.

Every input is an immutable fact, so the functions read nothing else. ``assemble_union`` decides a union when it is
built or rewritten: each builder passes the row of ``UnionRow`` it follows and a ``MemberFacts`` per member. The
adapters that apply the plan are listed under H10 in ``scripts/decision_homes.toml``. ``merge_members`` decides which
written members a union keeps and whether it takes over their None, by rendered text or by projected type.
``join_union`` writes the members, for type hints and
for the ``MISSING`` sentinel of Pydantic fields, which it writes after the hint as rendered, so a union in the hint
stays nested (``legacy-missing-appended``: ``Union[Optional[Union[str, int]], MISSING]``, where msgspec writes
``Union[str, int, None, UnsetType]``).

The rows named ``legacy-*`` keep today's output where builders, rewriters and renderers disagree on members, order or
the None; the fix of each deletes its row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Generic, Literal, TypeAlias, TypeVar, final

from datamodel_code_generator._records import record

if TYPE_CHECKING:
    from collections.abc import Hashable, Iterable, Sequence

__all__ = [
    "CONSTRUCTED",
    "FLATTENED",
    "MEMBER",
    "MemberFacts",
    "MemberRole",
    "MergedMembers",
    "UnionPlan",
    "UnionPolicy",
    "UnionRow",
    "UnionRule",
    "WrittenMember",
    "assemble_union",
    "join_union",
    "merge_members",
]

_T = TypeVar("_T")

UnionRow: TypeAlias = Literal[
    "declared",
    "constructed",
    "ordered",
    "flattened",
    "legacy-non-array-first",
    "legacy-replacement-last",
    "legacy-graphql-template",
]
"""How a builder or rewriter assembles its union.

* ``declared``: the members in the order the builder parsed them; ``UnionPolicy.nullable`` gives the union's own None
  (the jsonschema and OpenAPI builders).
* ``constructed``: what every ``DataType`` does with the members it is built with: a member that admits any value and
  null gives its None to the union and is dropped when another member does not admit any value, so ``str | Any``
  where the ``Any`` admits null renders as ``str | None`` (``optional-any-to-null``).
* ``ordered``: the members keep their order and render as written; None and the ``UNSET`` sentinel are members, the
  sentinel last, and a union of one member is that member (msgspec field hints).
* ``flattened``: render preparation rewrites the union around a quoted container: a member that only groups its
  members gives them its place, and the None of every member moves to the union, so ruff finds no nested or repeated
  member to rewrite into a ``|`` chain, which fails at run time once a member is a string.
* ``legacy-non-array-first``: the branches beside ``array`` of a type array, parsed after the array, go before it
  (``parser/jsonschema.py:JsonSchemaParser.parse_array_fields``), where the other type arrays keep the order of the
  schema.
* ``legacy-replacement-last``: a member that L5 replaces (root-model collapse) leaves its place and its replacement
  goes last, so ``anyOf [$ref S(string), integer]`` is ``S | int`` but ``int | str`` with ``--collapse-root-models``.
* ``legacy-graphql-template``: the members of a GraphQL union, in declaration order, which the union template writes as
  ``Union[...]`` whatever ``--use-union-operator`` says.
"""

MemberRole: TypeAlias = Literal["member", "non-array", "replaced", "replacement", "null", "sentinel", "grouping"]
"""What a member is to the row that orders it.

``non-array`` marks a type-array branch beside ``array`` (``legacy-non-array-first``), ``replaced`` and
``replacement`` the member L5 replaces and the one replacing it (``legacy-replacement-last``), ``null`` and
``sentinel`` the None and ``UNSET`` members of ``ordered`` unions, ``grouping`` a member that only groups its members
and writes nothing of its own (``flattened``).
"""

UnionRule: TypeAlias = Literal[
    "declared",
    "ordered",
    "flattened",
    "optional-any-to-null",
    "legacy-non-array-first",
    "legacy-replacement-last",
    "legacy-graphql-template",
]
"""The row of ``UnionRow`` that decided a ``UnionPlan``, or ``optional-any-to-null`` when the constructed row dropped
members."""


@final
@record
class MemberFacts:
    """The facts ``assemble_union`` reads about one member: its role, whether it admits any value, and its None."""

    role: MemberRole = "member"
    is_any: bool = False
    null: bool = False


@final
@record
class UnionPolicy:
    """The row a union follows, and whether the builder found that the union admits null."""

    row: UnionRow = "declared"
    nullable: bool = False


@final
@record
class UnionPlan:
    """The members a union holds, as indexes into the members passed in, and how it writes them.

    ``nullable`` is the union's own None. ``preserve_order`` renders the members in order, with None in place, and
    ``one_member_is_type`` makes a union of one member that member itself. ``spread`` names the members whose own
    members the union holds in their place, and ``null_moved`` the members whose None the union writes instead.
    """

    rule: UnionRule
    members: tuple[int, ...]
    nullable: bool = False
    preserve_order: bool = False
    one_member_is_type: bool = False
    spread: tuple[int, ...] = ()
    null_moved: tuple[int, ...] = ()


MEMBER = MemberFacts()
"""A plain member."""

CONSTRUCTED = UnionPolicy("constructed")
"""The row every ``DataType`` applies to the members it is built with."""

FLATTENED = UnionPolicy("flattened")
"""The row of render preparation's rewrite of the union around a quoted container."""


def _indexes(members: Sequence[MemberFacts], *roles: MemberRole) -> tuple[int, ...]:
    return tuple(index for index, member in enumerate(members) if member.role in roles)


def assemble_union(members: Sequence[MemberFacts], policy: UnionPolicy, /) -> UnionPlan:  # noqa: PLR0911
    """Return the plan of a union of ``members`` built or rewritten under ``policy`` (``UnionRow`` names the rows)."""
    match policy.row:
        case "declared" | "legacy-graphql-template":
            return UnionPlan(policy.row, tuple(range(len(members))), nullable=policy.nullable)
        case "constructed":
            optional_any = tuple(member.is_any and member.null for member in members)
            if any(optional_any) and not all(member.is_any for member in members):
                kept = tuple(index for index, dropped in enumerate(optional_any) if not dropped)
                return UnionPlan("optional-any-to-null", kept, nullable=True)
            return UnionPlan("declared", tuple(range(len(members))), nullable=policy.nullable)
        case "ordered":
            order = (
                *_indexes(members, "member", "non-array"),
                *_indexes(members, "null"),
                *_indexes(members, "sentinel"),
            )
            return UnionPlan("ordered", order, nullable=policy.nullable, preserve_order=True, one_member_is_type=True)
        case "flattened":
            null_moved = tuple(index for index, member in enumerate(members) if member.null)
            return UnionPlan(
                "flattened",
                tuple(range(len(members))),
                nullable=policy.nullable or bool(null_moved),
                spread=_indexes(members, "grouping"),
                null_moved=null_moved,
            )
        case "legacy-non-array-first":
            order = (*_indexes(members, "non-array"), *_indexes(members, "member", "null", "sentinel"))
            return UnionPlan(policy.row, order, nullable=policy.nullable)
    order = (*_indexes(members, "member", "non-array", "null", "sentinel"), *_indexes(members, "replacement"))
    return UnionPlan("legacy-replacement-last", order, nullable=policy.nullable)


@final
@record
class WrittenMember(Generic[_T]):
    """A member as a union writes it: the key it is told apart by, what the union keeps of it, and its None.

    ``null`` tells that the member is None itself, ``strips_null`` that ``kept`` is the member without the None it
    holds; both give the None to the union.
    """

    key: Hashable
    kept: _T
    null: bool = False
    strips_null: bool = False


@final
@record
class MergedMembers(Generic[_T]):
    """What a union keeps of its written members, and whether it takes over their None."""

    kept: tuple[_T, ...]
    takes_null: bool


def merge_members(members: Iterable[WrittenMember[_T]], /) -> MergedMembers[_T]:
    """Return the members a union writes: each once, without the members that are None, whose None it takes over.

    A member is a repeat when its key equals what the union kept of an earlier member (``legacy-dedup-before-strip``:
    the rendered text of a member holding None is compared before its None is stripped, so ``str`` then
    ``Optional[str]`` keeps both).
    """
    kept: list[_T] = []
    takes_null = False
    for member in members:
        if member.key in kept:
            continue
        if member.null:
            takes_null = True
            continue
        takes_null = takes_null or member.strips_null
        kept.append(member.kept)
    return MergedMembers(tuple(kept), takes_null)


def join_union(members: Sequence[str], /, *, union_operator: bool) -> str:
    """Write a union of member hints, with ``|`` or as ``Union[...]``."""
    return " | ".join(members) if union_operator else f"Union[{', '.join(members)}]"
