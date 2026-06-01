import logging
from datetime import datetime
from typing import Optional

import discord

from config import BOT_LOGS_CHANNEL_ID

logger = logging.getLogger("ArmamentBot")

_bot = None

def set_bot(bot_instance):
    global _bot
    _bot = bot_instance


async def log_accion(
    actor: discord.Member,
    accion: str,
    detalle: str = "",
    color: discord.Color = discord.Color.blurple(),
    emoji: str = "📋",
):
    try:
        channel = _bot.get_channel(BOT_LOGS_CHANNEL_ID)
        if not channel:
            return
        embed = discord.Embed(
            description=f"{emoji} **{accion}**" + (f"\n{detalle}" if detalle else ""),
            color=color,
            timestamp=datetime.now(),
        )
        embed.set_author(
            name=f"{actor.display_name} ({actor.id})",
            icon_url=actor.display_avatar.url if actor.display_avatar else None,
        )
        await channel.send(embed=embed)
    except Exception as e:
        logger.warning(f"⚠️ No se pudo enviar log de acción: {e}")