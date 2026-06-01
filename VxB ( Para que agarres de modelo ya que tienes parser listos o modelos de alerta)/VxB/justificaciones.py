import asyncio
import asyncio
import logging
from datetime import datetime
from typing import Optional

import discord

from config import JUSTIFICACION_CHANNEL_ID
from database import (
    get_db_connection,
    listar_contexto_justificaciones_db,
)
from text_moderation import moderar_texto_sightengine

logger = logging.getLogger("ArmamentBot")

_bot = None
CHECK_EMOJI = "<a:Check:1490022575634382989>"
QUESTION_EMOJI = "❓"


def set_bot(bot_instance):
    global _bot
    _bot = bot_instance


def _resumen_texto(texto: str, limite: int = 120) -> str:
    texto = " ".join((texto or "").split())
    if len(texto) <= limite:
        return texto
    return texto[: limite - 3].rstrip() + "..."


def _limitar_label(texto: str, limite: int = 80) -> str:
    texto = " ".join((texto or "").split())
    if len(texto) <= limite:
        return texto
    return texto[: limite - 3].rstrip() + "..."


def _formatear_lista_opciones(opciones: list[dict]) -> str:
    if not opciones:
        return "Sin opciones disponibles."
    lineas = []
    for opcion in opciones[:10]:
        label = opcion.get("label") or "Sin nombre"
        estado = opcion.get("estado")
        if estado:
            lineas.append(f"- {label} [{estado}]")
        else:
            lineas.append(f"- {label}")
    return "\n".join(lineas)


def _formatear_label_boton(opcion: dict, limite: int = 80) -> str:
    label = opcion.get("label") or "Sin nombre"
    estado = opcion.get("estado")
    if estado:
        label = f"{label} [{estado}]"
    return _limitar_label(label, limite)


def _estilo_boton(opcion: dict):
    estado = str(opcion.get("estado") or "").strip().lower()
    if estado == "activo":
        return discord.ButtonStyle.success
    if estado == "programado":
        return discord.ButtonStyle.secondary
    return discord.ButtonStyle.primary if opcion.get("tipo") == "operativo" else discord.ButtonStyle.secondary


def _color_justificacion(operativos: list[dict], eventos: list[dict]) -> discord.Color:
    if operativos and eventos:
        return discord.Color.blurple()
    if operativos:
        return discord.Color.green()
    if eventos:
        return discord.Color.gold()
    return discord.Color.red()


def _titulo_justificacion(operativos: list[dict], eventos: list[dict]) -> str:
    if operativos and eventos:
        return "Clasificar Justificación"
    if operativos:
        return "Clasificar Operativo"
    if eventos:
        return "Clasificar Evento"
    return "Justificación sin contexto"


def _descripcion_justificacion(operativos: list[dict], eventos: list[dict]) -> str:
    if operativos and eventos:
        return "Elegí una opción real para clasificar tu texto entre operativos y eventos."
    if operativos:
        return "Hay operativos activos. Elegí uno para clasificar tu texto."
    if eventos:
        return "No hay operativos activos, pero sí eventos disponibles para clasificar tu texto."
    return "No hay operativos ni eventos disponibles en este momento."


async def _cargar_contexto_justificaciones() -> dict:
    logger.info("🔎 [Justificaciones] Cargando contexto de justificaciones")
    contexto = await asyncio.to_thread(listar_contexto_justificaciones_db)
    logger.info(
        "📚 [Justificaciones] Contexto cargado | operativos=%s | eventos=%s",
        len(contexto.get("operativos") or []),
        len(contexto.get("eventos") or []),
    )
    return contexto


async def moderar_texto_local(texto: str) -> dict:
    return await moderar_texto_sightengine(texto)


