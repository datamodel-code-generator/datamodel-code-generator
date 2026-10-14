"""GC threshold scoping tests for public code generation."""

from __future__ import annotations

import gc
import json
import threading
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

import pytest

from datamodel_code_generator import _GC_YOUNG_THRESHOLD, InputFileType, _tuned_gc, generate
from tests.conftest import assert_generated_file_matches_output, assert_output, create_assert_file_content
from tests.main.conftest import (
    DATA_PATH,
    EXPECTED_JSON_SCHEMA_PATH,
    JSON_SCHEMA_DATA_PATH,
    run_generate_file_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path


assert_file_content = create_assert_file_content(EXPECTED_JSON_SCHEMA_PATH)
_GC_TUNING_DATA_PATH = DATA_PATH / "gc_tuning"


class _ThresholdRecordingMapping(Mapping[str, Any]):
    """Record the GC threshold when generation copies mapping input."""

    def __init__(self, data: Mapping[str, Any], thresholds: list[tuple[int, int, int]]) -> None:
        self._data = data
        self._thresholds = thresholds

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        self._thresholds.append(gc.get_threshold())
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


@pytest.fixture
def gc_state() -> Iterator[tuple[int, int, int]]:
    """Restore the process-global GC state after each test."""
    threshold = gc.get_threshold()
    gc.enable()
    try:
        yield threshold
    finally:
        gc.set_threshold(*threshold)
        gc.enable()


def test_generate_scopes_tuned_gc_for_e2e_mapping_input(gc_state: tuple[int, int, int], tmp_path: Path) -> None:
    """Generate the checked-in schema while its mapping copy observes the tuned threshold."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "person.json").read_text(encoding="utf-8"))
    observed_thresholds: list[tuple[int, int, int]] = []
    recording_input = _ThresholdRecordingMapping(schema, observed_thresholds)
    gc.disable()
    try:
        with _tuned_gc():
            disabled_scope_unchanged = gc.get_threshold() == gc_state
    finally:
        gc.enable()

    returned = generate(
        recording_input,
        input_file_type=InputFileType.JsonSchema,
        input_filename="person.json",
        disable_timestamp=True,
    )
    output_path = tmp_path / "person.py"
    generate(
        recording_input,
        input_file_type=InputFileType.JsonSchema,
        input_filename="person.json",
        output=output_path,
        disable_timestamp=True,
    )
    assert_generated_file_matches_output(returned, output_path)
    run_generate_file_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=tmp_path / "expected-person.py",
        input_file_type=InputFileType.JsonSchema,
        assert_func=assert_file_content,
        expected_file="general.py",
    )

    assert_output(
        "\n".join((
            f"mapping_size={len(recording_input)}",
            f"disabled_scope_unchanged={disabled_scope_unchanged}",
            f"tuned_during_generation={(_GC_YOUNG_THRESHOLD, *gc_state[1:]) in observed_thresholds}",
            f"restored_after_generation={gc.get_threshold() == gc_state}",
            "",
        )),
        _GC_TUNING_DATA_PATH / "generate.txt",
    )


def test_generate_restores_gc_threshold_after_exception(gc_state: tuple[int, int, int], mocker: Any) -> None:
    """Restore the threshold when the generation parser raises."""
    mocker.patch("datamodel_code_generator._parse_generation", side_effect=RuntimeError("parse failure"))

    with pytest.raises(RuntimeError, match="parse failure"):
        generate(
            {"title": "Broken", "type": "object"},
            input_file_type=InputFileType.JsonSchema,
        )

    assert_output(
        f"restored_after_exception={gc.get_threshold() == gc_state}\n",
        _GC_TUNING_DATA_PATH / "exception.txt",
    )


def test_tuned_gc_keeps_nested_scope_tuned_until_outer_exit(gc_state: tuple[int, int, int]) -> None:
    """Only the outermost nested scope owns threshold restoration."""
    tuned_threshold = (_GC_YOUNG_THRESHOLD, *gc_state[1:])
    observations: list[str] = []

    with _tuned_gc():
        observations.append(f"outer_entered={gc.get_threshold() == tuned_threshold}")
        with _tuned_gc():
            observations.append(f"inner_entered={gc.get_threshold() == tuned_threshold}")
        observations.append(f"inner_exited={gc.get_threshold() == tuned_threshold}")

    observations.append(f"outer_exited={gc.get_threshold() == gc_state}")
    assert_output("\n".join((*observations, "")), _GC_TUNING_DATA_PATH / "nested.txt")


def test_tuned_gc_keeps_concurrent_scope_tuned_until_all_workers_exit(gc_state: tuple[int, int, int]) -> None:
    """One concurrent exit must not restore a threshold held by another generation."""
    entered = threading.Barrier(3)
    release_first = threading.Event()
    release_second = threading.Event()
    first_exited = threading.Event()

    def run_first() -> None:
        with _tuned_gc():
            entered.wait(timeout=5)
            release_first.wait(timeout=5)
        first_exited.set()

    def run_second() -> None:
        with _tuned_gc():
            entered.wait(timeout=5)
            release_second.wait(timeout=5)

    first = threading.Thread(target=run_first)
    second = threading.Thread(target=run_second)
    first.start()
    second.start()
    observations: list[str] = []
    try:
        entered.wait(timeout=5)
        observations.append(f"both_entered={gc.get_threshold() == (_GC_YOUNG_THRESHOLD, *gc_state[1:])}")

        release_first.set()
        observations.extend((
            f"first_exited={first_exited.wait(timeout=5)}",
            f"second_still_holds={gc.get_threshold() == (_GC_YOUNG_THRESHOLD, *gc_state[1:])}",
        ))
    finally:
        release_first.set()
        release_second.set()
        first.join(timeout=5)
        second.join(timeout=5)

    observations.extend((
        f"first_joined={not first.is_alive()}",
        f"second_joined={not second.is_alive()}",
        f"restored_after_workers={gc.get_threshold() == gc_state}",
    ))
    assert_output("\n".join((*observations, "")), _GC_TUNING_DATA_PATH / "concurrent.txt")


def test_tuned_gc_leaves_disabled_host_unchanged(gc_state: tuple[int, int, int]) -> None:
    """Do not alter the threshold or enablement state when the host disabled GC."""
    gc.disable()
    observations: list[str] = []
    try:
        with _tuned_gc():
            observations.extend((
                f"threshold_unchanged_inside={gc.get_threshold() == gc_state}",
                f"disabled_inside={not gc.isenabled()}",
            ))
            with _tuned_gc():
                observations.extend((
                    f"threshold_unchanged_nested={gc.get_threshold() == gc_state}",
                    f"disabled_nested={not gc.isenabled()}",
                ))
        observations.extend((
            f"threshold_unchanged_after={gc.get_threshold() == gc_state}",
            f"disabled_after={not gc.isenabled()}",
        ))
    finally:
        gc.enable()

    assert_output("\n".join((*observations, "")), _GC_TUNING_DATA_PATH / "disabled.txt")


@pytest.mark.abnormal_path("CPython always provides gc.get_threshold; PyPy's gc module does not")
def test_generate_without_gc_threshold_api(
    gc_state: tuple[int, int, int], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Generate on a runtime such as PyPy whose gc module has no threshold API."""
    with monkeypatch.context() as patch:
        patch.delattr(gc, "get_threshold")
        patch.delattr(gc, "set_threshold")
        run_generate_file_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "person.json",
            output_path=tmp_path / "person.py",
            input_file_type=InputFileType.JsonSchema,
            assert_func=assert_file_content,
            expected_file="general.py",
        )

    with _tuned_gc():
        tuned_after_api_returns = gc.get_threshold() == (_GC_YOUNG_THRESHOLD, *gc_state[1:])

    assert_output(
        "\n".join((
            f"tuned_after_api_returns={tuned_after_api_returns}",
            f"restored_after_generation={gc.get_threshold() == gc_state}",
            "",
        )),
        _GC_TUNING_DATA_PATH / "no_threshold_api.txt",
    )
