#!/usr/bin/env python3
"""Seed a fake-but-realistic draft so /post_trophy_quiz works with no prod data.

Inserts one eligible DraftSession (with a Draftmancer-shaped log stored in the
draft_data DB column) plus fully-reported MatchResult rows (one 3-0, one 0-3),
using real MTG cards (Scryfall UUIDs + direct CDN image URLs) so the pile
images render. Combined with the TEST_MODE gates in cogs/trophy_quiz_commands.py
(Spaces -> draft_data fallback, MPT placeholder deck links), this makes the
whole trophy quiz flow clickable in a local test guild without DO_SPACES_* or
MPT_API_KEY credentials.

Usage (from the repo root the bot runs from — drafts.db is a relative path):

    pipenv run python scripts/seed_test_trophy_quiz.py --guild-id <TEST_GUILD_ID> [--me <YOUR_DISCORD_ID>]
    pipenv run python scripts/seed_test_trophy_quiz.py --purge

Then run /post_trophy_quiz in the test guild (needs Bot Manager/Manage Roles).
Each posted quiz consumes one seeded draft — re-run this script for each
additional quiz. --me puts your own Discord id in seat 0 so the pilots reveal
mentions a real user. --purge deletes all previously seeded test-trophy-* rows
(and their quiz sessions) for a clean slate.

Requires TEST_MODE=true (in .env or the environment); refuses otherwise. Safe
to run while the bot is up (WAL journal mode + 30s busy timeout). Card images
come straight from the Scryfall CDN (unthrottled), so posting is quick;
internet access is still required.
"""

import argparse
import asyncio
import random
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

