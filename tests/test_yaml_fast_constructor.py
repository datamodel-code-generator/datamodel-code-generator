"""Parity coverage for the optimized PyYAML constructor."""

from __future__ import annotations

import json
import random
import warnings
from math import isnan
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from yaml.constructor import BaseConstructor

from datamodel_code_generator._source import load_yaml
from datamodel_code_generator.util import _fast_construct_document, get_safe_loader
from tests.conftest import assert_output

if TYPE_CHECKING:
    from collections.abc import Iterator

_DATA_PATH = Path(__file__).parent / "data" / "yaml_fast_constructor"
_EXPECTED_PATH = Path(__file__).parent / "data" / "expected" / "yaml_fast_constructor"
_PARITY_OUTPUT = _EXPECTED_PATH / "parity.txt"
_CASES: dict[str, str] = json.loads((_DATA_PATH / "cases.json").read_text(encoding="utf-8"))
_SCHEMA_PATHS = tuple(sorted((Path(__file__).parent / "data" / "openapi").rglob("*.yaml"))) + tuple(
    sorted((Path(__file__).parent / "data" / "asyncapi").rglob("*.yaml"))
)


class _PurePythonFastSafeLoader(yaml.SafeLoader):
    """Use the optimized constructor with the pure-Python SafeLoader."""

    def construct_document(self, node: Any) -> Any:
        data = _fast_construct_document(self, node)
        self.constructed_objects = {}
        self.recursive_objects = {}
        self.deep_construct = False
        return data


def _construct_deferred_mapping(loader: Any, node: Any) -> Iterator[Any]:
    """Defer a nested generator so the constructor queue has another breadth level."""
    result: dict[Any, Any] = {}
    yield result
    for key_node, value_node in node.value:
        result[loader.construct_object(key_node)] = loader.construct_object(value_node)
    yield None


class _DeferredFastSafeLoader(get_safe_loader()):
    """Exercise deferred PyYAML generator work that ordinary SafeLoader inputs do not retain."""

    yaml_constructors = get_safe_loader().yaml_constructors.copy()

    def construct_document(self, node: Any) -> Any:
        data = _fast_construct_document(self, node)
        self.constructed_objects = {}
        self.recursive_objects = {}
        self.deep_construct = False
        return data


_DeferredFastSafeLoader.yaml_constructors["!deferred"] = _construct_deferred_mapping


class _CustomMapping(dict[Any, Any]):  # noqa: FURB189
    """Make custom mapping construction observable in parity coverage."""


class _CustomSequence(list[Any]):  # noqa: FURB189
    """Make custom sequence construction observable in parity coverage."""


def _without_snippet(mark: yaml.Mark | None) -> yaml.Mark | None:
    return mark and yaml.Mark(mark.name, mark.index, mark.line, mark.column, None, None)


def _error_message(exc: Exception) -> str:
    """Render an error without the source snippet that only the pure-Python reader attaches to marks."""
    if isinstance(exc, yaml.MarkedYAMLError):
        exc = type(exc)(
            exc.context, _without_snippet(exc.context_mark), exc.problem, _without_snippet(exc.problem_mark), exc.note
        )
    return f"{type(exc).__name__}: {exc}"


def _construct(text: str, loader_class: type, *, standard: bool) -> tuple[Any, str | None, list[tuple[str, str]]]:
    loader = loader_class(text)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            node = loader.get_single_node()
            if node is None:
                data, error = None, None
            elif standard:
                data, error = BaseConstructor.construct_document(loader, node), None
            else:
                data, error = loader.construct_document(node), None
        except Exception as exc:  # noqa: BLE001
            data, error = None, _error_message(exc)
        finally:
            loader.dispose()
    return data, error, [(str(item.message), item.category.__name__) for item in caught]


