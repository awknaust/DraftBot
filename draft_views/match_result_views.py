import discord
from discord import SelectOption
from discord.ui import Button, View, Select
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from session import AsyncSessionLocal, DraftSession, MatchResult
from helpers.display_names import get_display_name
from utils import (
    fetch_match_details,
    update_player_stats_and_elo,
    update_draft_summary_message,
    check_and_post_victory_or_draw,
    store_match_streak_extensions,
)
from loguru import logger


async def create_pairings_view(bot, guild, session_id, match_results):
    view = View(timeout=None)
    for match_result in match_results:
        button = MatchResultButton(
            bot=bot,
            session_id=session_id,
            match_id=match_result.id,
            match_number=match_result.match_number,
            label=f"Match {match_result.match_number} Results",
            style=discord.ButtonStyle.secondary,
            row=None,
        )
        view.add_item(button)
    return view


class MatchResultModal(discord.ui.DesignerModal):
    def __init__(self, bot, session_id, match_number, player1_name, player2_name):
        self.bot = bot
        self.session_id = session_id
        self.match_number = match_number

        self.result_select = discord.ui.Select(
            select_type=discord.ComponentType.string_select,
            placeholder=f"{player1_name} v. {player2_name}",
            options=[
                SelectOption(label=f"{player1_name} wins: 2-0", value="2-0-1"),
                SelectOption(label=f"{player1_name} wins: 2-1", value="2-1-1"),
                SelectOption(label=f"{player2_name} wins: 2-0", value="0-2-2"),
                SelectOption(label=f"{player2_name} wins: 2-1", value="1-2-2"),
                SelectOption(label="No Match Played", value="0-0-0"),
            ],
        )

        label = discord.ui.Label(
            f"Match {match_number}: {player1_name} vs {player2_name}",
            self.result_select,
        )

        super().__init__(label, title=f"Match {match_number} Result")

    async def callback(self, interaction):
        await interaction.response.defer()
        try:
            player1_wins, player2_wins, winner_indicator = self.result_select.values[0].split('-')
            player1_wins = int(player1_wins)
            player2_wins = int(player2_wins)
            winner_id = None

            draft_session = None

            async with AsyncSessionLocal() as session:
                async with session.begin():
                    stmt = select(MatchResult, DraftSession).join(DraftSession).where(
                        MatchResult.session_id == self.session_id,
                        MatchResult.match_number == self.match_number,
                    )
                    result = await session.execute(stmt)
                    row = result.first()

                    if not row:
                        await interaction.followup.send("Error: Match result or session not found.", ephemeral=True)
                        return

                    match_result, draft_session = row

                    if match_result:
                        match_result.player1_wins = player1_wins
                        match_result.player2_wins = player2_wins
                        if winner_indicator != '0':
                            winner_id = match_result.player1_id if winner_indicator == '1' else match_result.player2_id
                        match_result.winner_id = winner_id

                        await session.commit()

                        if draft_session and (draft_session.session_type == "random" or draft_session.session_type == "staked"):
                            streak_extensions = await update_player_stats_and_elo(match_result)

                            store_match_streak_extensions(
                                self.session_id,
                                match_result.player1_id,
                                match_result.player2_id,
                                streak_extensions,
                            )

                            if winner_id:
                                loser_id = match_result.player2_id if winner_id == match_result.player1_id else match_result.player1_id
                                from services.ring_bearer_service import check_match_defeat_transfer
                                await check_match_defeat_transfer(
                                    bot=self.bot,
                                    guild_id=str(draft_session.guild_id),
                                    winner_id=winner_id,
                                    loser_id=loser_id,
                                    session_id=self.session_id,
                                )

            if draft_session:
                await update_draft_summary_message(self.bot, self.session_id)
                from livedrafts import update_live_draft_summary
                await update_live_draft_summary(self.bot, self.session_id)
                if draft_session.session_type != "test":
                    await check_and_post_victory_or_draw(self.bot, self.session_id)
                await _update_pairings_posting(interaction, self.bot, self.session_id, self.match_number)
            else:
                logger.error(f"Draft session data missing for session {self.session_id}")
                await interaction.followup.send("Error: Could not retrieve draft session data.", ephemeral=True)

        except Exception as e:
            logger.exception(f"Error in match result modal: {e}")
            try:
                await interaction.followup.send("An error occurred while updating the match result.", ephemeral=True)
            except Exception:
                pass


