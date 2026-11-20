import os

import pytest


@pytest.fixture(autouse=True)
def pipe_everywhere(tmp_path):
    os.mkfifo(tmp_path / "pipe")
