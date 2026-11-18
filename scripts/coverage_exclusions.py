"""List the lines excluded from coverage (`pragma: no cover` and the other configured forms) that the suite ran."""

from __future__ import annotations

import sys
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

from coverage import Coverage
from coverage.exceptions import CoverageException
from coverage.misc import join_regex
from coverage.python import PythonParser


def _area(path: str) -> str:
    """Name the package directory below `src/datamodel_code_generator`, or the first directory elsewhere."""
    directories = Path(path).parts[:-1]
    package = directories[2:] if directories[:2] == ("src", "datamodel_code_generator") else directories
    return package[0] if package else "(top level)"


def executed_exclusions(data_file: Path) -> list[tuple[str, int, int, str]]:
    """List `(file, line, executed lines, source)` of each excluded region that ran, using the configured patterns.

    The patterns come from the coverage configuration with its plugins, so `pragma: no cover` and the other
    exclusion forms count alike. A region of several lines counts its lines after the marked one (a `def` or `if`
    header runs whenever the module does); a one-line region counts that line.
    """
    session = Coverage(data_file=str(data_file))
    session.load()
    data = session.get_data()
    exclude = join_regex(session.config.exclude_list)
    found = []
    for file in sorted(data.measured_files()):
        try:
            parser = PythonParser(filename=file, exclude=exclude)
            parser.parse_source()
        except CoverageException:
            continue
        marks = sorted(parser.raw_excluded)
        regions = defaultdict(list)
        for line in sorted(parser.excluded):
            regions[marks[max(bisect_right(marks, line) - 1, 0)]].append(line)
        lines = parser.text.splitlines()
        executed = set(data.lines(file) or ())
        found.extend(
            (file, mark, len(ran), lines[mark - 1].strip())
            for mark, region in regions.items()
            if (ran := [line for line in region if line in executed and (line > mark or len(region) == 1)])
        )
    return found


def main(argv: list[str] | None = None) -> None:
    """Print the executed exclusions of the coverage data file given as the only argument (report only)."""
    (data_file,) = sys.argv[1:] if argv is None else argv
    found = executed_exclusions(Path(data_file))
    print(f"Executed coverage exclusions: {len(found)}")
    for file, line, ran, source in found:
        print(f"{file}:{line} [{_area(file)}] {ran} line(s) ran: {source}")


if __name__ == "__main__":
    main()
