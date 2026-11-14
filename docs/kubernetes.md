<!-- related-cli-options: --read-kubernetes-extensions, --strict-nullable, --type-mappings, --type-overrides, --type-overrides-replace-whole-type, --field-extra-keys -->

# ☸️ Kubernetes Schemas

Kubernetes describes its resources with OpenAPI schemas: the `openAPIV3Schema` of each version of a
CustomResourceDefinition (CRD), and the `/openapi/v3` documents a cluster serves for every group version. These schemas
carry `x-kubernetes-*` extensions that change what a value may be. `--read-kubernetes-extensions` reads them, so the
generated models accept and keep the values Kubernetes does.

## 🚀 Quick Start

```bash
# A cluster's document of one group version (built-in resources and served CRDs)
kubectl get --raw /openapi/v3/apis/apps/v1 > apps_v1.json
datamodel-codegen --input apps_v1.json --input-file-type openapi --output models --read-kubernetes-extensions

# The schema of one version of a CRD file
yq '.spec.versions[] | select(.name == "v1") | .schema.openAPIV3Schema' widgets.yaml > widget.yaml
datamodel-codegen --input widget.yaml --input-file-type jsonschema --class-name Widget \
    --read-kubernetes-extensions --strict-nullable
```

The `standard-*-20261113` and later presets set `--read-kubernetes-extensions`.

## 📥 Two Ways to Get the Schemas

**Cluster documents.** `kubectl get --raw /openapi/v3/apis/<group>/<version>` (or `/openapi/v3/api/v1` for the core
group) returns a complete OpenAPI 3.0 document: the resources with their `x-kubernetes-group-version-kind`, the
`ObjectMeta` they reference and every served CRD as the API server publishes it. Read it with
`--input-file-type openapi`; the dotted schema names (`io.k8s.api.apps.v1.Deployment`) become modules
(`io/k8s/api/apps/v1.py`). Each document repeats the shared types, so to generate several group versions into one
package, merge their `components.schemas` first:

```bash
jq -s '.[0] * {components: {schemas: (map(.components.schemas) | add)}}' apps_v1.json batch_v1.json > merged.json
```

**CRD files.** The schema of a CRD version sits at `spec.versions[N].schema.openAPIV3Schema`. Extract it and read it as
JSON Schema. The extracted schema has no title (give the class a name with `--class-name` or add a `title`), no
`x-kubernetes-group-version-kind` (add one, as below, to fix `apiVersion` and `kind`), and writes OpenAPI 3.0's
`nullable`, which `--strict-nullable` reads. A whole CRD file, given as it is, is read as sample data, not as a schema.

```yaml
x-kubernetes-group-version-kind:
- group: example.com
  version: v1
  kind: Widget
```

## ⚙️ What `--read-kubernetes-extensions` Reads

Each extension is read as the standard keywords it stands for, wherever a schema object writes it:

| Extension | Read as | Output |
|---|---|---|
| `x-kubernetes-int-or-string: true`, or `format: int-or-string` | `type: [integer, string]`, in place of an `anyOf` or `oneOf` of exactly those two types | `int \| str`; a quantity (that `anyOf` beside a `pattern`) is one `int \| constr(pattern=...)` instead of two root models |
| `x-kubernetes-preserve-unknown-fields: true` beside `properties` and no `additionalProperties` | `additionalProperties: true` | Pydantic models keep the keys they do not declare (`extra='allow'`) |
| `x-kubernetes-group-version-kind` | `const` and `default` of the `apiVersion` and `kind` properties | `apiVersion: Literal['apps/v1'] = 'apps/v1'`, `kind: Literal['Deployment'] = 'Deployment'` |

- The group version kind fixes a property only where every entry of the list names the same value for it and the
  property is a plain string schema (no `enum`, `const`, `$ref` or combinator; a `default`, if any, equal to the
  value). `DeleteOptions`, listed under two groups, gets its `kind` but keeps a free `apiVersion`. The core group,
  whose name is empty, gives `v1`. An entry without a `group`, `version` and `kind` string is not read, and neither is
  the list in a draft-04 document, where `const` is no keyword.
