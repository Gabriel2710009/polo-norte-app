import asyncio
import logging
import sys

from config import LOG_LEVEL, BOT_LOGS_CHANNEL_ID, DEVELOPER_USER_IDS


# ─── HANDLER DE DISCORD ───────────────────────────────────────

# Mensajes de warning que NO se mandan al canal Discord (solo Railway)
_WARNINGS_IGNORADOS_DISCORD = (
    "Maximum number of edits",         # rate limit 30046 mensajes viejos
    "No se pudo obtener miembro",      # miembro no encontrado (normal)
    "No se pudo borrar mensaje",       # delete fallido (normal)
    "No se pudo enviar log de acción", # log_accion fallido
    "Webhook expirado",                # discord webhook timeout
    "Too Many Requests",               # rate limit genérico
)


class DiscordLogHandler(logging.Handler):
    """
    Envía solo ERROR y CRITICAL al canal Discord.
    Los WARNING quedan solo en Railway (stdout).
    """

    def __init__(self):
        super().__init__()
        self.bot_ready = False
        self._loop     = None
        self._bot_ref  = None

    def emit(self, record):
        # Solo ERROR y CRITICAL van al canal Discord
        if record.levelno < logging.ERROR:
            return
        if not self.bot_ready or self._loop is None:
            return
        # Filtrar errores de conexión a BD (muy verbosos, ya están en Railway)
        msg_lower = record.getMessage().lower()
        if "max client connections" in msg_lower or "maxclientsinsessionmode" in msg_lower:
            return
        asyncio.run_coroutine_threadsafe(self._build_and_send(record), self._loop)

    async def _build_and_send(self, record):
        if self._bot_ref is None:
            return
        try:
            channel = self._bot_ref.get_channel(BOT_LOGS_CHANNEL_ID)
            prefijo = "💥" if record.levelno >= logging.CRITICAL else "🔴"
            mensaje = record.getMessage()
            if len(mensaje) > 1800:
                mensaje = mensaje[:1800] + "..."

            import discord as _discord
            embed = _discord.Embed(
                title=f"{prefijo} {record.levelname}",
                description=f"```\n{mensaje}\n```",
                color=_discord.Color.dark_red() if record.levelno >= logging.CRITICAL else _discord.Color.red(),
            )
            embed.set_footer(text=f"{record.name} | {record.filename}:{record.lineno}")

            # Agregar traceback si hay excepción
            if record.exc_info:
                import traceback
                tb = "".join(traceback.format_exception(*record.exc_info))
                if len(tb) > 900:
                    tb = "..." + tb[-900:]
                embed.add_field(name="Traceback", value=f"```py\n{tb}\n```", inline=False)

            if channel:
                await channel.send(embed=embed)
            await self._send_developer_dms(embed)
        except Exception:
            pass

    async def _send_developer_dms(self, embed):
        for user_id in DEVELOPER_USER_IDS:
            try:
                user = self._bot_ref.get_user(int(user_id))
                if user is None:
                    user = await self._bot_ref.fetch_user(int(user_id))
                if user:
                    await user.send(embed=embed)
            except Exception:
                pass


# ─── CONFIGURACIÓN GLOBAL ─────────────────────────────────────

discord_log_handler = DiscordLogHandler()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s │ %(levelname)-8s │ %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Silenciar ruido de discord.py
for _name in ("discord", "discord.http", "discord.gateway", "discord.client", "discord.state"):
    logging.getLogger(_name).setLevel(logging.ERROR)

logger = logging.getLogger("ArmamentBot")
logger.addHandler(discord_log_handler)
