from .base_session import BaseSession
from discord import Embed, Color

USE_V2 = False


class RandomSession(BaseSession):
    def _create_view(self, bot, draft_session):
        if USE_V2:
            from .random_v2_view import RandomDraftV2View
            return RandomDraftV2View(
                bot=bot,
                draft_session_id=draft_session.session_id,
                cube=draft_session.cube,
                draft_start_time=int(draft_session.draft_start_time.timestamp()),
            )
        return super()._create_view(bot, draft_session)

    async def _send_initial_message(self, interaction, bot, draft_session, view):
        if USE_V2:
            await interaction.response.send_message(view=view)
        else:
            await super()._send_initial_message(interaction, bot, draft_session, view)

    def _create_embed_content(self):
        # Remove the cube from the title since it's now in its own field
        title = f"Looking for Players! Random Team Draft - Queue Opened <t:{self.session_details.draft_start_time}:R>"
        description = (
            "**How to use bot**:\n"
            "1. Click sign up and click the draftmancer link.\n"
            "2. When enough people join (6 or 8), push Ready Check. Once everyone is ready, push Create Teams.\n"
            "3. Create Teams will create random teams and a corresponding seating order. Draftmancer host needs "
            "to adjust table to match seating order. **TURN OFF RANDOM SEATING IN DRAFTMANCER**\n"
            f"{self.get_common_description()}"
        )
        embed = Embed(title=title, description=description, color=Color.dark_magenta())
        return embed

    def get_session_type(self):
        return "random"
