"""
Prototype: Components V2 signup view for random team drafts.

Replaces the traditional embed + PersistentView with a DesignerView that
owns both layout and button callbacks. To use, set USE_V2 = True in
random_session.py and call create_draft_session_v2 instead of create_draft_session.

Known limitations vs the current embed approach:
- update_draft_message in views.py reads message.embeds[0] — the v2 update
  path (update_v2_draft_message below) reads message.components instead.
- Sticky message rebuilding in message_management.py also reads embeds[0]
  and will need a separate code path before this can go to production.
"""

import discord
from datetime import datetime
from loguru import logger
from helpers.utils import get_cube_thumbnail_url
from sqlalchemy import update
from session import DraftSession, AsyncSessionLocal
from views import get_draft_session, SignUpHistory, update_draft_message

# Stable component ID for the players TextDisplay so we can find and patch it.
PLAYERS_DISPLAY_ID = 42


def _build_container(
    draft_start_time: int,
    cube: str,
    sign_up_btn: discord.ui.Button,
    cancel_btn: discord.ui.Button,
    ready_check_btn: discord.ui.Button,
    players_text: str = "**Players (0):** No players yet.",
) -> discord.ui.Container:
    cube_url = f"https://cubecobra.com/cube/list/{cube}"
    thumbnail_url = get_cube_thumbnail_url(cube)

    return discord.ui.Container(
        discord.ui.Section(
            discord.ui.TextDisplay(
                f"## Looking for Players! Random Team Draft\n"
                f"Queue Opened <t:{draft_start_time}:R>"
            ),
            discord.ui.TextDisplay(
                "**How to use bot:**\n"
                "1. Click **Sign Up** and then click your Draftmancer link.\n"
                "2. When 6 or 8 players have joined, click **Ready Check**. "
                "Once everyone is ready, click **Create Teams**.\n"
                "3. Create Teams generates random teams and a seating order. "
                "The Draftmancer host must match the table to that order. "
                "**Turn off random seating in Draftmancer.**\n\n"
                "Your personalised Draftmancer link will be sent once teams are created."
            ),
            accessory=discord.ui.Thumbnail(url=thumbnail_url),
        ),
        discord.ui.Separator(),
        discord.ui.TextDisplay(f"**Cube:** [{cube}]({cube_url})"),
        discord.ui.Separator(),
        discord.ui.TextDisplay(players_text, id=PLAYERS_DISPLAY_ID),
        discord.ui.Separator(),
        discord.ui.ActionRow(sign_up_btn, cancel_btn, ready_check_btn),
        color=discord.Color.dark_magenta(),
    )


