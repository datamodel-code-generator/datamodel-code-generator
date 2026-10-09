# Background and existing type reuse

This plan follows [API Client #40](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/40) and [OpenAPI client generation #3782](https://github.com/datamodel-code-generator/datamodel-code-generator/discussions/3782), checked on 2026-09-18.

The discussions describe concrete demand for typed API calls using existing Python models. They also record earlier concerns about integration complexity and maintenance. These establish the requested workflows; the detailed contracts specify the broader planned runtime scope.

Existing dcg already supports explicit Python type reuse. Model-level `--type-overrides` skips the overridden model and replaces field and inheritance references with an imported type; scoped overrides affect the chosen field. `x-python-import` is another explicit schema path. With Python model input, `--input-model-ref-strategy reuse-all` imports referenced types; it does not mean that the root is always left ungenerated.

The new generator must preserve those choices. Its `verify` mode compares dcg-owned model artifacts, while an externally imported class remains owned by its package. Do not require the source of that external class to become a generated artifact, and do not classify handwritten classes as categorically unsupported. Binding and applicable codec capabilities still need to be verified. [BINDING](DECISIONS-BINDING.md) and [MODEL-CODECS](DECISIONS-MODEL-CODECS.md) define those responsibilities.

Initial public methods follow the operation/resource design in [CLIENT](DECISIONS-CLIENT.md). The path-Literal API shown in #3782 is an example from that discussion, not an additional promised client surface. A general stable plugin API exposing all internal operation representations is also outside the initial scope.

The legacy FastAPI generator provides the server feature inventory in [FASTAPI-REQUIREMENTS](FASTAPI-REQUIREMENTS.md). Its transition begins only when the replacement is available; dcg model compatibility remains a separate requirement.
