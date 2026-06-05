"""Safe Discord interaction responses that avoid secondary failures."""
from __future__ import annotations


import discord


async def safe_reply(
    interaction: discord.Interaction,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    ephemeral: bool = True,
) -> bool:
    """Send or follow up on an interaction. Returns False if the interaction is unusable."""
    kwargs: dict = {"ephemeral": ephemeral}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view

    try:
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
        return True
    except discord.NotFound:
        return False
    except discord.InteractionResponded:
        try:
            await interaction.followup.send(**kwargs)
            return True
        except (discord.NotFound, discord.HTTPException):
            return False
    except discord.HTTPException:
        return False


async def safe_followup(
    interaction: discord.Interaction,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    ephemeral: bool = True,
) -> bool:
    kwargs: dict = {"ephemeral": ephemeral}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    try:
        await interaction.followup.send(**kwargs)
        return True
    except (discord.NotFound, discord.HTTPException, discord.InteractionResponded):
        return False
