# S03 implementation record

Status: implementing; no implementation PRs published or merged.

Repository: datamodel-code-generator/datamodel-code-generator. Fixed main baseline: `149d933d927ca12f2e0e45fc18f8a4cf772046d7`. S01 and S02 are merged. The planning PR #4098 remains open at `8e6f0d4a8a64b71e5a049ad8d108507b2fe164ca`; contracts are read from its separate checkout. The original worktree's untracked `1.tmp` is preserved.

## Scope and review boundaries

Implement S03 only, in four native-stack PRs as assigned by PR-STACKS:

1. Operations/type uses and actual resolver arguments/results.
2. Reuse, deduplication, collapse and actual type replacement.
3. Field provenance, five-backend facts and structured type projection.
4. Final symbols/artifacts, accepted-attempt freezing and source-lease disposal.

No new server/client public entry points, dependencies or default-path observers are authorized by this stack. All B requirements and G01–10, dry server/client consumers, strict typing and controlled performance comparisons remain completion gates.

## Integration changes since the pinned plan baseline

Current main adds exactly the six merged S01/S02 implementation PRs since `4f96e22e`: observation baselines, parser/store/resolver/copy factories, shared attempt lifecycle, canonical Api references, explicit Api traversal, and contract comparisons. S03 consumes these existing integration points. Legacy source processing remains the comparison oracle; ordinary explicit Api supplies declaration frames and canonical resolver identity.

## Verification

Initial capture boundary only: 16 E2E tests pass, with 100% line and branch coverage for the three new implementation modules (408 statements, 42 branches). The two basic resolver cases match a literal output fixture separately verified against fixed main. Eight additional Api fixtures match ordinary Api model bytes and original resolver/loader/validation/type-getter/render call counts. Strict mypy 2.3.1 and Pyright 1.1.411 pass for the initial four implementation modules; architecture checks pass. Full S03 coverage, negative typing, final binding, lifetime, benchmarks and CI have not passed yet. S02 results are prerequisite evidence, not candidate verification. Raw S03 investigation and measurement output belongs in `/private/tmp/dcg-s03-evidence`.

Deferred pointer processing reloads a source document into a new raw dictionary. SourceDocumentId therefore follows the actual loader document key, while declaration/producer frames preserve the actual raw node used by each call. A dictionary-identity requirement for document registration incorrectly rejected the existing local-roots fixture; the implementation no longer conflates these two identities.

Typing exceptions are local: the existing snooper class decorator erases inherited method signatures for Pyright; individual super-call diagnostics are suppressed while strict mypy checks their actual source signatures. The existing OpenAPI constructor omits supported Mapping input in its annotation, requiring one argument-specific mypy ignore. The internal resolver source type is explicitly defined without importing private facade helpers. Private `_ApiObject` is used inside its owning parser layer. No Any annotations or casts were added.

Api operation/schema frames are observed only by the capture instance's list subclass. Ordinary Api retains its original list, append/pop calls, and traversal body. Observation preserves actual effective parameter/security mappings instead of recomputing them. Borrowed frame/type/reference anchors are cleared on parser disposal; the source lease is separately closed by its consumer.

## Contract hashes

- `ACCEPTANCE.md`: `ab8138656f8013a23da95089abe110216270386f405be793c527254fa782276e`
- `ARCHITECTURE.md`: `b5021f0649466928c4ca7e7520536380accc23ba09a00d26a650b5226f8022ad`
- `BACKGROUND.md`: `77cadae0ab8b153e5e8c89e4be7d1d013ab181cac81b3623b5e469aa60470414`
- `CLIENT-REQUIREMENTS.md`: `b8891572c0b224c20286f4d30507aa2c7eb6503e26ec295fc17c78d9972fda08`
- `COVERAGE.md`: `af5941254e33aeb6713aaa74832f95d0f76881cf57c4d6658fb34bbc2be60b71`
- `DECISIONS-BINDING.md`: `ede700ae13a284fdca2928b075f6b0d0432b0f845284238ee9732e5d58cf867d`
- `DECISIONS-CLIENT.md`: `7a73bdb829f33bf6e9a888c3d2f206c2099789f44e5975d5a352feffd31858bd`
- `DECISIONS-ENTRY.md`: `3a49c001d03c2ac4ef9abaa145f665f88b46e6cdfd8a49780c9f9c5598b5a8c0`
- `DECISIONS-FASTAPI.md`: `ceec759e4d6c553858c57d35660a938e94708b94b2aaaa7afd1aa2dff5a2b688`
- `DECISIONS-MODEL-CODECS.md`: `fc4d804065f11c31e27a3174a2436db6b8cdbb7ae9454641110b8e3a144e1678`
- `DECISIONS-PROTOCOLS.md`: `a751d01cbbce08a40cfabaa5fb1f42a29d6b49dc79f8b63b6d30e7034fb330f1`
- `DECISIONS-RUNTIME.md`: `4d3841b1f4359cf384e924c58ff2155981f03e312f86ec94f90c3aed1bad7c66`
- `DECISIONS-TYPING.md`: `e96bcce1ce1d82f21fac898c3e89ecd164961d1397cc2d85cd52c940a7c8c965`
- `FASTAPI-REQUIREMENTS.md`: `e403e5497ae680eba5faa78d8e79935943f8493313518459815bd230ccecb85f`
- `IMPLEMENTATION-PROMPT.md`: `3990a0c8a9d86132b42c77040f08c2d89dc2de7da81ca847514f9190dffa7f61`
- `IMPLEMENTER.md`: `be5062538bc961655cf3bcf0f62b06034b872674b7d78503764d2841535f8a94`
- `PLAN.md`: `e7661f6f6e65eba283bc6cba4f7be7264321ebe0e4c402b6dbe91dd0b779a48e`
- `PR-STACKS.md`: `8a9fca2aa7b4dda07d856b8657be6c957eca1b6da1abd44fe0db16d9fb6dbfa0`
- `RATIONALE.md`: `9f81875b6c5fa3a0946157b05f12ec79b45c39336f4f9f02e7ab15a3349c5cd2`
- `README.md`: `4b0650cb9297ce568990f4f502b22c53229010343af25b819e2f874bba7f9b08`
- `REFERENCES.md`: `840615466e0107c7e50168dea9091359d6cf3f45b30c616a0bbca6c0f399dcf2`

## Handoff

Working branch: `generation-platform-binding-uses`; isolated worktree: `/private/tmp/dcg-generation-platform-s03`. Next: retain completed store replacements, root-collapse recipes, copies and directional variants, then implement final-field integration. Operation normalization and final immutable values remain part of the cumulative S03 gate. No subagents are used. Do not start S04 or merge without authorization.
