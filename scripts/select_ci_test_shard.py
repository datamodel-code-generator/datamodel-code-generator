"""Select deterministic pytest shards for CI."""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

EXCLUDED_PARTS = frozenset({"__pycache__", "cli_doc", "data"})
PAYLOAD_VALIDATION_FILE = "tests/main/test_payload_validation.py"
SPLIT_NODE_FILES = frozenset({PAYLOAD_VALIDATION_FILE})
RECIPE_VERSION = 1
WEIGHTS_VERSION = 2
SLOW_TEST_MS = 1000
PROFILES = ("default", "legacy")
TESTS_ROOT = Path("tests")
WEIGHTS_PATH = Path(__file__).with_name("ci_shard_weights.json")

Weights = dict[str, int]
Profile = tuple[Weights, Weights]


def _as_posix(path: Path) -> str:
    return path.as_posix()


def _collect_split_nodeids(path: Path) -> list[str]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=_as_posix(path))
    nodeids: list[str] = []

    for node in module.body:
        match node:
            case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) if name.startswith("test_"):
                nodeids.append(f"{_as_posix(path)}::{name}")
            case ast.ClassDef(name=class_name) if class_name.startswith("Test"):
                for child in node.body:
                    match child:
                        case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) if name.startswith("test_"):
                            nodeids.append(f"{_as_posix(path)}::{class_name}::{name}")

    return sorted(nodeids)


def _collect_test_files(root: Path = TESTS_ROOT) -> list[str]:
    """Return every sharded test module below ``root`` in sorted order."""
    files: list[str] = []
    for directory, subdirectories, filenames in os.walk(root):
        subdirectories[:] = [name for name in subdirectories if name not in EXCLUDED_PARTS]
        files.extend(
            (Path(directory) / name).as_posix()
            for name in filenames
            if name.startswith("test_") and name.endswith(".py")
        )
    files.sort()
    return files


def _collect_test_items(root: Path = TESTS_ROOT) -> list[str]:
    file_items = [file for file in _collect_test_files(root) if file not in SPLIT_NODE_FILES]
    split_node_items = (
        nodeid for split_file in sorted(SPLIT_NODE_FILES) for nodeid in _collect_split_nodeids(Path(split_file))
    )
    file_items.extend(split_node_items)
    file_items.sort()
    return file_items


def _median_weight(weights: dict[str, int]) -> int:
    ordered = sorted(weights.values())
    middle = len(ordered) // 2
    return (ordered[middle] + ordered[~middle]) // 2


def _is_profile(profile: object) -> bool:
    match profile:
        case {"files": dict(files), "nodes": dict(nodes), "slow": dict()} if files and nodes:
            return True
    return False


def _read_weights(path: Path) -> dict[str, Any]:
    """Return the validated measured-weight document."""
    document = json.loads(path.read_text(encoding="utf-8"))
    match document:
        case {"version": int(version), "profiles": dict(profiles)} if version == WEIGHTS_VERSION and all(
            _is_profile(profiles.get(name)) for name in PROFILES
        ):
            return document
    msg = f"unsupported shard weights: {path}"
    raise SystemExit(msg)


def _profile_weights(document: dict[str, Any], profile: str) -> Profile:
    measured = document["profiles"][profile]
    return measured["files"], measured["nodes"]


def _item_weight(item: str, weights: Profile, fallback: tuple[int, int]) -> int:
    index = 1 if "::" in item else 0
    return weights[index].get(item, fallback[index])


def _build_recipe_items(weights: Profile) -> list[dict[str, int | str]]:
    fallback = (_median_weight(weights[0]), _median_weight(weights[1]))
    return [{"nodeid": item, "weight": _item_weight(item, weights, fallback)} for item in _collect_test_items()]


def _validate_recipe_items(items: object) -> list[dict[str, int | str]]:
    if not isinstance(items, list) or not items:
        msg = "recipe items must be a nonempty list"
        raise SystemExit(msg)

    validated: list[dict[str, int | str]] = []
    seen: set[str] = set()
    for item in items:
        match item:
            case {"nodeid": str(nodeid), "weight": int(weight)} if (
                not isinstance(weight, bool)
                and weight > 0
                and nodeid.strip()
                and "\n" not in nodeid
                and "\r" not in nodeid
                and nodeid not in seen
            ):
                seen.add(nodeid)
                validated.append({"nodeid": nodeid, "weight": weight})
            case _:
                msg = f"invalid recipe item: {item!r}"
                raise SystemExit(msg)
    return validated


def _load_recipe_items(path: Path) -> list[dict[str, int | str]]:
    recipe = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(recipe, dict) or recipe.get("version") != RECIPE_VERSION or "items" not in recipe:
        msg = f"unsupported shard recipe: {recipe!r}"
        raise SystemExit(msg)
    return _validate_recipe_items(recipe["items"])


