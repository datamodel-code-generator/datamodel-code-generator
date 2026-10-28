# Example

A complete block:

```python
from pathlib import Path

print(Path("openapi.yaml"))
```

A block inside a tab, indented by four spaces:

=== "Tab"

    ```python
    import json

    print(json.dumps(data))
    ```

A top-level `await`, which only an async function may hold:

```python
async with session:
    await session.send("hi")
```

Generated blocks are skipped:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: example -->
```python
print(undefined)
```
<!-- END AUTO-GENERATED DOC EXAMPLE: example -->

Other languages are skipped:

```text
print(undefined)
```