class RandomDraftV2View(discord.ui.DesignerView):
    """Components V2 signup view for random team drafts."""

    def __init__(
        self,
        bot,
        draft_session_id: str,
        cube: str,
        draft_start_time: int,
        players_text: str = "**Players (0):** No players yet.",
    ):
        self.bot = bot
        self.draft_session_id = draft_session_id
        self.cube = cube
        self.draft_start_time = draft_start_time

        sign_up_btn = discord.ui.Button(
            label="Sign Up",
            style=discord.ButtonStyle.green,
            custom_id=f"v2_sign_up_{draft_session_id}",
        )
        sign_up_btn.callback = self._sign_up_callback

        cancel_btn = discord.ui.Button(
            label="Cancel Sign Up",
            style=discord.ButtonStyle.red,
            custom_id=f"v2_cancel_{draft_session_id}",
        )
        cancel_btn.callback = self._cancel_callback

        ready_check_btn = discord.ui.Button(
            label="Ready Check",
            style=discord.ButtonStyle.green,
            custom_id=f"v2_ready_check_{draft_session_id}",
        )
        ready_check_btn.callback = self._ready_check_callback

        container = _build_container(
            draft_start_time=draft_start_time,
            cube=cube,
            sign_up_btn=sign_up_btn,
            cancel_btn=cancel_btn,
            ready_check_btn=ready_check_btn,
            players_text=players_text,
        )
        super().__init__(container, timeout=None)

    def to_metadata(self) -> dict:
        return {
            "view_type": "random_v2",
            "draft_session_id": self.draft_session_id,
            "cube": self.cube,
            "draft_start_time": self.draft_start_time,
        }

    @classmethod
    async def from_metadata(cls, bot, metadata: dict) -> "RandomDraftV2View":
        """Reconstruct view from sticky metadata, populating current sign-ups."""
        session_id = metadata["draft_session_id"]
        draft_session = await get_draft_session(session_id)

        players_text = "**Players (0):** No players yet."
        if draft_session:
            sign_ups = draft_session.sign_ups or {}
            if sign_ups:
                count = len(sign_ups)
                names = "\n".join(sign_ups.values())
                players_text = f"**Players ({count}):**\n{names}"

        return cls(
            bot=bot,
            draft_session_id=session_id,
            cube=metadata["cube"],
            draft_start_time=metadata["draft_start_time"],
            players_text=players_text,
        )

    async def _sign_up_callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        draft_session = await get_draft_session(self.draft_session_id)
        if not draft_session:
            await interaction.response.send_message(
                "Draft session not found.", ephemeral=True
            )
            return

        sign_ups = draft_session.sign_ups or {}
        if user_id in sign_ups:
            await interaction.response.send_message(
                "You are already signed up!", ephemeral=True
            )
            return

        sign_ups[user_id] = interaction.user.display_name
        await SignUpHistory.record_signup_event(
            session_id=self.draft_session_id,
            user_id=user_id,
            display_name=interaction.user.display_name,
            action="join",
            guild_id=str(interaction.guild_id),
        )

        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    update(DraftSession)
                    .where(DraftSession.session_id == self.draft_session_id)
                    .values(sign_ups=sign_ups)
                )

        await interaction.response.send_message(
            "You are now signed up! Your Draftmancer link will be provided once teams are created.",
            ephemeral=True,
        )
        await update_v2_draft_message(self.bot, self.draft_session_id)

    async def _cancel_callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        draft_session = await get_draft_session(self.draft_session_id)
        if not draft_session:
            await interaction.response.send_message(
                "Draft session not found.", ephemeral=True
            )
            return

        sign_ups = draft_session.sign_ups or {}
        if user_id not in sign_ups:
            await interaction.response.send_message(
                "You are not signed up.", ephemeral=True
            )
            return

        del sign_ups[user_id]
        await SignUpHistory.record_signup_event(
            session_id=self.draft_session_id,
            user_id=user_id,
            display_name=interaction.user.display_name,
            action="leave",
            guild_id=str(interaction.guild_id),
        )

        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    update(DraftSession)
                    .where(DraftSession.session_id == self.draft_session_id)
                    .values(sign_ups=sign_ups)
                )

        await interaction.response.send_message(
            "You have been removed from the sign-up list.", ephemeral=True
        )
        await update_v2_draft_message(self.bot, self.draft_session_id)

    async def _ready_check_callback(self, interaction: discord.Interaction):
        # Delegate to the existing ready check flow in PersistentView — it sends
        # its own ephemeral embed and doesn't touch this message's components.
        from views import PersistentView
        temp_view = PersistentView.__new__(PersistentView)
        temp_view.draft_session_id = self.draft_session_id
        temp_view.bot = self.bot
        temp_view.session_type = "random"
        await temp_view.ready_check_callback(interaction, None)


async def update_v2_draft_message(bot, session_id: str):
    """Update the players TextDisplay in a Components V2 draft message."""
    draft_session = await get_draft_session(session_id)
    if not draft_session:
        logger.error(f"update_v2_draft_message: session {session_id} not found")
        return

    channel = bot.get_channel(int(draft_session.draft_channel_id))
    if not channel:
        logger.error(f"update_v2_draft_message: channel not found")
        return

    try:
        message = await channel.fetch_message(int(draft_session.message_id))
        view = discord.ui.DesignerView.from_message(message)

        players_display = view.get_item(PLAYERS_DISPLAY_ID)
        if not players_display:
            logger.error(f"update_v2_draft_message: TextDisplay id={PLAYERS_DISPLAY_ID} not found in message components")
            return

        sign_ups = draft_session.sign_ups or {}
        count = len(sign_ups)
        if sign_ups:
            names = "\n".join(sign_ups.values())
            players_display.content = f"**Players ({count}):**\n{names}"
        else:
            players_display.content = "**Players (0):** No players yet."

        await message.edit(view=view)
        logger.info(f"update_v2_draft_message: updated players for session {session_id}")

    except Exception:
        logger.exception(f"update_v2_draft_message: failed for session {session_id}")
