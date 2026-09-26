# S08 implementation record

Status: in progress. S08-1 (standard dataclass codecs) is implemented and verified locally, stacked on a fix of the shared union encoding that it builds on. S08-2 (TypedDict) and S08-3 (msgspec.Struct and the five-backend comparisons) follow. Nothing in this stack has been merged.

## Baseline and review boundaries

The authoritative contracts are DECISIONS-MODEL-CODECS §§5–8 (presence, backend contracts, aliases and unknown fields, direction, enums, unions and containers) and §14 (MC02, MC05–MC07, MC09, MC13, MC14 and MC16 for this backend), and DECISIONS-TYPING for the codec surfaces, read from the plan checkout at `0922e061643eb463fee5406623e9e939be4a3ba2` ([PR #4098](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4098)): `DECISIONS-MODEL-CODECS.md` `e985905c8cd40a5d1c1f87296f76f523e336c60f8df62c04019366cae5d33bb2`, `DECISIONS-TYPING.md` `5bc2422531438e3538dbf4a956ec5856782295618eca7d93a92629f35dd4c984`, `ACCEPTANCE.md` `5a6725c6b8b66662f40b1fde143491b439c3c7c8badba0af55f02a05122fd1e1`, `PR-STACKS.md` `08f51c2578164b9992452806ea6bc23cd1ad2f38d5e7961fff282da668bc98b2`. Main baseline: `34f144ff3569ef7525c1718a5d818841ed2cc43a`, with S01–S07 merged. The stack sits on the unmerged review fixes (#4173–#4178, native stack #4179), because it changes the runtime copies and runtime counts their expected files pin.

PR-STACKS splits S08 into the standard dataclass, TypedDict, and msgspec.Struct with the five-backend comparisons. The client target that renders these codecs into packages is S09, and FastAPI keeps rejecting the three backends (`E_FASTAPI_BACKEND_UNSUPPORTED`), so every S08 surface stays in the private runtime and planner, exercised through really generated models, the planner, and rendered model bindings.

The isolated checkouts are `/private/tmp/dcg-s08a` (the union fix) and `/private/tmp/dcg-s08`. No subagents were used; measurement scripts and raw output stay in the session scratchpad.

## Deviations from the plan

- A shared fix comes first: a union of a model and its subclass encoded a subclass value as the first member it was an instance of, so a Pydantic `Cat` sent through `Animal | Cat` lost its own fields while the smaller value still validated. Both codecs now prefer the member whose type is exactly the value's type, then fall back to the first member it is an instance of.
- Wire formats bound the native domains of leaves. The common validator asserts the standard formats, so a `date-time` or `time` without an offset, or a `date` that does not exist, never reaches a non-union leaf; the converters still parse only RFC 3339 text, since Python 3.11 and later accept wider ISO 8601 forms that 3.10 does not. In a union, a leaf member is chosen when the value lies in its wire domain: its type, its format, or its enum or literal values. A value inside the domain that the native type cannot represent fails without trying another member, as §8 requires: a duration with years or months, a network with host bits, an overflowing float or duration, or text that is not a decimal.
- Non-finite and non-decimal text such as `NaN`, `1_000` or ` 1.5 ` is not a decimal string, although `decimal.Decimal` accepts it.
- A set of dataclasses that are not frozen cannot be built, since they are unhashable; decoding a non-empty array of them fails with `native.set_item_not_hashable`, an empty one succeeds.

## S08-1: standard dataclass codecs

Runtime (`_runtime/model_codecs`):

- `codec.py` is the shared frame of the builtin codecs, taken out of `pydantic_v2.py`: context and direction checks, validated `decode`/`from_wire`/`snapshot`/`encode`, envelope encoding, schema-matched union members, and the pointer, key and scalar helpers. `PydanticModelCodec` keeps its adapter, key and extras logic on top of it; its outputs are unchanged, and the presence checks read the key views instead of copying them into sets.
- `structural.py` adds `StructuralModelCodec` for `dataclass_structural`. At construction it walks the use's type graph together with the final annotations (`typing.get_type_hints`, through `TypeAliasType` values and the optional fallback `X | None`) and builds one converter per node; a model must be a dataclass whose `dataclasses.fields` agree with the binding on constructibility and required arguments, and every required constructor argument must be bound. Any disagreement, including an unresolved annotation, raises `CodecConfigurationError` naming the model or field.
- Decoding validates the wire value first, then calls each final model's ordinary constructor with keyword arguments of the present members only, so defaults, `default_factory`, `frozen`, `slots`, `kw_only` and `__post_init__` behave natively; a constructor failure is `native.constructor`. Unknown members are kept as extras. A member of a field without `init` is `FIELD_NOT_CONSTRUCTIBLE`, a required argument the direction excludes is `DIRECTIONAL_REQUIRED`, and one the value omits is `MODEL_PROJECTION_GAP`; the planner makes such uses envelopes, and a native use that meets one fails as an unplanned gap. Leaves convert strictly by kind, with bool never an integer and enum and literal members matched with their JSON type; decimals keep their lexemes, and the planner's `decimal_string` representation reads and writes decimal text.
- Encoding reads each field with `getattr` (never `dataclasses.asdict`) and writes a frozen wire value directly; a field without `init` that was never set is left out. Presence follows §5: `None` from a synthesized fallback default is omitted, nullable `None` is sent as `null`, and explicit presence or `from_wire` preserve omission. Sets are ordered by their JSON bytes, enum keys by their values, and dates, times and durations are written as RFC 3339 and ISO 8601 text.
- Error locations are built only when an issue or extra is recorded: the walk passes a linked route of wire and native tokens instead of formatting a pointer for every member.

Planner (`_openapi_codec_plan.py`) and renderer:

- The `dataclasses.dataclass` backend plans `dataclass_structural` uses of `dataclass` symbols, whose models are `native_kind="dataclass"`. A field is required when its constructor has no default, and constructible unless `init` is false; required fields the schema leaves optional mark the use as an envelope, as do fields without `init`.
- `MC_ADAPTER_REQUIRED` names a leaf, map key or recursive alias that no structural converter reads: `bytes`, custom types such as `deque[int]`, keys that are not strings or enums (after resolving aliases), and recursive type aliases.
- `FieldBinding.constructible` records whether the constructor takes the field. Rendered bindings construct `StructuralModelCodec` for the backend, so FastAPI packages copy `codec.py` but not `structural.py`.

Tests:

- `test_structural_codecs.py` runs real generated dataclasses through the codec: the Pydantic pets and shapes sources and a structural source with every standard leaf (RFC 3339 and ISO 8601 forms, decimals of both representations, UUIDs, addresses, networks, paths, enums including one whose `True` member is an alias of `1`, literals, sets, tuples and `Any`), unions of leaves chosen by wire domain, fields without `init` or with factories and `kw_only`, `frozen`/`slots`/`kw_only` dataclasses, `strip_default_none`, generic containers, type aliases, untyped object and array schemas, and unsupported types with their diagnostics and startup refusals. The startup report checks every refusal against hand-written dataclasses, a `__post_init__` failure, an unplanned native gap, and relaxed schemas that let values reach the union, leaf and key converters.
- The rendered bindings of an adapters fixture construct and run the structural codec from a generated package.
- Typing: `codecs.py` gains structural positive cases and `codecs_negative.py` three negative lines (38–40); the negative Pydantic lines are now 24–29, after dropping two `encode` calls that were no longer errors once `encode` accepted any value at the server boundary. Strict mypy 2.3.1, strict Pyright 1.1.414 and ty 0.0.84 report nothing for the positive samples and exactly one error on each negative line; the runtime package and a rendered structural package pass all three.
- Coverage of the runtime codecs, the planner and the renderer is complete in the model codec suite, apart from `wire.checked_wire`, which the FastAPI suite covers.

Benchmarks (100 pets of the S04 benchmark source, best of 7, µs): the Pydantic BaseModel codec decodes in 3938 (3941 before the refactor) and encodes in 3796 (3777); the Pydantic dataclass codec decodes in 3904 (3966) and encodes in 3763 (3738), all within noise. The structural codec decodes in 3852, encodes in 3145 and encodes one pet in 31.7; about 2300 of each 100-pet call is the common wire validation. Lazy locations cut the structural walk from 561 to 433 per decode, and writing frozen values directly cut encoding from 3388 to 3145.

## Next action

Publish the union fix and S08-1 as the first two PRs of the S08 native stack, then implement S08-2 (TypedDict).
