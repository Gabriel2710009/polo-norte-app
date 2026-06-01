import logging
import re
from typing import Optional

import discord

from config import EVENTOS_CHANNEL_ID, OPERATIVOS_CHANNEL_ID

logger = logging.getLogger("ArmamentBot")

CHECK_EMOJI = "<a:Check:1490022575634382989>"
DISCORD_EVENT_RE = re.compile(r"https?://(?:canary\.|ptb\.)?discord\.com/events/(?P<guild_id>\d{17,20})/(?P<event_id>\d{17,20})")


def extraer_event_id(texto: str) -> Optional[int]:
    if not texto:
        return None
    match = DISCORD_EVENT_RE.search(texto)
    if not match:
        return None
    try:
        return int(match.group("event_id"))
    except (TypeError, ValueError):
        return None


async def manejar_mensaje_evento(message: discord.Message) -> bool:
    """
    Detecta enlaces de eventos de Discord en los canales configurados.
    - En OPERATIVOS_CHANNEL_ID intenta registrar el evento como operativo.
    - En EVENTOS_CHANNEL_ID lo guarda como evento para contexto/justificaciones
      sin arrancar tracking de asistencia.
    """
    if message.author.bot or message.webhook_id:
        return False
    if message.channel.id not in {OPERATIVOS_CHANNEL_ID, EVENTOS_CHANNEL_ID}:
        return False

    event_id = extraer_event_id(message.content or "")
    if not event_id:
        return False

    try:
        await message.add_reaction(CHECK_EMOJI)
    except Exception:
        pass

    if message.channel.id == OPERATIVOS_CHANNEL_ID:
        from asistencia import registrar_evento_operativo

        ok = await registrar_evento_operativo(
            event_id,
            tipo="operativo",
            canal_id=message.channel.id,
        )
        if ok:
            logger.info(
                f"📆 [Eventos] Link detectado como OPERATIVO | Canal={message.channel.id} | Evento={event_id} | Autor={message.author} ({message.author.id})"
            )
        else:
            logger.warning(
                f"⚠️ [Eventos] No se pudo registrar operativo desde el enlace | Canal={message.channel.id} | Evento={event_id}"
            )
        return True

    from asistencia import registrar_evento_operativo

    ok = await registrar_evento_operativo(
        event_id,
        tipo="evento",
        canal_id=message.channel.id,
        iniciar_tracking=False,
    )
    if ok:
        logger.info(
            f"📆 [Eventos] Link detectado como EVENTO | Canal={message.channel.id} | Evento={event_id} | Autor={message.author} ({message.author.id})"
        )
    else:
        logger.warning(
            f"⚠️ [Eventos] No se pudo registrar evento desde el enlace | Canal={message.channel.id} | Evento={event_id}"
        )
    return True