class MatchResultButton(Button):
    def __init__(self, bot, session_id, match_id, match_number, label, *args, **kwargs):
        super().__init__(label=label, *args, **kwargs)
        self.bot = bot
        self.session_id = session_id
        self.match_id = match_id
        self.match_number = match_number

    async def callback(self, interaction):
        try:
            player1_name, player2_name = await fetch_match_details(self.bot, self.session_id, self.match_number)

            if not player1_name or not player2_name:
                await interaction.response.send_message("Error: Could not fetch match details.", ephemeral=True)
                return

            modal = MatchResultModal(
                bot=self.bot,
                session_id=self.session_id,
                match_number=self.match_number,
                player1_name=player1_name,
                player2_name=player2_name,
            )
            await interaction.response.send_modal(modal)
        except Exception as e:
            logger.exception(f"Error in match result button: {e}")
            await interaction.response.send_message("An error occurred while fetching match details.", ephemeral=True)


class MatchResultSelect(Select):
    def __init__(self, bot, match_number, session_id, player1_name, player2_name, *args, **kwargs):
        self.bot = bot
        self.match_number = match_number
        self.session_id = session_id

        options = [
            SelectOption(label=f"{player1_name} wins: 2-0", value="2-0-1"),
            SelectOption(label=f"{player1_name} wins: 2-1", value="2-1-1"),
            SelectOption(label=f"{player2_name} wins: 2-0", value="0-2-2"),
            SelectOption(label=f"{player2_name} wins: 2-1", value="1-2-2"),
            SelectOption(label="No Match Played", value="0-0-0"),
        ]
        super().__init__(
            placeholder=f"{player1_name} v. {player2_name}",
            min_values=1,
            max_values=1,
            options=options,
            *args,
            **kwargs,
        )

    async def callback(self, interaction):
        await interaction.response.defer()
        try:
            player1_wins, player2_wins, winner_indicator = self.values[0].split('-')
            player1_wins = int(player1_wins)
            player2_wins = int(player2_wins)
            winner_id = None

            draft_session = None

            async with AsyncSessionLocal() as session:
                async with session.begin():
                    stmt = select(MatchResult, DraftSession).join(DraftSession).where(
                        MatchResult.session_id == self.session_id,
                        MatchResult.match_number == self.match_number
                    )
                    result = await session.execute(stmt)
                    row = result.first()

                    if not row:
                        await interaction.followup.send("Error: Match result or session not found.", ephemeral=True)
                        return

                    match_result, draft_session = row

                    if match_result:
                        match_result.player1_wins = player1_wins
                        match_result.player2_wins = player2_wins
                        if winner_indicator != '0':
                            winner_id = match_result.player1_id if winner_indicator == '1' else match_result.player2_id
                        match_result.winner_id = winner_id

                        await session.commit()

                        if draft_session and (draft_session.session_type == "random" or draft_session.session_type == "staked"):
                            streak_extensions = await update_player_stats_and_elo(match_result)

                            store_match_streak_extensions(
                                self.session_id,
                                match_result.player1_id,
                                match_result.player2_id,
                                streak_extensions,
                            )

                            if winner_id:
                                loser_id = match_result.player2_id if winner_id == match_result.player1_id else match_result.player1_id
                                from services.ring_bearer_service import check_match_defeat_transfer
                                await check_match_defeat_transfer(
                                    bot=self.bot,
                                    guild_id=str(draft_session.guild_id),
                                    winner_id=winner_id,
                                    loser_id=loser_id,
                                    session_id=self.session_id,
                                )

            if draft_session:
                await update_draft_summary_message(self.bot, self.session_id)
                from livedrafts import update_live_draft_summary
                await update_live_draft_summary(self.bot, self.session_id)
                if draft_session.session_type != "test":
                    await check_and_post_victory_or_draw(self.bot, self.session_id)
                await _update_pairings_posting(interaction, self.bot, self.session_id, self.match_number)
            else:
                logger.error(f"Draft session data missing for session {self.session_id}")
                await interaction.followup.send("Error: Could not retrieve draft session data.", ephemeral=True)

        except Exception as e:
            logger.exception(f"Error in match result selection: {e}")
            try:
                await interaction.followup.send("An error occurred while updating the match result.", ephemeral=True)
            except Exception:
                pass