def _guardar_justificacion_db(
    *,
    discord_id: int,
    usuario: str,
    tipo: str,
    subtipo: str,
    texto: str,
    mensaje_origen_id: Optional[int],
    canal_origen_id: Optional[int],
):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO justificaciones_texto
            (discord_id, usuario, tipo, subtipo, texto, mensaje_origen_id, canal_origen_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """,
        (
            int(discord_id),
            usuario,
            tipo,
            subtipo,
            texto,
            mensaje_origen_id,
            canal_origen_id,
        ),
    )
    conn.commit()
    cursor.close()
    conn.close()


async def _guardar_justificacion_sheets(
    *,
    discord_id: int,
    usuario: str,
    tipo: str,
    subtipo: str,
    texto: str,
    mensaje_origen_id: Optional[int],
    canal_origen_id: Optional[int],
):
    try:
        from sheets import registrar_justificacion_texto

        await registrar_justificacion_texto(
            {
                "created_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "discord_id": discord_id,
                "usuario": usuario,
                "tipo": tipo,
                "subtipo": subtipo,
                "texto": texto,
                "mensaje_origen_id": mensaje_origen_id,
                "canal_origen_id": canal_origen_id,
            }
        )
    except Exception as e:
        logger.warning(f"⚠️ [Justificaciones] No se pudo sincronizar Sheets: {e}")


class JustificacionModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        tipo: str,
        subtipo: str,
        texto_original: str,
        mensaje_origen_id: int,
        canal_origen_id: int,
    ):
        super().__init__(title=f"Justificar {tipo.title()}")
        self.tipo = tipo
        self.subtipo = subtipo
        self.texto_original = texto_original
        self.mensaje_origen_id = mensaje_origen_id
        self.canal_origen_id = canal_origen_id
        self.justificacion = discord.ui.TextInput(
            label="Justificacion",
            placeholder="Escribi la justificacion final",
            default=texto_original[:1000],
            required=True,
            max_length=1000,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.justificacion)

    async def on_submit(self, interaction: discord.Interaction):
        texto = str(self.justificacion.value or "").strip()
        try:
            _guardar_justificacion_db(
                discord_id=interaction.user.id,
                usuario=interaction.user.name,
                tipo=self.tipo,
                subtipo=self.subtipo,
                texto=texto,
                mensaje_origen_id=self.mensaje_origen_id,
                canal_origen_id=self.canal_origen_id,
            )
            await _guardar_justificacion_sheets(
                discord_id=interaction.user.id,
                usuario=interaction.user.name,
                tipo=self.tipo,
                subtipo=self.subtipo,
                texto=texto,
                mensaje_origen_id=self.mensaje_origen_id,
                canal_origen_id=self.canal_origen_id,
            )
        except Exception as e:
            logger.error(f"❌ [Justificaciones] Error guardando justificacion: {e}", exc_info=True)
            try:
                await interaction.response.send_message("❌ No pude guardar la justificacion.", ephemeral=True)
            except Exception:
                pass
            return

        try:
            await interaction.response.send_message(
                (
                    f"✅ Justificacion guardada para **{self.tipo.title()}** "
                    f"(**{self.subtipo}**).\n"
                    f"**Texto:** {_resumen_texto(texto, 180)}"
                ),
            )
        except Exception:
            try:
                await interaction.followup.send(
                    (
                        f"✅ Justificacion guardada para **{self.tipo.title()}** "
                        f"(**{self.subtipo}**).\n"
                        f"**Texto:** {_resumen_texto(texto, 180)}"
                    ),
                )
            except Exception:
                pass


class JustificacionPromptView(discord.ui.View):
    def __init__(self, *, texto_original: str, mensaje_origen_id: int, canal_origen_id: int, opciones: list[dict]):
        super().__init__(timeout=900)
        self.texto_original = texto_original
        self.mensaje_origen_id = mensaje_origen_id
        self.canal_origen_id = canal_origen_id

        for index, opcion in enumerate(opciones[:25]):
            button = discord.ui.Button(
                label=_formatear_label_boton(opcion),
                style=_estilo_boton(opcion),
                row=index // 5,
            )

            async def _callback(interaction: discord.Interaction, *, _opcion=opcion):
                await interaction.response.send_modal(
                    JustificacionModal(
                        tipo=_opcion["tipo"],
                        subtipo=_opcion["subtipo"],
                        texto_original=self.texto_original,
                        mensaje_origen_id=self.mensaje_origen_id,
                        canal_origen_id=self.canal_origen_id,
                    )
                )

            button.callback = _callback
            self.add_item(button)


async def manejar_mensaje_justificacion(message: discord.Message) -> None:
    if message.channel.id != JUSTIFICACION_CHANNEL_ID:
        return
    if message.author.bot or message.webhook_id:
        return
    if not message.content or not message.content.strip():
        return

    texto = message.content.strip()
    logger.info(
        "📝 [Justificaciones] Mensaje recibido | autor=%s (%s) | canal=%s | len=%s",
        message.author,
        message.author.id,
        message.channel.id,
        len(texto),
    )
    try:
        moderacion = await moderar_texto_local(texto)
        flagged = bool(moderacion.get("toxic", False))
        logger.info(
            "🛡️ [Justificaciones] Moderación Sightengine | toxic=%s | score=%s | reasons=%s | used=%s",
            moderacion.get("toxic", False),
            moderacion.get("score"),
            moderacion.get("reasons"),
            moderacion.get("sightengine_used"),
        )
    except Exception as e:
        logger.warning(f"⚠️ [Justificaciones] Moderación Sightengine falló, dejo pasar el texto: {e}")
        flagged = False

    if flagged:
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await message.author.send(
                "⚠️ Tu texto fue bloqueado por moderación automática. "
                "Reescribilo sin insultos, amenazas ni contenido prohibido."
            )
        except Exception:
            pass
        logger.info(
            f"🛑 [Justificaciones] Texto bloqueado | Usuario={message.author} ({message.author.id}) | "
            f"Contenido={_resumen_texto(texto, 120)}"
        )
        return

    contexto = await _cargar_contexto_justificaciones()
    operativos = list(contexto.get("operativos") or [])
    eventos = list(contexto.get("eventos") or [])
    opciones = operativos + eventos
    logger.info(
        "🧭 [Justificaciones] Opciones resueltas | operativos=%s | eventos=%s | total=%s",
        len(operativos),
        len(eventos),
        len(opciones),
    )

    if not opciones:
        logger.warning(
            "⚠️ [Justificaciones] Sin opciones para el MD | autor=%s (%s) | texto=%s",
            message.author,
            message.author.id,
            _resumen_texto(texto, 120),
        )
        try:
            await message.add_reaction(QUESTION_EMOJI)
        except Exception:
            pass

        aviso_dm = (
            "No existe ningún OP o evento activo en este momento.\n"
            "Dejé tu mensaje marcado con ? y no te mostré el formulario porque no hay un destino real para clasificarlo."
        )
        try:
            await message.author.send(aviso_dm)
            logger.info(
                "📨 [Justificaciones] DM sin contexto enviado | autor=%s (%s)",
                message.author,
                message.author.id,
            )
        except discord.Forbidden:
            try:
                aviso = await message.channel.send(
                    f"{message.author.mention} no existe ningun OP o evento activo ahora mismo."
                )
                await asyncio.sleep(8)
                await aviso.delete()
            except Exception:
                pass
        except Exception as e:
            logger.error(f"❌ [Justificaciones] Error enviando aviso sin contexto activo: {e}", exc_info=True)
        return

    try:
        await message.add_reaction(CHECK_EMOJI)
    except Exception:
        pass
    logger.info(
        "📨 [Justificaciones] Formulario enviado por DM | autor=%s (%s) | opciones=%s",
        message.author,
        message.author.id,
        len(opciones),
    )

    embed = discord.Embed(
        title=_titulo_justificacion(operativos, eventos),
        description=_descripcion_justificacion(operativos, eventos),
        color=_color_justificacion(operativos, eventos),
        timestamp=datetime.now(),
    )
    embed.set_author(
        name=f"Mensaje de {message.author.display_name}",
        icon_url=message.author.display_avatar.url if message.author.display_avatar else None,
    )
    embed.add_field(
        name=f"Operativos ({len(operativos)})",
        value=_formatear_lista_opciones(operativos),
        inline=False,
    )
    embed.add_field(
        name=f"Eventos ({len(eventos)})",
        value=_formatear_lista_opciones(eventos),
        inline=False,
    )
    embed.add_field(name="Texto recibido", value=_resumen_texto(texto, 900) or "—", inline=False)
    embed.set_footer(text="Después de elegir, vas a poder editar la justificación en privado.")

    try:
        await message.author.send(
            embed=embed,
            view=JustificacionPromptView(
                texto_original=texto,
                mensaje_origen_id=message.id,
                canal_origen_id=message.channel.id,
                opciones=opciones,
            ),
        )
    except discord.Forbidden:
        try:
            aviso = await message.channel.send(
                f"{message.author.mention} activá los mensajes privados para que te pueda mandar el formulario."
            )
            await asyncio.sleep(8)
            await aviso.delete()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"❌ [Justificaciones] Error enviando prompt privado: {e}", exc_info=True)
