"""Shared pytest fixtures.

Guild configs are persisted as ``<guild_id>.json`` by ``config.py``. Tests
drive the bot with mocked guilds, so a test that reaches any ``get_config()``
call used to leave a real file named after a ``MagicMock`` repr in the repo's
``configs/`` directory. Point the config directory at a throwaway location for
the whole test session so that can never happen again.

The environment variable has to be set at conftest *import* time: ``config.py``
builds its module-level ``bot_config`` singleton on import (which creates the
directory), and test modules import ``config`` before any fixture runs.
"""
import os
import shutil
import tempfile
from pathlib import Path

import pytest

# Session-wide fallback, in effect before per-test fixtures take over.
_SESSION_CONFIG_DIR = Path(tempfile.mkdtemp(prefix="draftbot-test-configs-"))
os.environ["DRAFTBOT_CONFIG_DIR"] = str(_SESSION_CONFIG_DIR)


@pytest.fixture(scope="session", autouse=True)
def _cleanup_session_config_dir():
    yield
    shutil.rmtree(_SESSION_CONFIG_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch):
    """Give each test its own config directory, and yield it.

    Tests that want to inspect what was written can request ``config_dir``
    directly; everything else just gets the isolation for free.
    """
    path = tmp_path / "configs"
    path.mkdir()
    monkeypatch.setenv("DRAFTBOT_CONFIG_DIR", str(path))
    return path
