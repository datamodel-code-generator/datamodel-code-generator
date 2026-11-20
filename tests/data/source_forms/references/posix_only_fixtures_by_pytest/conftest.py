import os

import pytest


@pytest.fixture
def pipe(tmp_path):
    os.mkfifo(tmp_path / "pipe")
    return tmp_path


@pytest.fixture
def pipe_user(pipe):
    return pipe


@pytest.fixture
def portable(tmp_path):
    return tmp_path


@pytest.fixture
def unused(tmp_path):
    os.mkfifo(tmp_path / "unused")
