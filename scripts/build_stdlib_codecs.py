"""Derive the dataclass and TypedDict codec runtime modules from their one Jinja source.

A generated client copies the codec module of its own model backend only, so each module holds no branch of the
other backend. Both are rendered from `stdlib.py.jinja2` and checked in; `--check` reports a module that differs
from what its source derives.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from jinja2 import Environment, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/datamodel_code_generator/_runtime/model_codecs/stdlib.py.jinja2"
BACKENDS = {"stdlib_dataclasses.py": "dataclass", "stdlib_typeddicts.py": "typeddict"}


def derived_sources() -> dict[Path, str]:
    """Render the codec module of each backend from the one source, in memory."""
    environment = Environment(
        autoescape=select_autoescape(["html", "xml"], default_for_string=False),
        keep_trailing_newline=True,
        lstrip_blocks=True,
        trim_blocks=True,
        undefined=StrictUndefined,
    )
    template = environment.from_string(SOURCE.read_text(encoding="utf-8"))
    return {SOURCE.with_name(name): template.render(backend=backend) for name, backend in BACKENDS.items()}


def main(argv: list[str] | None = None) -> int:
    """Write the derived modules, or report those that differ from what the source derives."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if a derived codec module is stale")
    args = parser.parse_args(argv)
    sources = derived_sources()
    if not args.check:
        for path, source in sources.items():
            path.write_text(source, encoding="utf-8", newline="\n")
        return 0
    stale = [
        path for path, source in sources.items() if not path.is_file() or path.read_text(encoding="utf-8") != source
    ]
    for path in stale:
        print(path.relative_to(ROOT).as_posix(), file=sys.stderr)
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