- A model keeps unknown keys only where the output can: a Pydantic `BaseModel` keeps and dumps them, and a Pydantic
  dataclass keeps them as attributes, as for `additionalProperties: true`. `msgspec` drops unknown fields by design, a
  standard dataclass has no place for them, and a `TypedDict` accepts them already. A preserved
  object without `properties` is a `dict[str, Any]`, and an untyped one is `Any`, with or without the option.
- `--extra-fields` wins over the extra keys this option keeps, as it does over `additionalProperties`.
- Under `--union-mode left_to_right` a numeric string such as `"5"` reads as `5`; keep the default smart mode.

Input: an extracted CRD version with a group version kind added

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: kubernetes.crd.schema -->
<details>
<summary>widget.yaml</summary>

<!-- fmt: off -->

```yaml
$schema: http://json-schema.org/draft-07/schema#
title: Widget
description: Widget is the Schema for the widgets API.
type: object
x-kubernetes-group-version-kind:
- group: example.com
  kind: Widget
  version: v1
required:
- spec
properties:
  apiVersion:
    description: APIVersion defines the versioned schema of this representation of an object.
    type: string
  kind:
    description: Kind is a string value representing the REST resource this object represents.
    type: string
  metadata:
    type: object
  spec:
    description: WidgetSpec defines the desired state of Widget.
    type: object
    required:
    - replicas
    properties:
      replicas:
        type: integer
        format: int32
      maxSurge:
        anyOf:
        - type: integer
        - type: string
        x-kubernetes-int-or-string: true
      port:
        x-kubernetes-int-or-string: true
      targetPort:
        type:
        - integer
        - string
        x-kubernetes-int-or-string: true
      resources:
        type: object
        properties:
          limits:
            type: object
            additionalProperties:
              anyOf:
              - type: integer
              - type: string
              pattern: ^(\+|-)?(([0-9]+(\.[0-9]*)?)|(\.[0-9]+))(([KMGTPE]i)|[numkMGTPE]|([eE](\+|-)?(([0-9]+(\.[0-9]*)?)|(\.[0-9]+))))?$
              x-kubernetes-int-or-string: true
      selector:
        type: object
        properties:
          app:
            type: string
        x-kubernetes-preserve-unknown-fields: true
      config:
        x-kubernetes-preserve-unknown-fields: true
      template:
        type: object
        x-kubernetes-embedded-resource: true
        x-kubernetes-preserve-unknown-fields: true
      ports:
        type: array
        items:
          type: object
          required:
          - name
          properties:
            name:
              type: string
            port:
              type: integer
        x-kubernetes-list-map-keys:
        - name
        x-kubernetes-list-type: map
      finalizers:
        type: array
        items:
          type: string
        x-kubernetes-list-type: set
      labels:
        type: object
        additionalProperties:
          type: string
        x-kubernetes-map-type: atomic
      paused:
        type: boolean
        nullable: true
      updatePolicy:
        type: string
        enum:
        - Recreate
    x-kubernetes-validations:
    - message: replicas must not be negative
      rule: self.replicas >= 0
  status:
    type: object
    properties:
      readyReplicas:
        type: integer
      conditions:
        type: array
        items:
          type: object
          required:
          - type
          - status
          properties:
            type:
              type: string
            status:
              type: string
              enum:
              - "True"
              - "False"
              - Unknown
        x-kubernetes-list-map-keys:
        - type
        x-kubernetes-list-type: map
```

<!-- fmt: on -->
</details>
<!-- END AUTO-GENERATED DOC EXAMPLE: kubernetes.crd.schema -->

```bash
datamodel-codegen --input widget.yaml --input-file-type jsonschema --read-kubernetes-extensions
```

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: kubernetes.crd.output -->
<!-- fmt: off -->

