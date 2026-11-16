"""``--read-ids-with-empty-fragment``: an `$id` that ends with an empty fragment names the document that declares it.

JSON Schema draft 7 allows the root `$id` with or without an empty fragment (section 8.2.1); both name one resource.
Up to 0.96.0 the generator records the `$id` as written, and looks a reference up without its fragment, so a `$ref`
to the document's own `$id` written with `#` loads the document at that URL from the network (#6878): the run fails
where the server returns a copy the generator refuses, where the output is one file and the fetched copy is a second
module, and where nothing can be fetched; with an output directory it writes the fetched copy as a second module.
Under the option the reference resolves inside the document. Without it a run that loads such a document warns,
before the fetch, and names the option.
"""

from __future__ import annotations

import sys
import warnings
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator.__main__ import Exit
from tests.conftest import MockHttpxResponse, assert_httpx_get_kwargs, assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import HttpxGetMockFactory

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "ids_with_empty_fragment"
OPTION = "--read-ids-with-empty-fragment"
URL = "http://www.iptc.org/std/ninjs/ninjs-schema_{version}.json"
REFUSED_COPY = '{"type": "object", "properties": {"uri": {"type": "string", "required": true}}}'
"""A remote copy the generator refuses: an older text of the schema, with ``required: true`` inside a property."""
READABLE_COPY = '{"$schema": "http://json-schema.org/draft-04/schema#", "type": "object", "title": "NewsItem"}'
"""A remote copy the generator reads, which becomes a module of its own."""
UNRELATED_OUTPUT = ("DeprecationWarning: Pydantic v2 with --use-annotated is recommended",)
"""The lines the stored text leaves out: the notice every Pydantic v2 run without --use-annotated shows."""


@pytest.fixture
def warnings_on_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Show every warning, as ``<category>: <message>`` on stderr, in the order the run shows it."""
    warnings.simplefilter("always")

    def show(message: Warning | str, category: type[Warning], *_: object, **__: object) -> None:
        sys.stderr.write(f"{category.__name__}: {message}\n")

    monkeypatch.setattr(warnings, "showwarning", show)


@pytest.mark.cli_doc(
    options=[OPTION],
    option_description="""Read an `$id` that ends with an empty fragment as the document that declares it.

JSON Schema draft 7 allows the root `$id` to be written with or without an empty fragment
(`https://example.com/item.json#` or `https://example.com/item.json`); both name the same resource. Without this
option the generator records the `$id` as written, so a `$ref` to it in the same document
(`https://example.com/item.json#`, or a pointer after it) loads the document at that URL from the network: the run
fails where the server returns a copy the generator refuses, where nothing can be fetched, and where the output is
one file and the fetched copy is a second module; with an output directory the fetched copy is written as a second
module, as in 0.96.0. Such a run warns before it loads the document and names this option. With
`--read-ids-with-empty-fragment` the `$id` is read as the URI without the empty fragment, so the reference resolves
inside the document and nothing is fetched.""",
    input_schema="jsonschema/ids_with_empty_fragment/ninjs_1_0.json",
    cli_args=[OPTION],
    golden_output="main/jsonschema/ids_with_empty_fragment/ids_with_empty_fragment[1.0-file-option].py",
    related_options=["--allow-remote-refs"],
)
@pytest.mark.usefixtures("warnings_on_stderr")
@pytest.mark.parametrize(
    ("version", "output", "option", "remote", "expected_exit"),
    [
        pytest.param("1.0", "file", False, REFUSED_COPY, Exit.ERROR, id="1.0-file"),
        pytest.param("1.0", "directory", False, REFUSED_COPY, Exit.ERROR, id="1.0-directory"),
        pytest.param("1.1", "file", False, READABLE_COPY, Exit.ERROR, id="1.1-file"),
        pytest.param("1.1", "directory", False, READABLE_COPY, Exit.OK, id="1.1-directory"),
        pytest.param("1.0", "file", True, None, Exit.OK, id="1.0-file-option"),
        pytest.param("1.0", "directory", True, None, Exit.OK, id="1.0-directory-option"),
        pytest.param("1.1", "file", True, None, Exit.OK, id="1.1-file-option"),
        pytest.param("1.1", "directory", True, None, Exit.OK, id="1.1-directory-option"),
    ],
)
def test_ids_with_empty_fragment(
    version: str,
    output: str,
    *,
    option: bool,
    remote: str | None,
    expected_exit: Exit,
    mock_httpx_get: HttpxGetMockFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A `$ref` to the document's own `$id` written with an empty fragment (#6878), the shapes of the issue.

    Without the option the document at the URL is loaded, served here: the refused copy of 1.0 fails both output
    kinds, the readable copy of 1.1 fails a single output file and is written as a second module of a directory, and
    each run warns before the fetch and names the option. With the option nothing is fetched and every run writes the
    one model the document holds, also into a directory (one module, written as a file). The stored text is what the
    run printed, its exit status and the files it wrote.
    """
    url = URL.format(version=version)
    httpx_get_mock = mock_httpx_get(*([] if remote is None else [MockHttpxResponse(url, remote)]))
    output_path = tmp_path / "out" / ("model.py" if output == "file" else "models")
    run_main_and_assert(
        input_path=INPUT_PATH / f"ninjs_{version.replace('.', '_')}.json",
        output_path=output_path,
        input_file_type="jsonschema",
        extra_args=[
            "--formatters",
            "builtin",
            "--disable-timestamp",
            "--allow-remote-refs",
            *([OPTION] if option else []),
        ],
        expected_exit=expected_exit,
        **({} if expected_exit == Exit.OK else {"skip_code_validation": True}),
    )
    if remote is None:
        assert_httpx_get_kwargs(httpx_get_mock, called=False)
    else:
        assert_httpx_get_kwargs(httpx_get_mock, expected_url=url, call_count=1)
    lines = capsys.readouterr().err.splitlines(keepends=True)
    written = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file())
    assert_output(
        "".join(line for line in lines if not line.startswith(UNRELATED_OUTPUT) and "errors.pydantic.dev" not in line)
        + f"exit: {expected_exit.name}\nwritten:\n"
        + "".join(f"  {name}\n" for name in written)
    )


@pytest.mark.parametrize("option", [False, True], ids=["default", "option"])
def test_ids_without_empty_fragment(option: bool, output_file: Path, mock_httpx_get: HttpxGetMockFactory) -> None:
    """A `$ref` to the document's own `$id` written without a fragment resolves inside it, with or without the option.

    Nothing is fetched, and the option changes nothing (#6878).
    """
    httpx_get_mock = mock_httpx_get()
    run_main_and_assert(
        input_path=INPUT_PATH / "ninjs_no_fragment.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--disable-timestamp", *([OPTION] if option else [])],
    )
    assert_httpx_get_kwargs(httpx_get_mock, called=False)