def _strict_equal(a: Any, b: Any, seen: dict[int, Any] | None = None) -> bool:  # noqa: PLR0911
    """Compare types, dict order, aliases, and cyclic containers exactly."""
    if seen is None:
        seen = {}
    if type(a) is not type(b):
        return False
    if isinstance(a, (dict, list, set, tuple)):
        if id(a) in seen:
            return seen[id(a)] is b
        seen[id(a)] = b
    if isinstance(a, dict):
        if list(a) != list(b) or any(type(x) is not type(y) for x, y in zip(a, b, strict=False)):
            return False
        return all(_strict_equal(a[key], b[key], seen) for key in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(_strict_equal(left, right, seen) for left, right in zip(a, b, strict=False))
    if isinstance(a, set):
        return a == b
    if isinstance(a, float) and isnan(a):
        return isnan(b)
    return a == b


def _parity_report(
    standard: tuple[Any, str | None, list[tuple[str, str]]],
    fast: tuple[Any, str | None, list[tuple[str, str]]],
) -> str:
    standard_data, standard_error, standard_warnings = standard
    fast_data, fast_error, fast_warnings = fast
    value_equal = (
        _strict_equal(standard_data, fast_data)
        if standard_error is None and fast_error is None
        else standard_error is not None and fast_error is not None
    )
    return "\n".join((
        f"exception={standard_error == fast_error}",
        f"warnings={standard_warnings == fast_warnings}",
        f"value={value_equal}",
        "",
    ))


def _assert_parity(text: str, loader_class: type) -> None:
    standard = _construct(text, loader_class, standard=True)
    fast = _construct(text, loader_class, standard=False)
    assert_output(_parity_report(standard, fast), _PARITY_OUTPUT)


def test_strict_equal_handles_non_equivalent_values() -> None:
    """Cover the comparator's defensive mismatch and NaN cases."""
    assert_output(
        "\n".join((
            f"type_mismatch={_strict_equal(1, '1')}",
            f"mapping_order_mismatch={_strict_equal({'a': 1, 'b': 2}, {'b': 2, 'a': 1})}",
            f"nan_equal={_strict_equal(float('nan'), float('nan'))}",
            "",
        )),
        _EXPECTED_PATH / "strict_equal.txt",
    )


def _select_yaml_loader(name: str) -> type:
    """Select a supported loader implementation for parity coverage."""
    loader_class: type = _PurePythonFastSafeLoader
    match name:
        case "custom":
            loader_class = get_safe_loader()
        case "pure-python":
            pass
        case _:
            raise AssertionError
    return loader_class


@pytest.fixture(params=("custom", "pure-python"))
def yaml_loader(request: pytest.FixtureRequest) -> type:
    """Return both C-backed and pure-Python loader implementations."""
    return _select_yaml_loader(request.param)


def test_yaml_loader_rejects_unknown_implementation() -> None:
    """Keep the fixture's defensive selector branch covered."""
    with pytest.raises(AssertionError) as exception_info:
        _select_yaml_loader("unknown")

    assert_output(f"{type(exception_info.value).__name__}\n", _EXPECTED_PATH / "invalid_loader.txt")


@pytest.mark.parametrize("text", _CASES.values(), ids=_CASES)
def test_fast_constructor_matches_standard_cases(text: str, yaml_loader: type) -> None:
    """Preserve PyYAML construction semantics for supported and error inputs."""
    _assert_parity(text, yaml_loader)


def test_fast_constructor_drains_nested_deferred_generators() -> None:
    """Keep PyYAML's generator queue in breadth-first order across nested constructors."""
    _assert_parity("outer: !deferred {nested: !!omap [{key: value}]}\n", _DeferredFastSafeLoader)


@pytest.mark.parametrize(
    ("constructor_form", "custom_tags", "input_path", "expected_path", "error_path"),
    [
        pytest.param(
            "return",
            ("map",),
            _DATA_PATH / "custom_return.yaml",
            _EXPECTED_PATH / "custom_return_map.txt",
            _DATA_PATH / "custom_error.yaml",
            id="return-map",
        ),
        pytest.param(
            "return",
            ("seq",),
            _DATA_PATH / "custom_return.yaml",
            _EXPECTED_PATH / "custom_return_seq.txt",
            _DATA_PATH / "custom_error.yaml",
            id="return-seq",
        ),
        pytest.param(
            "return",
            ("map", "seq"),
            _DATA_PATH / "custom_return.yaml",
            _EXPECTED_PATH / "custom_return_map_seq.txt",
            _DATA_PATH / "custom_error.yaml",
            id="return-map-seq",
        ),
        pytest.param(
            "generator",
            ("map",),
            _DATA_PATH / "custom_generator.yaml",
            _EXPECTED_PATH / "custom_generator_map.txt",
            _DATA_PATH / "custom_error.yaml",
            id="generator-map",
        ),
        pytest.param(
            "generator",
            ("seq",),
            _DATA_PATH / "custom_generator.yaml",
            _EXPECTED_PATH / "custom_generator_seq.txt",
            _DATA_PATH / "custom_error.yaml",
            id="generator-seq",
        ),
        pytest.param(
            "generator",
            ("map", "seq"),
            _DATA_PATH / "custom_generator.yaml",
            _EXPECTED_PATH / "custom_generator_map_seq.txt",
            _DATA_PATH / "custom_error.yaml",
            id="generator-map-seq",
        ),
    ],
)
def test_fast_constructor_preserves_custom_map_seq_constructors(  # noqa: PLR0914
    constructor_form: str,
    custom_tags: tuple[str, ...],
    input_path: Path,
    expected_path: Path,
    error_path: Path,
) -> None:
    """Delegate map and sequence extensions to PyYAML without changing their behavior."""
    text = input_path.read_text(encoding="utf-8")
    callback_log: list[str] = []

    class CustomMapSeqFastSafeLoader(get_safe_loader()):
        """Install the selected custom constructors over the optimized loader."""

        yaml_constructors = get_safe_loader().yaml_constructors.copy()

    if constructor_form == "return":

        def construct_mapping(loader: Any, node: Any) -> _CustomMapping:
            callback_log.append("map")
            return _CustomMapping(loader.construct_mapping(node))

        def construct_sequence(loader: Any, node: Any) -> _CustomSequence:
            callback_log.append("seq")
            return _CustomSequence(loader.construct_sequence(node))

    else:

        def construct_mapping(loader: Any, node: Any) -> Iterator[Any]:
            callback_log.append("map")
            result = _CustomMapping()
            yield result
            result.update(loader.construct_mapping(node))

        def construct_sequence(loader: Any, node: Any) -> Iterator[Any]:
            callback_log.append("seq")
            result = _CustomSequence()
            yield result
            result.extend(loader.construct_sequence(node))

    if "map" in custom_tags:
        CustomMapSeqFastSafeLoader.add_constructor("tag:yaml.org,2002:map", construct_mapping)
    if "seq" in custom_tags:
        CustomMapSeqFastSafeLoader.add_constructor("tag:yaml.org,2002:seq", construct_sequence)

    callback_log.clear()
    standard = _construct(text, CustomMapSeqFastSafeLoader, standard=True)
    standard_callbacks = ",".join(callback_log)
    standard_data = standard[0]

    callback_log.clear()
    fast = _construct(text, CustomMapSeqFastSafeLoader, standard=False)
    fast_callbacks = ",".join(callback_log)
    fast_data = fast[0]

    callback_log.clear()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        public_data = yaml.load(text, Loader=CustomMapSeqFastSafeLoader)
    public = (public_data, None, [(str(item.message), item.category.__name__) for item in caught])
    public_callbacks = ",".join(callback_log)

    mapping_type = _CustomMapping if "map" in custom_tags else dict
    sequence_type = _CustomSequence if "seq" in custom_tags else list
    mapping_types = all(
        type(value) is mapping_type
        for value in (
            standard_data,
            fast_data,
            public_data,
            standard_data["entry"],
            fast_data["entry"],
            public_data["entry"],
        )
    )
    sequence_types = all(
        type(value) is sequence_type
        for value in (
            standard_data["defaults"]["labels"],
            fast_data["defaults"]["labels"],
            public_data["defaults"]["labels"],
        )
    )
    aliases = (
        standard_data["entry"] is standard_data["copy"]
        and fast_data["entry"] is fast_data["copy"]
        and public_data["entry"] is public_data["copy"]
    )
    nested_references = (
        standard_data["entry"]["labels"] is standard_data["defaults"]["labels"]
        and fast_data["entry"]["labels"] is fast_data["defaults"]["labels"]
        and public_data["entry"]["labels"] is public_data["defaults"]["labels"]
    )
    merge = (
        standard_data["entry"]["category"] == "base"
        and fast_data["entry"]["category"] == "base"
        and public_data["entry"]["category"] == "base"
    )
    cycle = (
        "self" in standard_data
        and standard_data["self"]["child"] is standard_data["self"]
        and "self" in fast_data
        and fast_data["self"]["child"] is fast_data["self"]
        and "self" in public_data
        and public_data["self"]["child"] is public_data["self"]
    )

    error_text = error_path.read_text(encoding="utf-8")
    callback_log.clear()
    standard_error = _construct(error_text, CustomMapSeqFastSafeLoader, standard=True)
    standard_error_callbacks = ",".join(callback_log)

    callback_log.clear()
    fast_error = _construct(error_text, CustomMapSeqFastSafeLoader, standard=False)
    fast_error_callbacks = ",".join(callback_log)

    callback_log.clear()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            public_error_data, public_error_message = yaml.load(error_text, Loader=CustomMapSeqFastSafeLoader), None
        except Exception as exc:  # noqa: BLE001
            public_error_data, public_error_message = None, _error_message(exc)
    public_error = (
        public_error_data,
        public_error_message,
        [(str(item.message), item.category.__name__) for item in caught],
    )
    public_error_callbacks = ",".join(callback_log)

    assert_output(
        "\n".join((
            f"fast_{_parity_report(standard, fast).replace(chr(10), ',').rstrip(',')}",
            f"public_{_parity_report(standard, public).replace(chr(10), ',').rstrip(',')}",
            f"standard_callbacks={standard_callbacks}",
            f"fast_callbacks={fast_callbacks}",
            f"public_callbacks={public_callbacks}",
            f"callback_order={standard_callbacks == fast_callbacks}",
            f"public_callback_order={standard_callbacks == public_callbacks}",
            f"mapping_types={mapping_types}",
            f"sequence_types={sequence_types}",
            f"aliases={aliases}",
            f"nested_references={nested_references}",
            f"merge={merge}",
            f"cycle={cycle}",
            f"error_fast_{_parity_report(standard_error, fast_error).replace(chr(10), ',').rstrip(',')}",
            f"error_public_{_parity_report(standard_error, public_error).replace(chr(10), ',').rstrip(',')}",
            f"error_standard={standard_error[1]}",
            f"error_fast={fast_error[1]}",
            f"error_public={public_error[1]}",
            f"error_standard_callbacks={standard_error_callbacks}",
            f"error_fast_callbacks={fast_error_callbacks}",
            f"error_public_callbacks={public_error_callbacks}",
            f"error_callback_order={standard_error_callbacks == fast_error_callbacks}",
            f"error_public_callback_order={standard_error_callbacks == public_error_callbacks}",
            "",
        )),
        expected_path,
    )


@pytest.mark.parametrize(
    "path", _SCHEMA_PATHS, ids=lambda path: path.relative_to(Path(__file__).parent / "data").as_posix()
)
def test_fast_constructor_matches_schema_fixtures(path: Path) -> None:
    """Keep the optimized C-loader path identical for every OpenAPI and AsyncAPI YAML fixture."""
    _assert_parity(path.read_text(encoding="utf-8"), get_safe_loader())


def _fuzz_value(random_: random.Random, depth: int = 0) -> Any:
    if depth == 3 or random_.randrange(4) == 0:
        return random_.choice((None, True, False, random_.randrange(-10, 11), random_.random(), "value"))
    if random_.randrange(2):
        return [_fuzz_value(random_, depth + 1) for _ in range(random_.randrange(4))]
    return {f"key_{index}": _fuzz_value(random_, depth + 1) for index in range(random_.randrange(4))}


def _render_fuzz_document(random_: random.Random, value: Any) -> str:
    """Render aliases, random styles, explicit key/value tags, and merge keys."""
    text = yaml.dump(
        value,
        default_flow_style=random_.choice((None, False, True)),
        default_style=random_.choice((None, "'", '"')),
        sort_keys=False,
        allow_unicode=True,
    )
    lines = text.splitlines()
    tags = (
        "!!str",
        "!!int",
        "!!float",
        "!!bool",
        "!!null",
        "!!set",
        "!!map",
        "!!seq",
        "!!omap",
        "!!pairs",
        "!!binary",
        "!!timestamp",
        "!custom",
        "!!python/tuple",
    )
    for _ in range(random_.randrange(4)):
        index = random_.randrange(len(lines))
        tag = random_.choice(tags)
        line = lines[index]
        if ": " in line and random_.randrange(10) < 3:
            stripped = line.lstrip()
            if not stripped.startswith(("- ", "? ", "<<")):
                indent = line[: len(line) - len(stripped)]
                lines[index] = f"{indent}{tag} {stripped}"
        elif ": " in line:
            lines[index] = line.replace(": ", f": {tag} ", 1)
        elif line.lstrip().startswith("- "):
            lines[index] = line.replace("- ", f"- {tag} ", 1)

    text = "\n".join(lines) + "\n"
    if random_.randrange(5) == 0:
        return "base: &base {p: 1, q: 2}\nroot:\n  <<: *base\n" + "".join(f"  {line}\n" for line in text.splitlines())
    return text


def _fuzz_documents() -> Iterator[str]:
    random_ = random.Random(20260902)
    explicit_tags = (
        "!!str 123",
        "!!int '7'",
        "!!float '1'",
        "!!bool true",
        "!!null null",
        "!!set {key: null}",
        "!!map {key: value}",
        "!!seq [value]",
        "!!omap [{key: value}]",
        "!!pairs [{key: value}]",
        "!!binary aGVsbG8=",
        "!!timestamp 2001-12-14",
        "!custom value",
        "!!python/tuple [value]",
    )
    for index in range(500):
        if index < len(explicit_tags):
            yield f"value: {explicit_tags[index]}\n"
            continue
        value = _fuzz_value(random_)
        if random_.randrange(4) == 0:
            shared = [index, _fuzz_value(random_)]
            value = {"left": shared, "right": shared}
            if random_.randrange(5) == 0:
                value["self"] = value
        yield _render_fuzz_document(random_, value)


def test_fast_constructor_fuzz_parity(tmp_path: Path) -> None:
    """Exercise generated scalars, aliases, cycles, and explicit tags with a fixed seed."""
    expected = tmp_path / "parity.txt"
    documents = tuple(_fuzz_documents())
    expected.write_text((_DATA_PATH / "parity.txt").read_text(encoding="utf-8") * len(documents), encoding="utf-8")
    result = "".join(
        _parity_report(
            _construct(text, get_safe_loader(), standard=True),
            _construct(text, get_safe_loader(), standard=False),
        )
        for text in documents
    )
    assert_output(result, expected)


def test_load_yaml_uses_fast_constructor() -> None:
    """Exercise the production PyYAML ``load_yaml`` path rather than a loader directly."""
    text = (_DATA_PATH / "load_yaml.yaml").read_text(encoding="utf-8")
    standard = _construct(text, get_safe_loader(), standard=True)
    fast = (load_yaml(text), None, [])
    assert_output(_parity_report(standard, fast), _PARITY_OUTPUT)
