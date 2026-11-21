"""Paths below the stored expected output, built through a constant, unpacking, loops, a with item and a walrus."""

from pathlib import Path

EXPECTED = Path(__file__).parent / "data" / "expected"
TEXTS = [path.read_text() for path in [EXPECTED / "f.txt"]]


def test_reads():
    first, other = EXPECTED / "a.txt", Path("b.txt")
    first.read_text()
    other.read_text()
    for item in [EXPECTED / "c.txt"]:
        item.read_bytes()
    with EXPECTED / "d.txt" as handle:
        handle.read_text()
    (found := EXPECTED / "e.txt").read_text()
    sorted(EXPECTED.glob("*.py"))
    return found
