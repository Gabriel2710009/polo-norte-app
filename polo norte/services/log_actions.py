import logging
import discord

logger = logging.getLogger("LogActions")

_bot = None
_log_channel_id = 0
_log_channel = None


def setup(bot_instance, channel_id: int):
    global _bot, _log_channel_id
    _bot = bot_instance
    _log_channel_id = channel_id


async def _get_channel():
    global _log_channel
    if _log_channel:
        return _log_channel
    if not _bot or not _log_channel_id:
        return None
    _log_channel = _bot.get_channel(_log_channel_id)
    return _log_channel


def log_info(title: str, description: str = ""):
    logger.info("%s: %s", title, description)


def log_warning(title: str, description: str = ""):
    logger.warning("%s: %s", title, description)


async def log_error(title: str, description: str = ""):
    logger.error("%s: %s", title, description)
    channel = await _get_channel()
    if not channel:
        return
    try:
        embed = discord.Embed(title=title, description=description, color=discord.Color.red(), timestamp=discord.utils.utcnow())
        await channel.send(embed=embed)
    except Exception as e:
        logger.warning("No se pudo enviar log de error a Discord: %s", e)
