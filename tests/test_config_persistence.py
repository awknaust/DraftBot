"""Guild config files must only ever be named after a real guild snowflake.

Regression coverage for mocked guild IDs (``<MagicMock name='mock.guild.id'
id='...'>``) leaking out of the test suite and into the repo's ``configs/``
directory.
"""
import json
from unittest.mock import MagicMock

import pytest

import config as config_module
from config import (
    Config,
    get_config,
    get_config_dir,
    is_valid_guild_id,
    save_config,
    update_setting,
)


VALID_GUILD_ID = "1504287716890771556"


@pytest.fixture
def fresh_config(config_dir):
    """A Config instance rooted at the per-test config directory."""
    return Config()


@pytest.mark.parametrize("guild_id", ["123", 123, VALID_GUILD_ID, int(VALID_GUILD_ID)])
def test_accepts_snowflakes(guild_id):
    assert is_valid_guild_id(guild_id) is True


@pytest.mark.parametrize(
    "guild_id",
    [
        "",
        "abc",
        "12a",
        "12.3",
        "-123",
        "../escape",
        "١٢٣",  # non-ASCII digits: str.isdigit() alone would accept these
        MagicMock(),
        None,
    ],
)
def test_rejects_non_snowflakes(guild_id):
    assert is_valid_guild_id(guild_id) is False


def test_mocked_guild_id_writes_nothing(fresh_config, config_dir):
    interaction = MagicMock()

    fresh_config.get_guild_config(interaction.guild.id)

    assert list(config_dir.iterdir()) == []


def test_mocked_guild_id_still_returns_usable_config(fresh_config):
    interaction = MagicMock()

    guild_config = fresh_config.get_guild_config(interaction.guild.id)

    assert guild_config["timezone"] == "US/Eastern"


def test_save_config_refuses_mock_and_reports_failure(fresh_config, config_dir):
    guild_id = str(MagicMock())
    fresh_config.configs[guild_id] = {"timezone": "US/Eastern"}

    assert fresh_config.save_config(guild_id) is False
    assert list(config_dir.iterdir()) == []


def test_valid_guild_id_is_written_to_config_dir(fresh_config, config_dir):
    fresh_config.get_guild_config(VALID_GUILD_ID)

    written = config_dir / f"{VALID_GUILD_ID}.json"
    assert written.exists()
    assert json.loads(written.read_text())["timezone"] == "US/Eastern"


def test_load_configs_skips_junk_filenames(config_dir):
    (config_dir / f"{VALID_GUILD_ID}.json").write_text(json.dumps({"timezone": "UTC"}))
    junk = "<MagicMock name='mock.guild.id' id='4413817040'>.json"
    (config_dir / junk).write_text(json.dumps({"timezone": "UTC"}))

    loaded = Config().configs

    assert VALID_GUILD_ID in loaded
    assert not any("MagicMock" in key for key in loaded)


def test_config_dir_follows_env_var(config_dir, monkeypatch, tmp_path):
    assert get_config_dir() == config_dir

    elsewhere = tmp_path / "somewhere-else"
    monkeypatch.setenv("DRAFTBOT_CONFIG_DIR", str(elsewhere))
    assert get_config_dir() == elsewhere


def test_module_level_helpers_respect_the_guard(config_dir, monkeypatch):
    monkeypatch.setattr(config_module, "bot_config", Config())
    guild_id = str(MagicMock())

    assert save_config(guild_id, {"timezone": "US/Eastern"}) is False
    assert update_setting(guild_id, "timezone", "UTC") is False
    # The in-memory value still updated, so callers keep working.
    assert get_config(guild_id)["timezone"] == "UTC"
    assert list(config_dir.iterdir()) == []