def _write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _module_file(classname: str, modules: dict[str, str]) -> tuple[str, str] | None:
    """Split a JUnit classname into its known test module and class chain."""
    parts = classname.split(".")
    for end in range(len(parts), 0, -1):
        if (file := modules.get(".".join(parts[:end]))) is not None:
            return file, "::".join(parts[end:])
    return None


def _milliseconds(totals: dict[str, float]) -> Weights:
    return {key: max(1, round(seconds * 1000)) for key, seconds in sorted(totals.items())}


def _junit_weights(paths: Iterable[Path], files: Iterable[str]) -> tuple[Weights, Weights, Weights]:
    """Sum JUnit durations per sharded module, per split-file test function, and per slow test."""
    from xml.etree.ElementTree import iterparse  # noqa: PLC0415

    modules = {file[:-3].replace("/", "."): file for file in files}
    file_totals: dict[str, float] = {}
    node_totals: dict[str, float] = {}
    slow_tests: dict[str, float] = {}
    for path in paths:
        for _, element in iterparse(path):
            if element.tag != "testcase":
                continue
            located = _module_file(element.get("classname", ""), modules)
            seconds = float(element.get("time", "0"))
            name = element.get("name", "")
            element.clear()
            if located is None:
                continue
            file, classes = located
            if seconds * 1000 >= SLOW_TEST_MS:
                slow_tests["::".join(part for part in (file, classes, name) if part)] = seconds
            if file not in SPLIT_NODE_FILES:
                file_totals[file] = file_totals.get(file, 0.0) + seconds
                continue
            nodeid = "::".join(part for part in (file, classes, name.partition("[")[0]) if part)
            node_totals[nodeid] = node_totals.get(nodeid, 0.0) + seconds
    if not file_totals or not node_totals:
        msg = "JUnit reports must include sharded module and split-file test durations"
        raise SystemExit(msg)
    return _milliseconds(file_totals), _milliseconds(node_totals), _milliseconds(slow_tests)


def _record_weights(path: Path, profile: str, junit_paths: Iterable[Path], *, run_id: str, sha: str) -> None:
    """Replace one profile's measured weights with durations from CI JUnit reports."""
    document = _read_weights(path)
    files, nodes, slow = _junit_weights(junit_paths, _collect_test_files())
    document["profiles"][profile] = {
        "files": files,
        "nodes": nodes,
        "provenance": {"run_id": run_id, "sha": sha},
        "slow": slow,
    }
    _write_json(path, document)


def _select_shard(items: list[dict[str, int | str]], shard_index: int, shard_total: int) -> list[str]:
    """Assign items to shards heaviest-first and keep that order so xdist starts long tests early."""
    shards: list[list[str]] = [[] for _ in range(shard_total)]
    shard_weights = [0] * shard_total
    weighted_items = sorted(
        ((int(item["weight"]), str(item["nodeid"])) for item in items),
        key=lambda item: (-item[0], item[1]),
    )

    for weight, item in weighted_items:
        target = min(
            range(shard_total),
            key=lambda index: (shard_weights[index], len(shards[index]), index),
        )
        shards[target].append(item)
        shard_weights[target] += weight

    return shards[shard_index - 1]


def main(argv: list[str] | None = None) -> None:
    """Select a shard, serialize its reproducible recipe, or record measured weights."""
    parser = argparse.ArgumentParser()
    parser.add_argument("shard_index", type=int, nargs="?")
    parser.add_argument("shard_total", type=int, nargs="?")
    parser.add_argument("--profile", choices=PROFILES, default="default")
    parser.add_argument("--weights", type=Path, default=WEIGHTS_PATH)
    parser.add_argument("--recipe", type=Path)
    parser.add_argument("--write-recipe", type=Path)
    parser.add_argument("--junit", type=Path, nargs="+")
    parser.add_argument("--run-id")
    parser.add_argument("--sha")
    args = parser.parse_args(argv)

    if args.junit:
        if args.run_id is None or args.sha is None:
            parser.error("--run-id and --sha are required with --junit")
        _record_weights(args.weights, args.profile, args.junit, run_id=args.run_id, sha=args.sha)
        return

    items = (
        _load_recipe_items(args.recipe)
        if args.recipe
        else _build_recipe_items(_profile_weights(_read_weights(args.weights), args.profile))
    )
    if args.write_recipe:
        _write_json(args.write_recipe, {"version": RECIPE_VERSION, "items": items})
        if args.shard_index is None and args.shard_total is None:
            return

    if args.shard_index is None or args.shard_total is None:
        parser.error("shard_index and shard_total are required unless only --write-recipe is used")

    shard_index = args.shard_index
    shard_total = args.shard_total

    match 1 <= shard_index <= shard_total:
        case False:
            msg = "shard_index must be between 1 and shard_total"
            raise SystemExit(msg)
        case _:
            if selected := _select_shard(items, shard_index, shard_total):
                print(*selected, sep="\n")
                return

    msg = f"No tests selected for shard {shard_index}/{shard_total}"
    raise SystemExit(msg)


if __name__ == "__main__":
    main()
