"""Run the pinned JSON-Schema-Test-Suite draft 2020-12 corpus through offline codec schema bundles."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from datamodel_code_generator._runtime.model_codecs.errors import CodecError
from datamodel_code_generator._runtime.model_codecs.media import decode_json
from datamodel_code_generator._runtime.model_codecs.schema import SchemaBundle, SchemaResource

if TYPE_CHECKING:
    from pathlib import Path


def _remotes(suite: Path) -> list[SchemaResource]:
    root = suite / "remotes"
    remotes = [
        SchemaResource(uri=f"http://localhost:1234/{path.relative_to(root).as_posix()}", contents=decode_json(path.read_bytes()))
        for path in sorted(root.rglob("*.json"))
        if not any(part.startswith("draft") and part != "draft2020-12" for part in path.relative_to(root).parts)
    ]
    while True:
        try:
            SchemaBundle(remotes)
        except CodecError as error:
            remotes = [remote for remote in remotes if remote.uri not in str(error)]
        else:
            return remotes


def json_schema_suite_report(suite: Path) -> str:
    """Count passes and list every mismatch or bundle rejection, keeping the suite's own expectations."""
    remotes = _remotes(suite)
    counts: Counter[str] = Counter()
    mismatches: list[str] = []
    tests = suite / "tests/draft2020-12"
    for path in sorted(tests.rglob("*.json")):
        relative = path.relative_to(tests).as_posix()
        for index, group in enumerate(decode_json(path.read_bytes())):
            uri = f"https://dcg.invalid/suite/{relative}/{index}"
            try:
                validator = SchemaBundle([SchemaResource(uri=uri, contents=group["schema"]), *remotes]).validator(f"{uri}#")
            except CodecError as error:
                counts["configuration"] += len(group["tests"])
                mismatches.append(f"{relative} [{index}] {group['description']}: {type(error).__name__}")
                continue
            for test in group["tests"]:
                if (not validator.validate(test["data"])) == test["valid"]:
                    counts["pass"] += 1
                else:
                    counts["mismatch"] += 1
                    mismatches.append(f"{relative} [{index}] {group['description']} / {test['description']}")
    return "\n".join([f"remotes: {len(remotes)}", *(f"{name}: {counts[name]}" for name in sorted(counts)), *mismatches]) + "\n"