```python
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, constr


class Resources(BaseModel):
    limits: (
        dict[
            str,
            int
            | constr(
                pattern=r'^(\+|-)?(([0-9]+(\.[0-9]*)?)|(\.[0-9]+))(([KMGTPE]i)|[numkMGTPE]|([eE](\+|-)?(([0-9]+(\.[0-9]*)?)|(\.[0-9]+))))?$'
            ),
        ]
        | None
    ) = None


class Selector(BaseModel):
    model_config = ConfigDict(
        extra='allow',
    )
    app: str | None = None


class Port(BaseModel):
    name: str
    port: int | None = None


class UpdatePolicy(Enum):
    Recreate = 'Recreate'


class Spec(BaseModel):
    replicas: int
    maxSurge: int | str | None = None
    port: int | str | None = None
    targetPort: int | str | None = None
    resources: Resources | None = None
    selector: Selector | None = None
    config: Any | None = None
    template: dict[str, Any] | None = None
    ports: list[Port] | None = None
    finalizers: list[str] | None = None
    labels: dict[str, str] | None = None
    paused: bool | None = None
    updatePolicy: UpdatePolicy | None = None


class Status1(Enum):
    True_ = 'True'
    False_ = 'False'
    Unknown = 'Unknown'


class Condition(BaseModel):
    type: str
    status: Status1


class Status(BaseModel):
    readyReplicas: int | None = None
    conditions: list[Condition] | None = None


class Widget(BaseModel):
    apiVersion: Literal['example.com/v1'] = Field(
        'example.com/v1',
        description='APIVersion defines the versioned schema of this representation of an object.',
    )
    kind: Literal['Widget'] = Field(
        'Widget',
        description='Kind is a string value representing the REST resource this object represents.',
    )
    metadata: dict[str, Any] | None = None
    spec: Spec = Field(
        ..., description='WidgetSpec defines the desired state of Widget.'
    )
    status: Status | None = None
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: kubernetes.crd.output -->

## 📤 Sending Resources

`apiVersion` and `kind` take their values by default, so a model built without them is complete. Dump it with
`model_dump(exclude_none=True)` rather than `exclude_unset=True`: a default is not "set", and `exclude_unset` would
leave `apiVersion` and `kind` out of the request.

```python
widget = Widget(spec=Spec(replicas=2))
widget.model_dump(by_alias=True, exclude_none=True)
# {'apiVersion': 'example.com/v1', 'kind': 'Widget', 'spec': {'replicas': 2}}
```

## 🧰 Options That Go Well With It

- `--strict-nullable` reads the `nullable: true` of a CRD schema read as JSON Schema.
- `--type-mappings string+duration=string` keeps Go durations (`format: duration`, such as `"15s"` or `"1h30m"`) as
  strings: by default they are `timedelta`, which reads ISO 8601 durations and rejects that form.
- `--type-overrides '{"Widget.metadata": "my_models.ObjectMeta"}'` with `--type-overrides-replace-whole-type` types the
  `metadata` of a resource read from a CRD file, which the CRD writes as a bare `type: object`, by an `ObjectMeta`
  model you have, for example one generated from a cluster document.
- `--field-extra-keys` carries the extensions this option does not read into the field's `json_schema_extra`, for
  example `--field-extra-keys x-kubernetes-validations`.

## 🚫 What Is Not Read

- `x-kubernetes-list-type`, `x-kubernetes-list-map-keys`, `x-kubernetes-map-type`, `x-kubernetes-patch-strategy` and
  `x-kubernetes-patch-merge-key` describe how server-side apply and strategic merge combine values; no Python type
  expresses that. A list of type `set` stays a `list`, which keeps the order Kubernetes keeps.
- `x-kubernetes-validations` holds CEL rules; evaluating them would need a CEL runtime in the generated models.
- `x-kubernetes-embedded-resource` stands beside `x-kubernetes-preserve-unknown-fields` on an object, which is already
  a `dict[str, Any]` in a CRD file; in a cluster document the API server adds the `apiVersion`, `kind` and `metadata`
  properties, and the object keeps its other keys.
- `x-kubernetes-unions` is not read.

## 📖 See Also

- [Generate from JSON Schema](jsonschema.md)
- [Generate from OpenAPI](openapi.md)
- [Presets](presets.md)
- [CLI Reference: `--read-kubernetes-extensions`](cli-reference/typing-customization.md#read-kubernetes-extensions)
