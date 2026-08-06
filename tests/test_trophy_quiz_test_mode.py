"""TEST_MODE-only local-simulation paths for the trophy quiz:
the Spaces -> DraftSession.draft_data fallback, the MPT placeholder deck link,
and the seed script's builders feeding the real pipeline."""

import os
import random
import tempfile
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

import cogs.trophy_quiz_commands as trophy_quiz_commands
from database.db_session import AsyncSessionLocal
from database.models_base import Base
from helpers.magicprotools_helper import MagicProtoolsHelper
from models import DraftSession, MatchResult, TrophyQuizSession
from scripts.seed_test_trophy_quiz import (
    DRAFTER_NAMES,
    MATCH_TABLE,
    build_draft_log,
    build_sign_ups,
)
from services.draft_log_store import build_mtgo_deck_text, map_discord_to_draftmancer, split_decklist
from services.trophy_quiz_service import select_two_decks


@pytest_asyncio.fixture
async def test_db():
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db'); tmp.close()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp.name}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    AsyncSessionLocal.configure(bind=engine)
    yield engine
    await engine.dispose(); os.unlink(tmp.name)


_DISCORD_IDS = [str(900000000000000100 + i) for i in range(6)]


def _build_log(session_id="test-trophy-abc12345"):
    return build_draft_log(session_id, _DISCORD_IDS, DRAFTER_NAMES, random.Random(0))


async def _seed(session_id, guild_id, log):
    """Mirror of the seed script's DB insert, against the test engine."""
    async with AsyncSessionLocal() as s:
        async with s.begin():
            s.add(DraftSession(
                session_id=session_id, guild_id=guild_id, session_type="random",
                draft_start_time=datetime.now(),
                sign_ups=build_sign_ups(_DISCORD_IDS, DRAFTER_NAMES),
                cube="TestCube", spaces_object_key=f"test/{session_id}.json",
                draft_data=log, data_received=True,
            ))
            for i, (p1, p2, w) in enumerate(MATCH_TABLE):
                s.add(MatchResult(
                    session_id=session_id, match_number=i + 1,
                    player1_id=_DISCORD_IDS[p1], player2_id=_DISCORD_IDS[p2],
                    winner_id=_DISCORD_IDS[w], guild_id=guild_id,
                    result_submitted_at=datetime.now(),
                ))


class _FakeChannel:
    def __init__(self, channel_id=999):
        self.id = channel_id
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        message = AsyncMock()
        message.id = 555
        message.pin = AsyncMock()
        return message


def _fake_jpeg():
    buf = BytesIO()
    buf.write(b"\xff\xd8fakejpeg")
    buf.seek(0)
    return buf


# ---- Spaces -> draft_data fallback ------------------------------------------

@pytest.mark.asyncio
async def test_select_falls_back_to_db_draft_data_in_test_mode(test_db):
    log = _build_log()
    await _seed("test-trophy-abc12345", "g1", log)

    with patch("cogs.trophy_quiz_commands.load_from_spaces", AsyncMock(return_value=None)), \
         patch("cogs.trophy_quiz_commands.is_test_mode", return_value=True):
        draft, decks, draft_data = await trophy_quiz_commands._select_eligible_draft(
            "g1", rng=random.Random(0))

    assert draft is not None and draft.session_id == "test-trophy-abc12345"
    assert decks is not None and len(decks) == 2
    assert draft_data == log                      # the DB copy, not Spaces


@pytest.mark.asyncio
async def test_select_does_not_fall_back_outside_test_mode(test_db):
    """Locks prod behavior: without TEST_MODE, a Spaces miss still skips the draft."""
    await _seed("test-trophy-abc12345", "g1", _build_log())

    with patch("cogs.trophy_quiz_commands.load_from_spaces", AsyncMock(return_value=None)), \
         patch("cogs.trophy_quiz_commands.is_test_mode", return_value=False):
        draft, decks, draft_data = await trophy_quiz_commands._select_eligible_draft(
            "g1", rng=random.Random(0))

    assert (draft, decks, draft_data) == (None, None, None)


# ---- MPT placeholder ---------------------------------------------------------

@pytest.mark.asyncio
async def test_mpt_placeholder_in_test_mode(test_db):
    """No MPT_API_KEY (submit returns None): in TEST_MODE the quiz still posts,
    with well-formed https placeholder deck links persisted on the decks.
    (The non-test abort is locked by test_trophy_quiz_command.py.)"""
    log = _build_log()
    await _seed("test-trophy-abc12345", "g1", log)

    with patch("cogs.trophy_quiz_commands.load_from_spaces", AsyncMock(return_value=None)), \
         patch("cogs.trophy_quiz_commands.is_test_mode", return_value=True):
        draft, deck_pair, draft_data = await trophy_quiz_commands._select_eligible_draft(
            "g1", rng=random.Random(0))
    assert draft is not None

    cog = trophy_quiz_commands.TrophyQuizCommands(bot=None)
    channel = _FakeChannel()
    with patch("helpers.magicprotools_helper.MagicProtoolsHelper.submit_deck_view",
               AsyncMock(return_value=None)), \
         patch("cogs.trophy_quiz_commands.PileImageBuilder.build",
               AsyncMock(side_effect=lambda *a, **k: _fake_jpeg())), \
         patch("cogs.trophy_quiz_commands.is_test_mode", return_value=True):
        message = await cog._create_and_post_trophy_quiz(
            guild_id="g1", channel=channel, draft_session=draft,
            deck_pair=deck_pair, posted_by="mod", draft_data=draft_data,
        )

    assert message is not None
    assert len(channel.sent) == 1
    async with AsyncSessionLocal() as s:
        quiz = (await s.execute(select(TrophyQuizSession))).scalar_one()
    assert all(d["mpt_url"].startswith("https://") for d in quiz.decks)


# ---- seed builders satisfy the real pipeline ---------------------------------

def test_seed_builder_output_passes_pipeline():
    log = _build_log()
    sign_ups = build_sign_ups(_DISCORD_IDS, DRAFTER_NAMES)

    # seat-order alignment: sign-up i maps to draftmancer seat i
    mapping = map_discord_to_draftmancer(log, sign_ups)
    assert [mapping[uid] for uid in _DISCORD_IDS] == [f"dm-{i}" for i in range(6)]

    match_results = [
        SimpleNamespace(player1_id=_DISCORD_IDS[p1], player2_id=_DISCORD_IDS[p2],
                        winner_id=_DISCORD_IDS[w])
        for p1, p2, w in MATCH_TABLE
    ]
    pair = select_two_decks(log, sign_ups, match_results, random.Random(0))
    assert pair is not None and len(pair) == 2
    assert all(deck["pool"] for deck in pair)

    # every drafter yields a buildable deck + a convertible MPT draft (covers
    # the case where a developer DOES have a real MPT_API_KEY locally)
    mpt = MagicProtoolsHelper()
    for i in range(6):
        dm_id = f"dm-{i}"
        split = split_decklist(log, dm_id)
        assert split["main"]
        assert build_mtgo_deck_text(split, log["carddata"])
        assert mpt.convert_to_magicprotools_format(log, dm_id)

    for card in log["carddata"].values():
        assert {"name", "cmc", "mana_cost"} <= card.keys()
