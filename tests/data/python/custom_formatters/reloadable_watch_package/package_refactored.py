import os
from pathlib import Path

if marker := os.environ.get("DATAMODEL_CODEGEN_FORMATTER_EXECUTIONS"):
    marker_path = Path(marker)
    with marker_path.open("a", encoding="utf-8") as marker_file:
        marker_file.write("executed\n")

from .new_helper import CodeFormatter