# Add parent directory to path to import project modules
sys.path.append(str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

SESSION_PREFIX = "test-trophy-"

# Real single-faced cards with their permanent Scryfall UUIDs and direct CDN
# image URLs — the exact shape captured Draftmancer logs have. The CDN rung of
# helpers/card_image_fetcher is unthrottled, so pile images build fast and
# can't be 429'd; the by-UUID and by-name API rungs remain as fallbacks.
# Spans MV 0-7+ plus mana_cost=="" & cmc==0 nonbasics for the Lands pile column.
# No setRestriction / back keys in the log (see helpers/magicprotools_helper).
TEST_CARDS = [
    {"id": "bd3d4b4b-cf31-4f89-8140-9650edb03c7b", "name": "Ancient Tomb",
     "cmc": 0, "mana_cost": "",
     "image": "https://cards.scryfall.io/normal/front/b/d/bd3d4b4b-cf31-4f89-8140-9650edb03c7b.jpg"},
    {"id": "c21565d0-fc40-4d89-9b27-87c03385e0af", "name": "City of Brass",
     "cmc": 0, "mana_cost": "",
     "image": "https://cards.scryfall.io/normal/front/c/2/c21565d0-fc40-4d89-9b27-87c03385e0af.jpg"},
    {"id": "c0318a48-30e4-4ef7-be3d-5e561c5ce428", "name": "Evolving Wilds",
     "cmc": 0, "mana_cost": "",
     "image": "https://cards.scryfall.io/normal/front/c/0/c0318a48-30e4-4ef7-be3d-5e561c5ce428.jpg"},
    {"id": "aaafb9bc-7cea-4624-a227-595544fa42b0", "name": "Wasteland",
     "cmc": 0, "mana_cost": "",
     "image": "https://cards.scryfall.io/normal/front/a/a/aaafb9bc-7cea-4624-a227-595544fa42b0.jpg"},
    {"id": "305078a5-ac18-4721-bba2-3434eba5b1cf", "name": "Ornithopter",
     "cmc": 0, "mana_cost": "{0}",
     "image": "https://cards.scryfall.io/normal/front/3/0/305078a5-ac18-4721-bba2-3434eba5b1cf.jpg"},
    {"id": "bd8fa327-dd41-4737-8f19-2cf5eb1f7cdd", "name": "Black Lotus",
     "cmc": 0, "mana_cost": "{0}",
     "image": "https://cards.scryfall.io/normal/front/b/d/bd8fa327-dd41-4737-8f19-2cf5eb1f7cdd.jpg"},
    {"id": "7673784e-db4b-43a1-8d55-1bb9fc1e284f", "name": "Lightning Bolt",
     "cmc": 1, "mana_cost": "{R}",
     "image": "https://cards.scryfall.io/normal/front/7/6/7673784e-db4b-43a1-8d55-1bb9fc1e284f.jpg"},
    {"id": "6a0b230b-d391-4998-a3f7-7b158a0ec2cd", "name": "Llanowar Elves",
     "cmc": 1, "mana_cost": "{G}",
     "image": "https://cards.scryfall.io/normal/front/6/a/6a0b230b-d391-4998-a3f7-7b158a0ec2cd.jpg"},
    {"id": "b4e9c870-23c0-413a-ae39-265f09da16d1", "name": "Swords to Plowshares",
     "cmc": 1, "mana_cost": "{W}",
     "image": "https://cards.scryfall.io/normal/front/b/4/b4e9c870-23c0-413a-ae39-265f09da16d1.jpg"},
    {"id": "b5545882-6963-4729-b2c6-fb4bdc75ffcc", "name": "Brainstorm",
     "cmc": 1, "mana_cost": "{U}",
     "image": "https://cards.scryfall.io/normal/front/b/5/b5545882-6963-4729-b2c6-fb4bdc75ffcc.jpg"},
    {"id": "34c3a894-ee75-4db9-a69f-711bb3cc150a", "name": "Duress",
     "cmc": 1, "mana_cost": "{B}",
     "image": "https://cards.scryfall.io/normal/front/3/4/34c3a894-ee75-4db9-a69f-711bb3cc150a.jpg"},
    {"id": "91fdb56b-54d5-4272-8319-505ff987fe9b", "name": "Sol Ring",
     "cmc": 1, "mana_cost": "{1}",
     "image": "https://cards.scryfall.io/normal/front/9/1/91fdb56b-54d5-4272-8319-505ff987fe9b.jpg"},
    {"id": "4f616706-ec97-4923-bb1e-11a69fbaa1f8", "name": "Counterspell",
     "cmc": 2, "mana_cost": "{U}{U}",
     "image": "https://cards.scryfall.io/normal/front/4/f/4f616706-ec97-4923-bb1e-11a69fbaa1f8.jpg"},
    {"id": "4101e3fe-b0e7-4f0f-b9ac-9b61a4d628b3", "name": "Lightning Helix",
     "cmc": 2, "mana_cost": "{R}{W}",
     "image": "https://cards.scryfall.io/normal/front/4/1/4101e3fe-b0e7-4f0f-b9ac-9b61a4d628b3.jpg"},
    {"id": "7a8b1c49-8594-426d-b585-41140235bb0e", "name": "Sakura-Tribe Elder",
     "cmc": 2, "mana_cost": "{1}{G}",
     "image": "https://cards.scryfall.io/normal/front/7/a/7a8b1c49-8594-426d-b585-41140235bb0e.jpg"},
    {"id": "b9640cbf-b016-410e-9eff-e8924883517b", "name": "Bitterblossom",
     "cmc": 2, "mana_cost": "{1}{B}",
     "image": "https://cards.scryfall.io/normal/front/b/9/b9640cbf-b016-410e-9eff-e8924883517b.jpg"},
    {"id": "a5048047-abff-4a1f-8d72-6b758a03542c", "name": "Remand",
     "cmc": 2, "mana_cost": "{1}{U}",
     "image": "https://cards.scryfall.io/normal/front/a/5/a5048047-abff-4a1f-8d72-6b758a03542c.jpg"},
    {"id": "3b6e5956-f795-451b-bb24-56462d1ced27", "name": "Umezawa's Jitte",
     "cmc": 2, "mana_cost": "{2}",
     "image": "https://cards.scryfall.io/normal/front/3/b/3b6e5956-f795-451b-bb24-56462d1ced27.jpg"},
    {"id": "39704000-65d3-4d39-849e-a3b617376bbc", "name": "Eternal Witness",
     "cmc": 3, "mana_cost": "{1}{G}{G}",
     "image": "https://cards.scryfall.io/normal/front/3/9/39704000-65d3-4d39-849e-a3b617376bbc.jpg"},
    {"id": "683c4e13-525c-45c9-8832-bfe67965c34e", "name": "Vindicate",
     "cmc": 3, "mana_cost": "{1}{W}{B}",
     "image": "https://cards.scryfall.io/normal/front/6/8/683c4e13-525c-45c9-8832-bfe67965c34e.jpg"},
    {"id": "3dac9526-388d-487e-a790-066f83050794", "name": "Blade Splicer",
     "cmc": 3, "mana_cost": "{2}{W}",
     "image": "https://cards.scryfall.io/normal/front/3/d/3dac9526-388d-487e-a790-066f83050794.jpg"},
    {"id": "a1ef40d5-c287-4229-8059-3505d9f251ca", "name": "Man-o'-War",
     "cmc": 3, "mana_cost": "{2}{U}",
     "image": "https://cards.scryfall.io/normal/front/a/1/a1ef40d5-c287-4229-8059-3505d9f251ca.jpg"},
    {"id": "efbb7256-9337-4183-8bda-a419f3f2c501", "name": "Liliana of the Veil",
     "cmc": 3, "mana_cost": "{1}{B}{B}",
     "image": "https://cards.scryfall.io/normal/front/e/f/efbb7256-9337-4183-8bda-a419f3f2c501.jpg"},
    {"id": "cdb88a22-8086-4bf7-89e8-cce929440dd8", "name": "Trygon Predator",
     "cmc": 3, "mana_cost": "{1}{G}{U}",
     "image": "https://cards.scryfall.io/normal/front/c/d/cdb88a22-8086-4bf7-89e8-cce929440dd8.jpg"},
    {"id": "537d2b05-3f52-45d6-8fe3-26282085d0c6", "name": "Wrath of God",
     "cmc": 4, "mana_cost": "{2}{W}{W}",
     "image": "https://cards.scryfall.io/normal/front/5/3/537d2b05-3f52-45d6-8fe3-26282085d0c6.jpg"},
    {"id": "04588d2f-9e90-4f83-b85e-67e4bc222a62", "name": "Fact or Fiction",
     "cmc": 4, "mana_cost": "{3}{U}",
     "image": "https://cards.scryfall.io/normal/front/0/4/04588d2f-9e90-4f83-b85e-67e4bc222a62.jpg"},
    {"id": "e2f12f6f-9383-47e6-a44f-2834ad130e51", "name": "Bloodbraid Elf",
     "cmc": 4, "mana_cost": "{2}{R}{G}",
     "image": "https://cards.scryfall.io/normal/front/e/2/e2f12f6f-9383-47e6-a44f-2834ad130e51.jpg"},
    {"id": "f17f85d3-58e5-4128-90c5-98b524256af8", "name": "Restoration Angel",
     "cmc": 4, "mana_cost": "{3}{W}",
     "image": "https://cards.scryfall.io/normal/front/f/1/f17f85d3-58e5-4128-90c5-98b524256af8.jpg"},
    {"id": "4bd3014b-94bb-4a9f-92cf-239a2dcc7e97", "name": "Baneslayer Angel",
     "cmc": 5, "mana_cost": "{3}{W}{W}",
     "image": "https://cards.scryfall.io/normal/front/4/b/4bd3014b-94bb-4a9f-92cf-239a2dcc7e97.jpg"},
    {"id": "3de308cc-14ac-407e-99e7-568572ecd0e7", "name": "Mulldrifter",
     "cmc": 5, "mana_cost": "{4}{U}",
     "image": "https://cards.scryfall.io/normal/front/3/d/3de308cc-14ac-407e-99e7-568572ecd0e7.jpg"},
    {"id": "a75f6bb4-ab06-42ca-a0df-326d9a098a26", "name": "Thundermaw Hellkite",
     "cmc": 5, "mana_cost": "{3}{R}{R}",
     "image": "https://cards.scryfall.io/normal/front/a/7/a75f6bb4-ab06-42ca-a0df-326d9a098a26.jpg"},
    {"id": "b7f16fdf-a3f5-462d-a64a-789d893b6ef5", "name": "Batterskull",
     "cmc": 5, "mana_cost": "{5}",
     "image": "https://cards.scryfall.io/normal/front/b/7/b7f16fdf-a3f5-462d-a64a-789d893b6ef5.jpg"},
    {"id": "5d275f04-cc60-4e3f-95cc-3d02bc916b82", "name": "Wurmcoil Engine",
     "cmc": 6, "mana_cost": "{6}",
     "image": "https://cards.scryfall.io/normal/front/5/d/5d275f04-cc60-4e3f-95cc-3d02bc916b82.jpg"},
    {"id": "3b4faa6e-5013-4c59-80f5-662a386672eb", "name": "Grave Titan",
     "cmc": 6, "mana_cost": "{4}{B}{B}",
     "image": "https://cards.scryfall.io/normal/front/3/b/3b4faa6e-5013-4c59-80f5-662a386672eb.jpg"},
    {"id": "f839aadb-084e-4ddc-ba62-d654a695cc6b", "name": "Inferno Titan",
     "cmc": 6, "mana_cost": "{4}{R}{R}",
     "image": "https://cards.scryfall.io/normal/front/f/8/f839aadb-084e-4ddc-ba62-d654a695cc6b.jpg"},
    {"id": "3d6eacf2-f6c7-4ede-b5a5-7463602699ae", "name": "Sun Titan",
     "cmc": 6, "mana_cost": "{4}{W}{W}",
     "image": "https://cards.scryfall.io/normal/front/3/d/3d6eacf2-f6c7-4ede-b5a5-7463602699ae.jpg"},
    {"id": "c6f1e60f-a195-4590-80b0-86767de6c423", "name": "Avenger of Zendikar",
     "cmc": 7, "mana_cost": "{5}{G}{G}",
     "image": "https://cards.scryfall.io/normal/front/c/6/c6f1e60f-a195-4590-80b0-86767de6c423.jpg"},
    {"id": "4b0c6662-4dde-40a2-97e0-0318478c0367", "name": "Karn Liberated",
     "cmc": 7, "mana_cost": "{7}",
     "image": "https://cards.scryfall.io/normal/front/4/b/4b0c6662-4dde-40a2-97e0-0318478c0367.jpg"},
    {"id": "4069e510-f3f3-4668-9f13-3546fa9bc7c3", "name": "Griselbrand",
     "cmc": 8, "mana_cost": "{4}{B}{B}{B}",
     "image": "https://cards.scryfall.io/normal/front/4/0/4069e510-f3f3-4668-9f13-3546fa9bc7c3.jpg"},
]

DRAFTER_NAMES = ["TestAlice", "TestBob", "TestCarol", "TestDave", "TestErin", "TestFrank"]

# 6 drafters, 3 rounds, 9 fully-reported matches as (p1_idx, p2_idx, winner_idx):
# drafter 0 goes 3-0, drafter 5 goes 0-3, the rest 2-1/1-2 — satisfies
# select_two_decks (both buckets non-empty + an extreme record present).
MATCH_TABLE = [
    (0, 1, 0), (2, 3, 2), (4, 5, 4),
    (0, 2, 0), (1, 4, 1), (3, 5, 3),
    (0, 3, 0), (1, 5, 1), (2, 4, 2),
]

POOL_SIZE = 24
MAIN_SIZE = 16


def _carddata() -> dict:
    """carddata keyed by Scryfall UUID, with Draftmancer's language-keyed
    image_uris shape so the image fetcher's unthrottled CDN rung is used."""
    return {
        card["id"]: {
            "name": card["name"],
            "cmc": card["cmc"],
            "mana_cost": card["mana_cost"],
            "image_uris": {"en": card["image"]},
        }
        for card in TEST_CARDS
    }


def build_sign_ups(discord_ids: list, names: list) -> dict:
    """sign_ups dict in SEAT ORDER — map_discord_to_draftmancer aligns sign_ups
    insertion order against users sorted by seatNum, so this order is load-bearing."""
    return {str(uid): name for uid, name in zip(discord_ids, names)}


def build_draft_log(session_id: str, discord_ids: list, names: list, rng: random.Random) -> dict:
    """A minimal-but-valid Draftmancer log: enough for select_two_decks,
    map_discord_to_draftmancer, split_decklist/build_mtgo_deck_text, the pile
    images, and (if an MPT_API_KEY happens to be set) the MPT converter."""
    carddata = _carddata()
    all_ids = list(carddata.keys())

    users = {}
    for seat, name in enumerate(names):
        pool = rng.sample(all_ids, POOL_SIZE)
        users[f"dm-{seat}"] = {
            "userName": name,
            "seatNum": seat,
            "isBot": False,
            "cards": pool,
            "decklist": {
                "main": pool[:MAIN_SIZE],
                "side": pool[MAIN_SIZE:],
                "lands": {"U": 9, "R": 8},
            },
            "picks": [
                {"packNum": i // 8, "pickNum": i % 8, "booster": [cid], "pick": [0]}
                for i, cid in enumerate(pool)
            ],
        }

    return {
        "sessionID": session_id,
        "time": int(datetime.now().timestamp() * 1000),  # epoch ms (MPT divides by 1000)
        "users": users,
        "carddata": carddata,
    }


async def seed(guild_id: str, me: str | None) -> str:
    from database.db_session import AsyncSessionLocal, engine
    from database.models_base import Base
    import models  # noqa: F401 — registers every table on Base for create_all
    from models import DraftSession, MatchResult

    discord_ids = [str(900000000000000100 + i) for i in range(6)]
    names = list(DRAFTER_NAMES)
    if me:
        discord_ids[0] = str(me)
        names[0] = "You (seat 0)"

    session_id = SESSION_PREFIX + uuid4().hex[:8]
    rng = random.Random(session_id)
    sign_ups = build_sign_ups(discord_ids, names)
    log = build_draft_log(session_id, discord_ids, names, rng)

    if not Path("drafts.db").exists():
        print("⚠ drafts.db not found in the current directory — creating a new one.")
        print("  Run this script from the repo root the bot runs from, or the bot won't see the seed.")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        async with session.begin():
            session.add(DraftSession(
                session_id=session_id,
                guild_id=str(guild_id),
                session_type="random",
                draft_start_time=datetime.now(),
                sign_ups=sign_ups,
                cube="TestCube",
                spaces_object_key=f"test/{session_id}.json",  # dummy; non-null for eligibility
                draft_data=log,
                data_received=True,
            ))
            for i, (p1, p2, w) in enumerate(MATCH_TABLE):
                session.add(MatchResult(
                    session_id=session_id,
                    match_number=i + 1,
                    player1_id=discord_ids[p1],
                    player2_id=discord_ids[p2],
                    player1_wins=2 if w == p1 else 1,
                    player2_wins=2 if w == p2 else 1,
                    winner_id=discord_ids[w],
                    guild_id=str(guild_id),
                    result_submitted_at=datetime.now(),
                ))
    return session_id


async def purge() -> None:
    from sqlalchemy import delete
    from database.db_session import AsyncSessionLocal
    from models import DraftSession, MatchResult, TrophyQuizSession

    pattern = SESSION_PREFIX + "%"
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(
                delete(TrophyQuizSession).where(TrophyQuizSession.draft_session_id.like(pattern)))
            await session.execute(
                delete(MatchResult).where(MatchResult.session_id.like(pattern)))
            await session.execute(
                delete(DraftSession).where(DraftSession.session_id.like(pattern)))
    print(f"Purged all {SESSION_PREFIX}* drafts, their match results, and their quiz sessions.")


def main():
    parser = argparse.ArgumentParser(
        description="Seed a fake draft for local trophy quiz testing (TEST_MODE only).")
    parser.add_argument("--guild-id", help="Discord guild id of your test server")
    parser.add_argument("--me", help="your Discord user id — seats you as drafter 0 (cosmetic)")
    parser.add_argument("--purge", action="store_true",
                        help="delete all previously seeded test-trophy-* rows instead of seeding")
    args = parser.parse_args()

    from config import is_test_mode
    if not is_test_mode():
        print("ERROR: this script only runs with TEST_MODE=true (in .env or the environment).")
        print("It seeds fake drafts/matches and must never touch a production database.")
        sys.exit(1)

    if args.purge:
        asyncio.run(purge())
        return
    if not args.guild_id:
        parser.error("--guild-id is required (unless using --purge)")

    session_id = asyncio.run(seed(args.guild_id, args.me))
    print(f"✅ Seeded draft {session_id} for guild {args.guild_id}.")
    print("Next: run /post_trophy_quiz in your test guild (needs Bot Manager/Manage Roles).")
    print("Each quiz consumes one seeded draft — re-run this script for another quiz.")


if __name__ == "__main__":
    main()