async def _update_pairings_posting(interaction, bot, draft_session_id, match_number):
    guild = bot.get_guild(int(interaction.guild_id))

    if not guild:
        logger.warning("Guild not found in _update_pairings_posting")
        return

    async with AsyncSessionLocal() as session:
        stmt = select(MatchResult).where(
            MatchResult.session_id == draft_session_id,
            MatchResult.match_number == match_number,
        )
        result = await session.execute(stmt)
        match_result = result.scalar_one_or_none()

        if not match_result:
            logger.warning(f"No match result found for match {match_number} in session {draft_session_id}")
            return

        pairing_message_id = match_result.pairing_message_id
        if not pairing_message_id:
            logger.warning(f"No pairing message ID for match {match_number}")
            return

        draft_session_result = await session.execute(select(DraftSession).filter_by(session_id=draft_session_id))
        draft_session = draft_session_result.scalar_one_or_none()

        if not draft_session:
            logger.warning("Draft session not found in _update_pairings_posting")
            return

        channel = guild.get_channel(int(draft_session.draft_chat_channel))
        if not channel:
            logger.warning("Channel not found in _update_pairings_posting")
            return

        try:
            message = await channel.fetch_message(int(pairing_message_id))
        except Exception as e:
            logger.error(f"Failed to fetch pairings message {pairing_message_id}: {e}")
            return

        embed = message.embeds[0] if message.embeds else None
        if not embed:
            logger.warning("No embed found in pairings message")
            return

        stmt = select(MatchResult).where(
            MatchResult.session_id == draft_session_id,
            MatchResult.pairing_message_id == pairing_message_id,
        )
        result = await session.execute(stmt)
        match_results_for_this_message = result.scalars().all()

        player1 = guild.get_member(int(match_result.player1_id))
        player2 = guild.get_member(int(match_result.player2_id))
        player1_name = get_display_name(player1, guild)
        player2_name = get_display_name(player2, guild)

        for mr in match_results_for_this_message:
            if mr.match_number == match_number:
                winning_team_emoji = "⚫ "
                if mr.winner_id:
                    if mr.winner_id in draft_session.team_a:
                        winning_team_emoji = "🔴 "
                    elif mr.winner_id in draft_session.team_b:
                        winning_team_emoji = "🔵 "

                updated_value = (
                    f"{winning_team_emoji}**Match {mr.match_number}**\n"
                    f"{player1_name}: {mr.player1_wins} wins\n"
                    f"{player2_name}: {mr.player2_wins} wins"
                )

                found_match = False
                for i, field in enumerate(embed.fields):
                    if (f"Match {mr.match_number}" in field.value
                            and player1_name in field.value
                            and player2_name in field.value):
                        embed.set_field_at(i, name=field.name, value=updated_value, inline=field.inline)
                        found_match = True
                        break

                if not found_match:
                    for i, field in enumerate(embed.fields):
                        if f"Match {mr.match_number}" in field.value:
                            embed.set_field_at(i, name=field.name, value=updated_value, inline=field.inline)
                            break

        new_view = await _build_pairings_view(bot, guild.id, draft_session_id, pairing_message_id)

        try:
            await message.edit(embed=embed, view=new_view)
        except Exception as e:
            logger.error(f"Error updating pairings message: {e}")


async def _build_pairings_view(bot, guild_id, draft_session_id, pairing_message_id):
    """Rebuild the pairings button view with result-coloured buttons."""
    guild = bot.get_guild(guild_id)
    if not guild:
        return discord.ui.View()

    view = discord.ui.View(timeout=None)
    async with AsyncSessionLocal() as session:
        draft_session_result = await session.execute(select(DraftSession).filter_by(session_id=draft_session_id))
        draft_session = draft_session_result.scalar_one_or_none()

        stmt = select(MatchResult).where(
            MatchResult.session_id == draft_session_id,
            MatchResult.pairing_message_id == pairing_message_id,
        )
        result = await session.execute(stmt)
        match_results = result.scalars().all()

        for match_result in match_results:
            button_style = discord.ButtonStyle.secondary

            if match_result.winner_id:
                if draft_session and match_result.winner_id in draft_session.team_a:
                    button_style = discord.ButtonStyle.danger
                elif draft_session and match_result.winner_id in draft_session.team_b:
                    button_style = discord.ButtonStyle.blurple
                else:
                    button_style = discord.ButtonStyle.grey

            button = MatchResultButton(
                bot=bot,
                session_id=draft_session_id,
                match_id=match_result.id,
                match_number=match_result.match_number,
                label=f"Match {match_result.match_number} Results",
                style=button_style,
            )
            view.add_item(button)

    return view
