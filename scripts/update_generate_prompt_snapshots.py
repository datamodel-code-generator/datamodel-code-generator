"""Update generate-prompt stdout snapshots."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent / "tests" / "data" / "expected" / "main_kr" / "generate_prompt"

CASES = (
    ("basic.txt", ["--generate-prompt"]),
    ("with_question.txt", ["--generate-prompt", "How do I convert enums to Literal types?"]),
    (
        "with_options.txt",
        [
            "--input",
            "schema.json",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--snake-case-field",
            "--generate-prompt",
            "What other options should I use?",
        ],
    ),
    ("with_list_options.txt", ["--strict-types", "str", "int", "--generate-prompt"]),
    (
        "json_output.txt",
        [
            "--input",
            "tests/data/jsonschema/person.json",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--no-use-annotated",
            "--strict-types",
            "str",
            "int",
            "--generate-prompt",
            "Which strict Pydantic v2 options should I use?",
            "--output-format",
            "json",
        ],
    ),
)


def _generate_stdout(args: list[str]) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "datamodel_code_generator", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        msg = f"datamodel-codegen exited with {result.returncode} for args: {args}"
        raise RuntimeError(msg)
    return result.stdout


def _update_snapshots(*, check: bool = False) -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    mismatches: list[Path] = []
    for filename, args in CASES:
        output_path = OUTPUT_DIR / filename
        generated = _generate_stdout(list(args))
        if check:
            if not output_path.exists() or output_path.read_text(encoding="utf-8") != generated:
                mismatches.append(output_path)
            continue
        output_path.write_text(generated, encoding="utf-8")

    if mismatches:
        for path in mismatches:
            print(f"Content mismatch: {path}", file=sys.stderr)
        return 1
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update generate-prompt stdout snapshots")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if snapshots are up to date without modifying files",
    )
    return parser.parse_args()


def main() -> int:
    """Parse CLI arguments and update generate-prompt snapshots."""
    args = _parse_args()
    return _update_snapshots(check=args.check)


if __name__ == "__main__":
    sys.exit(main())
