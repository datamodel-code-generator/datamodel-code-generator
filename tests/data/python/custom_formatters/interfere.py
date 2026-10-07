from pathlib import Path

from datamodel_code_generator.format import CustomCodeFormatter


class CodeFormatter(CustomCodeFormatter):
    """Keep the code, appending to the published manifest of the server package as another writer would."""

    def apply(self, code: str) -> str:
        with Path("server/.dcg-target-manifest.json").open("ab") as handle:
            handle.write(b" ")
        return code
