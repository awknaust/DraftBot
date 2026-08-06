#!/usr/bin/env python3
"""Inject completed quiz HISTORY (both quiz types) so /stats and the quiz
leaderboards have data to show — unlike seed_test_trophy_quiz.py, which seeds a
draft for POSTING a new quiz, this seeds already-played quizzes.

For each seeded quiz (pick + trophy) it writes the same rows the real flows
write: QuizSession + QuizSubmission with QuizStats updated via the production
QuizStats.update_stats(), and TrophyQuizSession + finalized TrophyQuizSubmission
scored with the production score_submission(). Your target user plus three
synthetic test players participate in every quiz (with different skill levels),
so leaderboards rank meaningfully. Submissions are spread over the past ~60
days so the 14d/30d/90d leaderboard views differ.

Usage (from the repo root the bot runs from — drafts.db is a relative path):

    pipenv run python scripts/seed_test_quiz_stats.py --guild-id <TEST_GUILD_ID> --user-id <YOUR_DISCORD_ID>
    pipenv run python scripts/seed_test_quiz_stats.py --guild-id <TEST_GUILD_ID> --user-id <YOUR_DISCORD_ID> --pick 20 --trophy 15
    pipenv run python scripts/seed_test_quiz_stats.py --purge

--purge deletes every seeded quiz/submission (quiz_id test-qstats-*) and
rebuilds each affected player's QuizStats from their remaining submissions.
Requires TEST_MODE=true; refuses otherwise. Safe while the bot is running.
"""

import argparse
import asyncio
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

# Add parent directory to path to import project modules
sys.path.append(str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

QUIZ_PREFIX = "test-qstats-"

# (player suffix/name, per-pick and per-deck success probability). The target
# user is inserted at the front with 0.55 — mid-pack, so they're neither first
# nor last on the leaderboards.
SYNTHETIC_PLAYERS = [
    ("900000000000000100", "TestAlice", 0.75),
    ("900000000000000101", "TestBob", 0.45),
    ("900000000000000102", "TestCarol", 0.25),
]

PICK_WEIGHTS = [2, 3, 4, 5]  # exact-match points for picks 1-4 (mirrors the pick quiz)


async def _next_display_id(session, model, guild_id: str) -> int:
    from sqlalchemy import func, select
    result = await session.execute(
        select(func.max(model.display_id)).where(model.guild_id == guild_id))
    return (result.scalar() or 0) + 1


def _pick_submission_kwargs(rng: random.Random, hit_rate: float) -> dict:
    """Randomized-but-self-consistent pick quiz result columns."""
    results = [rng.random() < hit_rate for _ in range(4)]
    points = [w if hit else 0 for w, hit in zip(PICK_WEIGHTS, results)]
    return {
        "guesses": [f"guess-{i}" for i in range(4)],
        "correct_count": sum(results),
        **{f"pick_{i + 1}_correct": results[i] for i in range(4)},
        "points_earned": sum(points),
        **{f"pick_{i + 1}_points": points[i] for i in range(4)},
    }


def _trophy_guess(rng: random.Random, actual: list, hit_rate: float) -> list:
    """A guess whose quality tracks hit_rate: good players usually call the
    direction right, and when they do, often nail the exact records too."""
    if rng.random() >= hit_rate:            # direction wrong: swapped the decks
        return list(reversed(actual))
    if rng.random() < hit_rate:             # direction + exact records right
        return list(actual)
    # right direction, wrong records: another better-first (or worse-first) pair
    better_first = actual[0] > actual[1]
    options = [[3, 0], [2, 1], [3, 1], [2, 0]] if better_first else [[0, 3], [1, 2], [1, 3], [0, 2]]
    return rng.choice([g for g in options if g != list(actual)])


async def seed(guild_id: str, user_id: str, pick_count: int, trophy_count: int) -> None:
    from sqlalchemy import select
    from database.db_session import AsyncSessionLocal, engine
    from database.models_base import Base
    import models  # noqa: F401 — registers every table on Base for create_all
    from models import (QuizSession, QuizStats, QuizSubmission,
                        TrophyQuizSession, TrophyQuizSubmission)
    from services.trophy_quiz_service import apply_change_cost, score_submission

    players = [(str(user_id), "You", 0.55)] + SYNTHETIC_PLAYERS
    rng = random.Random()

    if not Path("drafts.db").exists():
        print("⚠ drafts.db not found in the current directory — creating a new one.")
        print("  Run this script from the repo root the bot runs from, or the bot won't see the seed.")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        async with session.begin():
            pick_display = await _next_display_id(session, QuizSession, str(guild_id))
            trophy_display = await _next_display_id(session, TrophyQuizSession, str(guild_id))

            # Pick quizzes: one QuizSession + a submission per player, oldest
            # first so QuizStats streaks replay in chronological order.
            for i in range(pick_count):
                quiz_id = f"{QUIZ_PREFIX}pick-{uuid4().hex[:8]}"
                when = datetime.now() - timedelta(days=60 - i * (60 / max(pick_count, 1)),
                                                  hours=rng.randint(0, 12))
                session.add(QuizSession(
                    quiz_id=quiz_id, display_id=pick_display + i, guild_id=str(guild_id),
                    channel_id="0", draft_session_id=f"{QUIZ_PREFIX}draft",
                    pack_trace_data={"picks": []}, correct_answers=[f"answer-{n}" for n in range(4)],
                    posted_by="seed-script", posted_at=when, total_participants=len(players),
                ))
                for pid, name, hit_rate in players:
                    kwargs = _pick_submission_kwargs(rng, hit_rate)
                    session.add(QuizSubmission(
                        quiz_id=quiz_id, player_id=pid, display_name=name,
                        submitted_at=when, **kwargs))
                    stats = (await session.execute(select(QuizStats).where(
                        QuizStats.player_id == pid, QuizStats.guild_id == str(guild_id)
                    ))).scalar_one_or_none()
                    if not stats:
                        stats = QuizStats(player_id=pid, guild_id=str(guild_id), display_name=name)
                        session.add(stats)
                    stats.update_stats(kwargs["correct_count"], kwargs["points_earned"])
                    stats.last_quiz_time = when

            # Trophy quizzes: one session + a finalized, production-scored
            # submission per player; occasionally a paid answer change.
            for i in range(trophy_count):
                quiz_id = f"{QUIZ_PREFIX}trophy-{uuid4().hex[:8]}"
                when = datetime.now() - timedelta(days=60 - i * (60 / max(trophy_count, 1)),
                                                  hours=rng.randint(0, 12))
                actual = rng.choice([[3, 0], [0, 3], [2, 1], [1, 2], [3, 1], [1, 3]])
                session.add(TrophyQuizSession(
                    quiz_id=quiz_id, display_id=trophy_display + i, guild_id=str(guild_id),
                    channel_id="0", draft_session_id=f"{QUIZ_PREFIX}d-{i}",
                    decks=[{"slot": "A", "drafter_id": SYNTHETIC_PLAYERS[0][0], "wins": actual[0]},
                           {"slot": "B", "drafter_id": SYNTHETIC_PLAYERS[1][0], "wins": actual[1]}],
                    posted_by="seed-script", total_participants=len(players),
                ))
                for pid, name, hit_rate in players:
                    guesses = _trophy_guess(rng, actual, hit_rate)
                    result = score_submission(guesses, actual)
                    changed = rng.random() < 0.15
                    session.add(TrophyQuizSubmission(
                        quiz_id=quiz_id, player_id=pid, display_name=name,
                        guesses=guesses, direction_correct=result["direction_correct"],
                        exact_points=result["exact_points"],
                        points_earned=apply_change_cost(result["total"], changed),
                        finalized=True, changed_answer=changed, submitted_at=when,
                    ))

    print(f"✅ Seeded {pick_count} pick quizzes and {trophy_count} trophy quizzes "
          f"in guild {guild_id} for {len(players)} players (you + "
          f"{', '.join(name for _, name, _ in SYNTHETIC_PLAYERS)}).")
    print("Check /stats (🧠 Quiz Stats field) and the quiz leaderboards.")


async def purge() -> None:
    from sqlalchemy import delete, select
    from database.db_session import AsyncSessionLocal
    from models import (QuizSession, QuizStats, QuizSubmission,
                        TrophyQuizSession, TrophyQuizSubmission)

    pattern = QUIZ_PREFIX + "%"
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Players + guilds whose QuizStats aggregates must be rebuilt.
            affected = (await session.execute(
                select(QuizSubmission.player_id, QuizSession.guild_id)
                .join(QuizSession, QuizSubmission.quiz_id == QuizSession.quiz_id)
                .where(QuizSession.quiz_id.like(pattern)).distinct()
            )).all()

            await session.execute(delete(QuizSubmission).where(QuizSubmission.quiz_id.like(pattern)))
            await session.execute(delete(QuizSession).where(QuizSession.quiz_id.like(pattern)))
            await session.execute(delete(TrophyQuizSubmission).where(TrophyQuizSubmission.quiz_id.like(pattern)))
            await session.execute(delete(TrophyQuizSession).where(TrophyQuizSession.quiz_id.like(pattern)))

            # Rebuild each affected player's QuizStats from what remains,
            # replaying update_stats in submission order (streaks depend on it).
            for player_id, guild_id in affected:
                stats = (await session.execute(select(QuizStats).where(
                    QuizStats.player_id == player_id, QuizStats.guild_id == guild_id
                ))).scalar_one_or_none()
                if stats is None:
                    continue
                remaining = (await session.execute(
                    select(QuizSubmission)
                    .join(QuizSession, QuizSubmission.quiz_id == QuizSession.quiz_id)
                    .where(QuizSession.guild_id == guild_id,
                           QuizSubmission.player_id == player_id)
                    .order_by(QuizSubmission.submitted_at)
                )).scalars().all()
                if not remaining:
                    await session.delete(stats)
                    continue
                fresh = QuizStats(player_id=player_id, guild_id=guild_id,
                                  display_name=stats.display_name)
                for sub in remaining:
                    fresh.update_stats(sub.correct_count, sub.points_earned)
                    fresh.last_quiz_time = sub.submitted_at
                await session.delete(stats)
                await session.flush()
                session.add(fresh)
    print(f"Purged all {QUIZ_PREFIX}* quizzes/submissions and rebuilt affected QuizStats.")


def main():
    parser = argparse.ArgumentParser(
        description="Inject completed pick + trophy quiz history for /stats and "
                    "leaderboard testing (TEST_MODE only).")
    parser.add_argument("--guild-id", help="Discord guild id of your test server")
    parser.add_argument("--user-id", help="your Discord user id (gets quiz history alongside 3 test players)")
    parser.add_argument("--pick", type=int, default=8, help="number of pick quizzes to seed (default 8)")
    parser.add_argument("--trophy", type=int, default=6, help="number of trophy quizzes to seed (default 6)")
    parser.add_argument("--purge", action="store_true",
                        help="delete all seeded quiz history and rebuild QuizStats instead of seeding")
    args = parser.parse_args()

    from config import is_test_mode
    if not is_test_mode():
        print("ERROR: this script only runs with TEST_MODE=true (in .env or the environment).")
        print("It injects fake quiz history and must never touch a production database.")
        sys.exit(1)

    if args.purge:
        asyncio.run(purge())
        return
    if not args.guild_id or not args.user_id:
        parser.error("--guild-id and --user-id are required (unless using --purge)")

    asyncio.run(seed(args.guild_id, args.user_id, args.pick, args.trophy))


if __name__ == "__main__":
    main()
