import os
import json
import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ui import View, Modal, TextInput

from database import entrevistas_db
from services import aprobar as aprobar_module

logger = logging.getLogger("EntrevistasCog")

ROL_AUTORIZADO_ID = 1307612928211554386

EMOJIS = {
    "BIEN": "<:bien:1414589831983661198>",
    "MAL": "<:mal:1414589888661291081>",
    "REGULAR": "<:regular:1414590371748647043>",
}

ESTADO_ACTIVA = "ACTIVA"
ESTADO_EXPIRADA = "EXPIRADA"
ESTADO_FINALIZADA = "FINALIZADA"
ESTADO_ABANDONADA = "ABANDONADA"
ESTADOS_RECUPERABLES = (ESTADO_ACTIVA, ESTADO_EXPIRADA)


@dataclass
class InterviewSession:
    user_id: int
    staff_id: int
    channel_id: int
    guild_id: int
    questions: list[dict]
    current_index: int = 0
    answers: list[str] = field(default_factory=list)
    motives: dict[int, str] = field(default_factory=dict)
    intento: int = 1
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    processing: bool = False
    answered_current: bool = False
    message: discord.Message | None = None
    message_id: int | None = None
    client: discord.Client | None = None
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    estado: str = ESTADO_ACTIVA
    last_updated: datetime | None = None


active_interviews: dict[int, InterviewSession] = {}


def _session_a_datos(session: InterviewSession) -> dict:
    return {
        "user_id": str(session.user_id),
        "staff_id": str(session.staff_id),
        "channel_id": str(session.channel_id),
        "guild_id": str(session.guild_id),
        "session_id": session.session_id,
        "questions": session.questions,
        "current_index": session.current_index,
        "answers": session.answers,
        "motives": session.motives,
        "intento": session.intento,
        "started_at": session.started_at,
        "estado": session.estado,
    }


def _datos_a_session(
    datos: dict,
    client: discord.Client | None = None,
) -> InterviewSession:
    try:
        questions = datos.get("questions", [])
        if isinstance(questions, str):
            questions = json.loads(questions)
        answers = datos.get("answers", [])
        if isinstance(answers, str):
            answers = json.loads(answers)
        motives_raw = datos.get("motives", {})
        if isinstance(motives_raw, str):
            motives_raw = json.loads(motives_raw)
        motives = {int(k): v for k, v in (motives_raw or {}).items()}
    except Exception:
        logger.exception("Error deserializando sesi\u00f3n de entrevista: user=%s", datos.get("user_id"))
        questions, answers, motives = [], [], {}

    started_at = datos.get("started_at")
    if isinstance(started_at, str):
        try:
            started_at = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        except Exception:
            started_at = datetime.now(timezone.utc)

    last_updated = datos.get("updated_at")
    if isinstance(last_updated, str):
        try:
            last_updated = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
        except Exception:
            last_updated = None

    return InterviewSession(
        user_id=int(datos["user_id"]),
        staff_id=int(datos.get("staff_id") or 0),
        channel_id=int(datos.get("channel_id") or 0),
        guild_id=int(datos.get("guild_id") or 0),
        questions=questions,
        current_index=int(datos.get("current_index", 0)),
        answers=answers,
        motives=motives,
        intento=int(datos.get("intento", 1)),
        started_at=started_at,
        session_id=datos.get("session_id") or uuid.uuid4().hex,
        estado=datos.get("estado", ESTADO_ACTIVA),
        client=client,
        last_updated=last_updated,
    )


def _persistir_sesion(session: InterviewSession):
    session.last_updated = datetime.now(timezone.utc)
    try:
        entrevistas_db.guardar_sesion_entrevista(_session_a_datos(session))
    except Exception:
        logger.exception(
            "Error persistiendo sesi\u00f3n de entrevista: user=%s",
            session.user_id,
        )


def _limpiar_sesion_persistida(session: InterviewSession):
    try:
        entrevistas_db.eliminar_sesion_entrevista(str(session.user_id))
    except Exception:
        logger.exception(
            "Error eliminando sesi\u00f3n persistida de entrevista: user=%s",
            session.user_id,
        )


def _mensaje_no_activa(session: InterviewSession) -> str:
    if session.estado == ESTADO_EXPIRADA:
        return (
            "La entrevista expir\u00f3. Us\u00e1 `/recuperar_entrevista` "
            "para continuarla."
        )
    return "La entrevista ya termin\u00f3."

postulacion_config_cache: dict[str, int] = {
    "log_channel_id": 0,
    "errores_channel_id": 0,
}

def _load_config():
    global postulacion_config_cache
    try:
        config = entrevistas_db.cargar_configuracion()
        postulacion_config_cache = {
            "log_channel_id": int(config.get("log_channel_id", 0)),
            "errores_channel_id": int(config.get("errores_channel_id", 0)),
        }
        logger.info(
            "Configuraci\u00f3n de postulaci\u00f3n cargada: log=%s err=%s",
            postulacion_config_cache["log_channel_id"],
            postulacion_config_cache["errores_channel_id"],
        )
        if os.getenv("ENTREVISTAS_LOG_CHANNEL_ID") or os.getenv("ENTREVISTAS_POSTULACION_CHANNEL_ID") or os.getenv("ENTREVISTAS_ERRORES_CHANNEL_ID"):
            logger.warning(
                "Las variables de entorno ENTREVISTAS_*_CHANNEL_ID est\u00e1n obsoletas. "
                "Us\u00e1 /config_postulacion para configurar los canales."
            )
    except Exception as e:
        logger.warning("Error cargando configuraci\u00f3n de postulaci\u00f3n: %s", e)


def tiene_permiso_entrevista(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(role.id == ROL_AUTORIZADO_ID for role in member.roles)


def calcular_resultado(session: InterviewSession) -> dict[str, Any]:
    bien = 0
    regular = 0
    mal = 0

    for respuesta in session.answers:
        if respuesta == "BIEN":
            bien += 1
        elif respuesta == "REGULAR":
            regular += 1
        elif respuesta == "MAL":
            mal += 1

    errores = mal + (regular // 2)
    resultado = "APROBADO" if errores <= 3 else "NO_APROBADO"

    return {
        "resultado": resultado,
        "errores": errores,
        "bien": bien,
        "regular": regular,
        "mal": mal,
    }


def _build_preguntas_log_text(session: InterviewSession) -> str:
    partes: list[str] = []
    for i, q in enumerate(session.questions, 1):
        texto = q["pregunta"]
        cat = q.get("categoria", "?")
        partes.append(f"{i}. {texto}\n   Categor\u00eda: {cat}")
    return "\n\n".join(partes)


def _agregar_preguntas_al_embed(embed: discord.Embed, session: InterviewSession):
    texto = _build_preguntas_log_text(session)
    if not texto:
        return
    limites = 1024
    if len(texto) <= limites:
        embed.add_field(name="\U0001f4cb Preguntas realizadas", value=texto, inline=False)
    else:
        partes = []
        for linea in texto.split("\n\n"):
            if partes and len(partes[-1]) + len(linea) + 2 > limites:
                partes.append("")
            if partes and partes[-1]:
                partes[-1] += "\n\n" + linea
            else:
                partes.append(linea)
        total = len(partes)
        for i, p in enumerate(partes, 1):
            embed.add_field(
                name=f"\U0001f4cb Preguntas realizadas ({i}/{total})",
                value=p, inline=False,
            )


def _build_pregunta_embed(pregunta: dict) -> discord.Embed:
    embed = discord.Embed(
        title="\u2753 Pregunta",
        description=pregunta["pregunta"],
        color=discord.Color.blue(),
    )
    embed.add_field(name="Categor\u00eda", value=pregunta.get("categoria", "?"), inline=True)
    resp = pregunta.get("respuesta_esperada", "")
    if resp:
        embed.add_field(name="Respuesta esperada", value=resp, inline=False)
    return embed


def mostrar_pregunta_actual(session: InterviewSession) -> discord.Embed:
    if session.current_index >= len(session.questions):
        return discord.Embed(
            title="\U0001f4cb Entrevista finalizada",
            description="Procesando resultado...",
            color=discord.Color.green(),
        )

    pregunta = session.questions[session.current_index]
    total = len(session.questions)

    embed = discord.Embed(
        title=f"\U0001f4cb Entrevista - Pregunta {session.current_index + 1}/{total}",
        description=pregunta["pregunta"],
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Categor\u00eda", value=pregunta["categoria"], inline=True)
    resp = pregunta.get("respuesta_esperada", "")
    if resp:
        embed.add_field(name="Respuesta esperada", value=resp, inline=False)
    embed.set_footer(text=f"Intento {session.intento}/{entrevistas_db.MAX_INTENTOS}")
    return embed


def _agrupar_respuestas(session):
    grupos = {"GENERAL": [], "ARMERIA": [], "CASOS_PRACTICOS": []}
    contadores = {"GENERAL": 1, "ARMERIA": 1, "CASOS_PRACTICOS": 1}
    for i, (q, a) in enumerate(zip(session.questions, session.answers)):
        cat = q["categoria"]
        num = contadores[cat]
        grupos[cat].append({
            "num": num,
            "idx": i,
            "emoji_str": f"{num}. {EMOJIS.get(a, '\u2753')}",
        })
        contadores[cat] += 1
    return grupos


def _plantilla_postulacion(session, resultado):
    grupos = _agrupar_respuestas(session)
    emojis_leyenda = (
        f"{EMOJIS['BIEN']} Bien\n"
        f"{EMOJIS['MAL']} Mal\n"
        f"{EMOJIS['REGULAR']} Regular\n"
    )
    lineas = [emojis_leyenda, ""]
    for cat in ("GENERAL", "ARMERIA", "CASOS_PRACTICOS"):
        for item in grupos[cat]:
            lineas.append(f"> {item['emoji_str']}")
        lineas.append("-----------")

    lineas.append("")
    lineas.append(f"**Entrevistado:**\n<@{session.user_id}>")
    lineas.append("")
    lineas.append(f"**Entrevistado por:**\n<@{session.staff_id}>")
    return "\n".join(lineas)


def _plantilla_errores(session, resultado):
    grupos = _agrupar_respuestas(session)
    emojis_leyenda = (
        f"{EMOJIS['BIEN']} Bien\n"
        f"{EMOJIS['MAL']} Mal\n"
        f"{EMOJIS['REGULAR']} Regular\n"
    )
    lineas = [emojis_leyenda, ""]
    for cat in ("GENERAL", "ARMERIA", "CASOS_PRACTICOS"):
        for item in grupos[cat]:
            lineas.append(f"> {item['emoji_str']}")
            motivo = session.motives.get(item["idx"])
            if motivo:
                lineas.append(f"  Motivo: {motivo}")
        lineas.append("-----------")

    lineas.append("")
    lineas.append(f"**Entrevistado:**\n<@{session.user_id}>")
    lineas.append("")
    lineas.append(f"**Entrevistado por:**\n<@{session.staff_id}>")
    return "\n".join(lineas)


async def _obtener_mensaje_entrevista(
    session: InterviewSession,
    interaction: discord.Interaction | None = None,
) -> discord.Message | None:
    if interaction is not None:
        try:
            message = await interaction.original_response()
        except Exception:
            logger.exception(
                "No se pudo vincular el mensaje a la interacci\u00f3n actual: user=%s",
                session.user_id,
            )
        else:
            session.message = message
            session.message_id = message.id
            if message.channel is not None:
                session.channel_id = message.channel.id
            return message

    if session.message is not None:
        return session.message

    if session.message_id is None or session.client is None:
        return None

    channel = session.client.get_channel(session.channel_id)
    if channel is None:
        logger.warning(
            "No se pudo resolver el canal %s para reconstruir la entrevista: user=%s",
            session.channel_id, session.user_id,
        )
        return None

    try:
        message = await channel.fetch_message(session.message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        logger.exception(
            "No se pudo reconstruir el mensaje de la entrevista: user=%s",
            session.user_id,
        )
        return None

    session.message = message
    return message


def _marcar_interfaz_expirada(session: InterviewSession, motivo: str):
    if session.estado != ESTADO_ACTIVA:
        return
    session.estado = ESTADO_EXPIRADA
    active_interviews.pop(session.user_id, None)
    _persistir_sesion(session)
    logger.warning(
        "[ENTREVISTA] Sesi\u00f3n expirada, recuperaci\u00f3n disponible v\u00eda comando (%s): "
        "user=%s staff=%s session=%s",
        motivo, session.user_id, session.staff_id, session.session_id,
    )


async def _editar_mensaje_entrevista(
    session: InterviewSession,
    interaction: discord.Interaction | None = None,
    *,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
):
    if interaction is not None and not interaction.response.is_done():
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            logger.warning(
                "No se pudo diferir la interacci\u00f3n antes de editar la entrevista: user=%s",
                session.user_id,
            )

    message = await _obtener_mensaje_entrevista(session, interaction)
    if message is None:
        logger.warning("No se pudo obtener el mensaje de la entrevista para editarlo: user=%s",
            session.user_id,
        )
        return

    try:
        await message.edit(embed=embed, view=view)
    except discord.NotFound:
        logger.warning("Mensaje de entrevista no encontrado al editar: user=%s",
            session.user_id,
        )
        _marcar_interfaz_expirada(session, "mensaje no encontrado")
    except discord.Forbidden:
        logger.warning("Sin permisos para editar el mensaje de entrevista: user=%s",
            session.user_id,
        )
        _marcar_interfaz_expirada(session, "sin permisos")
    except discord.HTTPException:
        logger.exception(
            "Error HTTP editando mensaje de entrevista: user=%s",
            session.user_id,
        )


async def finalizar_entrevista(session: InterviewSession):
    session.estado = ESTADO_FINALIZADA
    try:
        await _actualizar_y_detener_panel(session)
    except Exception:
        logger.exception(
            "[ENTREVISTA] Error actualizando panel al finalizar: user=%s",
            session.user_id,
        )
    bot = session.client
    if bot is None:
        logger.error(
            "No hay cliente disponible para finalizar la entrevista: user=%s",
            session.user_id,
        )
        active_interviews.pop(session.user_id, None)
        return
    guild = bot.get_guild(session.guild_id)
    if not guild:
        logger.warning("Guild %s no encontrada para finalizar entrevista", session.guild_id)
        active_interviews.pop(session.user_id, None)
        return

    resultado = calcular_resultado(session)

    try:
        entrevistas_db.guardar_entrevista(
            entrevistado_id=str(session.user_id),
            entrevistador_id=str(session.staff_id),
            canal_id=str(session.channel_id),
            resultado=resultado["resultado"],
            intento=session.intento,
            total_errores=resultado["errores"],
            preguntas_used=session.questions,
            respuestas=session.answers,
            motivos=session.motives,
        )
    except Exception:
        logger.exception("Error guardando entrevista en DB: user=%s", session.user_id)

    channel = bot.get_channel(session.channel_id)
    miembro = guild.get_member(session.user_id)
    staff = guild.get_member(session.staff_id)

    try:
        plantilla_post = _plantilla_postulacion(session, resultado)
    except Exception:
        logger.exception("Error generando plantilla de postulaci\u00f3n")
        plantilla_post = ""

    try:
        plantilla_err = _plantilla_errores(session, resultado)
    except Exception:
        logger.exception("Error generando plantilla de errores")
        plantilla_err = ""

    logger.info(
        "Enviando plantilla al ticket: user=%s channel_id=%s",
        session.user_id, session.channel_id,
    )

    post_channel = bot.get_channel(session.channel_id)
    if not post_channel:
        logger.warning("No se encontr\u00f3 el canal del ticket: %s", session.channel_id)
    elif not isinstance(post_channel, discord.TextChannel):
        logger.warning("El canal del ticket no es TextChannel: %s", session.channel_id)
    elif not post_channel.permissions_for(guild.me).send_messages:
        logger.warning("Bot sin permisos de env\u00edo en ticket %s", post_channel.id)
    else:
        try:
            await post_channel.send(plantilla_post)
        except Exception as e:
            logger.warning("Error enviando plantilla a canal postulaci\u00f3n: %s", e)

    err_channel = bot.get_channel(postulacion_config_cache["errores_channel_id"]) if postulacion_config_cache["errores_channel_id"] else None

    if err_channel and isinstance(err_channel, discord.TextChannel):
        try:
            await err_channel.send(plantilla_err)
        except Exception as e:
            logger.warning("Error enviando plantilla a canal errores: %s", e)

    log_channel = bot.get_channel(postulacion_config_cache["log_channel_id"]) if postulacion_config_cache["log_channel_id"] else None

    if resultado["resultado"] == "APROBADO":
        if miembro and staff and isinstance(channel, discord.TextChannel):
            try:
                aprobacion = await aprobar_module.ejecutar_aprobacion(
                    member=miembro, admin=staff, channel=channel, origen="entrevista",
                )
                logger.info(
                    "Aprobaci\u00f3n por entrevista: user=%s staff=%s resultado=%s",
                    session.user_id, session.staff_id, aprobacion,
                )
            except Exception as e:
                logger.warning("Error en aprobaci\u00f3n por entrevista: %s", e)
        else:
            logger.warning(
                "Aprobaci\u00f3n saltada: miembro=%s staff=%s channel=%s",
                miembro is not None, staff is not None,
                isinstance(channel, discord.TextChannel) if channel else False,
            )

        try:
            entrevistas_db.restablecer_intentos(str(session.user_id))
        except Exception:
            logger.exception("Error restableciendo intentos para %s", session.user_id)

        if log_channel and isinstance(log_channel, discord.TextChannel):
            embed = discord.Embed(
                title="\u2705 Entrevista aprobada",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Entrevistado", value=f"<@{session.user_id}>", inline=True)
            embed.add_field(name="Entrevistador", value=f"<@{session.staff_id}>", inline=True)
            embed.add_field(name="Resultado", value=f"APROBADO ({resultado['errores']} errores)", inline=False)
            embed.add_field(name="Intento", value=str(session.intento), inline=True)
            embed.add_field(name="Fecha", value=discord.utils.utcnow().strftime("%d/%m/%Y %H:%M UTC"), inline=True)
            _agregar_preguntas_al_embed(embed, session)
            try:
                await log_channel.send(embed=embed)
            except Exception as e:
                logger.warning("Error enviando log de aprobaci\u00f3n: %s", e)
    else:
        try:
            entrevistas_db.incrementar_intento(str(session.user_id))
        except Exception:
            logger.exception("Error incrementando intento para %s", session.user_id)

        if channel and isinstance(channel, discord.TextChannel):
            try:
                await channel.send(
                    f"\u274c **Entrevista no aprobada**\n\n"
                    f"Gracias por participar en la entrevista.\n\n"
                    f"En este momento no cumpliste con los requisitos necesarios para ingresar.\n\n"
                    f"Podr\u00e1s volver a intentarlo nuevamente dentro de **24 horas**.\n\n"
                    f"Si despu\u00e9s de 24 hs del plazo no solicitas una nueva entrevista, "
                    f"este ticket ser\u00e1 cerrado.\n\n"
                    f"**Intento:** {session.intento}/{entrevistas_db.MAX_INTENTOS}"
                )
            except Exception as e:
                logger.warning("Error enviando mensaje de rechazo: %s", e)

        if log_channel and isinstance(log_channel, discord.TextChannel):
            embed = discord.Embed(
                title="\u274c Entrevista no aprobada",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Entrevistado", value=f"<@{session.user_id}>", inline=True)
            embed.add_field(name="Entrevistador", value=f"<@{session.staff_id}>", inline=True)
            embed.add_field(name="Resultado", value=f"NO APROBADO ({resultado['errores']} errores)", inline=False)
            embed.add_field(name="Intento", value=f"{session.intento}/{entrevistas_db.MAX_INTENTOS}", inline=True)
            embed.add_field(name="Fecha", value=discord.utils.utcnow().strftime("%d/%m/%Y %H:%M UTC"), inline=True)
            _agregar_preguntas_al_embed(embed, session)
            try:
                await log_channel.send(embed=embed)
            except Exception as e:
                logger.warning("Error enviando log de rechazo: %s", e)

    try:
        active_interviews.pop(session.user_id, None)
    except Exception as e:
        logger.warning("Error limpiando sesi\u00f3n activa: %s", e)

    _limpiar_sesion_persistida(session)

    logger.info(
        "[ENTREVISTA] Sesión finalizada: user=%s staff=%s resultado=%s errores=%s "
        "intento=%s session=%s",
        session.user_id, session.staff_id,
        resultado["resultado"], resultado["errores"], session.intento,
        session.session_id,
    )


class PreguntaModal(Modal, title="Agregar pregunta"):
    def __init__(self, categoria: str):
        super().__init__()
        self.categoria = categoria
        self.pregunta_input = TextInput(
            label=f"Pregunta ({categoria})",
            style=discord.TextStyle.paragraph,
            placeholder="Escribe la pregunta...",
            max_length=4000,
            required=True,
        )
        self.add_item(self.pregunta_input)
        self.respuesta_input = TextInput(
            label="Respuesta esperada",
            style=discord.TextStyle.paragraph,
            placeholder="Escribe la respuesta esperada...",
            max_length=4000,
            required=False,
        )
        self.add_item(self.respuesta_input)

    async def on_submit(self, interaction: discord.Interaction):
        texto = self.pregunta_input.value.strip()
        if not texto:
            await interaction.response.send_message("\u274c La pregunta no puede estar vac\u00eda.", ephemeral=True)
            return
        try:
            q_id = entrevistas_db.agregar_pregunta(
                pregunta=texto,
                categoria=self.categoria,
                creado_por=str(interaction.user.id),
                respuesta_esperada=self.respuesta_input.value.strip(),
            )
            await interaction.response.send_message(
                f"\u2705 Pregunta agregada correctamente (ID: {q_id})",
                ephemeral=True,
            )
            logger.info("Pregunta creada: id=%s categoria=%s por %s", q_id, self.categoria, interaction.user)
        except Exception as e:
            logger.warning("Error al agregar pregunta: %s", e)
            await interaction.response.send_message(
                "\u274c Ocurri\u00f3 un error al guardar la pregunta.", ephemeral=True,
            )


class EditarPreguntaModal(Modal, title="Editar pregunta"):
    def __init__(self, pregunta_id: int, texto_actual: str, categoria_actual: str, respuesta_actual: str):
        super().__init__()
        self.pregunta_id = pregunta_id
        self.pregunta_input = TextInput(
            label="Pregunta",
            style=discord.TextStyle.paragraph,
            default=texto_actual,
            max_length=4000,
            required=True,
        )
        self.add_item(self.pregunta_input)
        self.categoria_input = TextInput(
            label="Categor\u00eda (GENERAL, ARMERIA, CASOS_PRACTICOS)",
            placeholder="Ej: GENERAL",
            default=categoria_actual,
            max_length=20,
            required=True,
        )
        self.add_item(self.categoria_input)
        self.respuesta_input = TextInput(
            label="Respuesta esperada",
            style=discord.TextStyle.paragraph,
            default=respuesta_actual,
            max_length=4000,
            required=False,
        )
        self.add_item(self.respuesta_input)

    async def on_submit(self, interaction: discord.Interaction):
        texto = self.pregunta_input.value.strip()
        cat = self.categoria_input.value.strip().upper()
        if not texto:
            await interaction.response.send_message("\u274c La pregunta no puede estar vac\u00eda.", ephemeral=True)
            return
        if cat not in entrevistas_db.CATEGORIAS_VALIDAS:
            await interaction.response.send_message(
                f"\u274c Categor\u00eda inv\u00e1lida. V\u00e1lidas: {', '.join(sorted(entrevistas_db.CATEGORIAS_VALIDAS))}",
                ephemeral=True,
            )
            return
        try:
            actualizado = entrevistas_db.editar_pregunta(
                self.pregunta_id, texto,
                nueva_categoria=cat,
                respuesta_esperada=self.respuesta_input.value.strip(),
            )
            if actualizado:
                await interaction.response.send_message(
                    f"\u2705 Pregunta ID {self.pregunta_id} actualizada.", ephemeral=True,
                )
                logger.info("Pregunta editada: id=%s por %s", self.pregunta_id, interaction.user)
            else:
                await interaction.response.send_message(
                    "\u274c No se encontr\u00f3 la pregunta para editar.", ephemeral=True,
                )
        except Exception as e:
            logger.warning("Error al editar pregunta %s: %s", self.pregunta_id, e)
            await interaction.response.send_message(
                "\u274c Ocurri\u00f3 un error al editar la pregunta.", ephemeral=True,
            )


class ReasonModal(Modal, title="Motivo de la respuesta"):
    def __init__(self, session: InterviewSession, tipo: str):
        super().__init__()
        self.session = session
        self.tipo = tipo
        self.motivo_input = TextInput(
            label="Motivo (opcional)",
            style=discord.TextStyle.paragraph,
            placeholder="Escribe el motivo de tu evaluaci\u00f3n...",
            max_length=500,
            required=False,
        )
        self.add_item(self.motivo_input)

    async def on_submit(self, interaction: discord.Interaction):
        if self.session.estado != ESTADO_ACTIVA:
            await interaction.response.send_message(
                _mensaje_no_activa(self.session),
                ephemeral=True,
            )
            return
        if self.session.answered_current:
            await interaction.response.send_message(
                "Esta pregunta ya fue procesada.",
                ephemeral=True,
            )
            return
        self.session.answered_current = True
        try:
            self.session.answers.append(self.tipo)
            texto = self.motivo_input.value.strip()
            if texto:
                self.session.motives[self.session.current_index] = texto
            self.session.current_index += 1
            _persistir_sesion(self.session)

            await interaction.response.defer()

            if self.session.current_index >= len(self.session.questions):
                embed = discord.Embed(
                    title="\U0001f4cb Entrevista finalizada",
                    description="Procesando resultado...",
                    color=discord.Color.green(),
                )
                self.session.estado = ESTADO_FINALIZADA
                await _editar_mensaje_entrevista(self.session, interaction, embed=embed, view=None)
                await finalizar_entrevista(self.session)
            else:
                embed = mostrar_pregunta_actual(self.session)
                view = QuestionView(self.session)
                await _editar_mensaje_entrevista(self.session, interaction, embed=embed, view=view)
                self.session.answered_current = False
        finally:
            self.session.processing = False

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        self.session.processing = False
        logger.exception("Error en ReasonModal: %s", error)


class QuestionView(View):
    def __init__(self, session: InterviewSession):
        super().__init__(timeout=900)
        self.session = session

    async def on_timeout(self):
        if active_interviews.get(self.session.user_id) is not self.session:
            return
        self.session.estado = ESTADO_EXPIRADA
        active_interviews.pop(self.session.user_id, None)
        _persistir_sesion(self.session)
        logger.info(
            "[ENTREVISTA] Sesi\u00f3n expirada, recuperaci\u00f3n disponible v\u00eda comando (timeout): "
            "user=%s staff=%s session=%s",
            self.session.user_id, self.session.staff_id, self.session.session_id,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.session.estado != ESTADO_ACTIVA:
            await interaction.response.send_message(
                _mensaje_no_activa(self.session),
                ephemeral=True,
            )
            return False

        if interaction.user.id != self.session.staff_id:
            await interaction.response.send_message(
                "No tienes permiso para controlar esta entrevista.",
                ephemeral=True,
            )
            return False

        if self.session.processing:
            await interaction.response.send_message(
                "Esta pregunta ya est\u00e1 siendo procesada.",
                ephemeral=True,
            )
            return False

        if self.session.answered_current:
            await interaction.response.send_message(
                "Esta pregunta ya fue procesada.",
                ephemeral=True,
            )
            return False

        return True

    async def _avanzar(self, interaction: discord.Interaction, respuesta: str):
        if self.session.estado != ESTADO_ACTIVA:
            await interaction.response.send_message(
                _mensaje_no_activa(self.session),
                ephemeral=True,
            )
            return
        if self.session.answered_current:
            await interaction.response.send_message(
                "Esta pregunta ya fue procesada.",
                ephemeral=True,
            )
            return
        self.session.answered_current = True
        self.session.processing = True
        try:
            self.session.answers.append(respuesta)
            self.session.current_index += 1
            _persistir_sesion(self.session)

            if self.session.current_index >= len(self.session.questions):
                embed = discord.Embed(
                    title="\U0001f4cb Entrevista finalizada",
                    description="Procesando resultado...",
                    color=discord.Color.green(),
                )
                self.session.estado = ESTADO_FINALIZADA
                await _editar_mensaje_entrevista(self.session, interaction, embed=embed, view=None)
                await finalizar_entrevista(self.session)
            else:
                embed = mostrar_pregunta_actual(self.session)
                await _editar_mensaje_entrevista(self.session, interaction, embed=embed, view=self)
                self.session.answered_current = False
        finally:
            self.session.processing = False

    @discord.ui.button(
        emoji=discord.PartialEmoji(name="bien", id=1414589831983661198),
        style=discord.ButtonStyle.success,
        label="Bien",
    )
    async def bien(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._avanzar(interaction, "BIEN")

    @discord.ui.button(
        emoji=discord.PartialEmoji(name="regular", id=1414590371748647043),
        style=discord.ButtonStyle.secondary,
        label="Regular",
    )
    async def regular(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.session.estado != ESTADO_ACTIVA:
            await interaction.response.send_message(
                _mensaje_no_activa(self.session),
                ephemeral=True,
            )
            return
        if self.session.processing:
            await interaction.response.send_message(
                "Esta pregunta ya est\u00e1 siendo procesada.",
                ephemeral=True,
            )
            return
        self.session.processing = True
        await interaction.response.send_modal(ReasonModal(self.session, "REGULAR"))

    @discord.ui.button(
        emoji=discord.PartialEmoji(name="mal", id=1414589888661291081),
        style=discord.ButtonStyle.danger,
        label="Mal",
    )
    async def mal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.session.estado != ESTADO_ACTIVA:
            await interaction.response.send_message(
                _mensaje_no_activa(self.session),
                ephemeral=True,
            )
            return
        if self.session.processing:
            await interaction.response.send_message(
                "Esta pregunta ya est\u00e1 siendo procesada.",
                ephemeral=True,
            )
            return
        self.session.processing = True
        await interaction.response.send_modal(ReasonModal(self.session, "MAL"))


_recovery_locks: dict[int, asyncio.Lock] = {}


def _get_recovery_lock(user_id: int) -> asyncio.Lock:
    lock = _recovery_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _recovery_locks[user_id] = lock
    return lock


async def _recuperar_sesion(
    interaction: discord.Interaction,
    usuario: discord.Member,
    session: InterviewSession,
) -> bool:
    async with _get_recovery_lock(session.user_id):
        logger.info(
            "[ENTREVISTA] Recuperaci\u00f3n solicitada: "
            "user=%s autor=%s session=%s",
            session.user_id, interaction.user.id, session.session_id,
        )
        if interaction.user.id != session.staff_id:
            await interaction.response.send_message(
                "No ten\u00e9s permisos para controlar esta entrevista.",
                ephemeral=True,
            )
            logger.info(
                "[ENTREVISTA] Recuperaci\u00f3n denegada (no es el entrevistador): "
                "user=%s staff=%s autor=%s session=%s",
                session.user_id, session.staff_id, interaction.user.id, session.session_id,
            )
            return False

        existente = active_interviews.get(session.user_id)
        if existente is not None and existente.estado == ESTADO_ACTIVA:
            await interaction.response.send_message(
                "Este usuario ya tiene una entrevista en curso con interfaz activa.",
                ephemeral=True,
            )
            logger.info(
                "[ENTREVISTA] Recuperaci\u00f3n denegada (ya hay interfaz activa): "
                "user=%s session=%s",
                session.user_id, session.session_id,
            )
            return False

        try:
            datos_frescos = entrevistas_db.recuperar_sesion_entrevista(str(session.user_id))
        except Exception as e:
            logger.warning("Error al re-consultar sesi\u00f3n persistida: %s", e)
            datos_frescos = None

        if datos_frescos is None:
            await interaction.response.send_message(
                "Esta entrevista ya no puede recuperarse.",
                ephemeral=True,
            )
            logger.info(
                "[ENTREVISTA] Recuperaci\u00f3n denegada (sesi\u00f3n persistida inexistente): "
                "user=%s session=%s",
                session.user_id, session.session_id,
            )
            return False

        estado_actual = datos_frescos.get("estado", "")
        if (
            estado_actual not in ESTADOS_RECUPERABLES
            or str(session.staff_id) != datos_frescos.get("staff_id")
        ):
            await interaction.response.send_message(
                "Esta entrevista ya no puede recuperarse.",
                ephemeral=True,
            )
            logger.info(
                "[ENTREVISTA] Recuperaci\u00f3n denegada (estado no recuperable): "
                "user=%s estado=%s session=%s",
                session.user_id, estado_actual, session.session_id,
            )
            return False

        estado_previo = session.estado
        session.client = interaction.client
        session.estado = ESTADO_ACTIVA
        session.processing = False
        session.answered_current = False

        if session.current_index >= len(session.questions):
            embed = discord.Embed(
                title="\U0001f4cb Entrevista finalizada",
                description="Procesando resultado...",
                color=discord.Color.green(),
            )
            session.estado = ESTADO_FINALIZADA
            try:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception:
                logger.exception(
                    "[ENTREVISTA] No se pudo enviar confirmaci\u00f3n de finalizaci\u00f3n al recuperar: "
                    "user=%s session=%s",
                    session.user_id, session.session_id,
                )
            _limpiar_sesion_persistida(session)
            await finalizar_entrevista(session)
            logger.info(
                "[ENTREVISTA] Recuperaci\u00f3n exitosa (sesi\u00f3n ya completa, se finaliz\u00f3): "
                "user=%s staff=%s session=%s",
                session.user_id, session.staff_id, session.session_id,
            )
            return True

        embed = mostrar_pregunta_actual(session)
        embed.add_field(name="Entrevistado", value=usuario.mention, inline=True)

        view = QuestionView(session)
        try:
            await interaction.response.send_message(
                embed=embed,
                view=view,
                ephemeral=True,
            )
        except Exception:
            logger.exception(
                "[ENTREVISTA] No se pudo reconstruir la interfaz al recuperar: "
                "user=%s session=%s",
                session.user_id, session.session_id,
            )
            session.estado = estado_previo
            _persistir_sesion(session)
            return False

        try:
            message = await interaction.original_response()
        except Exception:
            logger.exception(
                "[ENTREVISTA] No se pudo obtener el mensaje recuperado: user=%s session=%s",
                session.user_id, session.session_id,
            )
        else:
            session.message = message
            session.message_id = message.id

        active_interviews[session.user_id] = session
        _persistir_sesion(session)
        logger.info(
            "[ENTREVISTA] Recuperaci\u00f3n exitosa: user=%s staff=%s pregunta=%s/%s session=%s",
            session.user_id, session.staff_id,
            session.current_index + 1, len(session.questions),
            session.session_id,
        )
        return True


class RecuperarSesionView(View):
    def __init__(self, session: InterviewSession, usuario: discord.Member):
        super().__init__(timeout=120)
        self.session = session
        self.usuario = usuario

    @discord.ui.button(label="Continuar entrevista", style=discord.ButtonStyle.primary)
    async def continuar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _recuperar_sesion(interaction, self.usuario, self.session)

    @discord.ui.button(label="Descartar y empezar de nuevo", style=discord.ButtonStyle.danger)
    async def descartar(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with _get_recovery_lock(self.session.user_id):
            if interaction.user.id != self.session.staff_id:
                await interaction.response.send_message(
                    "No ten\u00e9s permisos para descartar esta entrevista.",
                    ephemeral=True,
                )
                logger.info(
                    "[ENTREVISTA] Descartar denegado (no es el entrevistador): "
                    "user=%s autor=%s session=%s",
                    self.session.user_id, interaction.user.id, self.session.session_id,
                )
                return
            self.session.estado = ESTADO_ABANDONADA
            try:
                await _actualizar_y_detener_panel(self.session)
            except Exception:
                logger.exception(
                    "[ENTREVISTA] Error actualizando panel al abandonar: user=%s",
                    self.session.user_id,
                )
            _limpiar_sesion_persistida(self.session)
            active_interviews.pop(self.session.user_id, None)
            await interaction.response.edit_message(
                content=(
                    f"\u274c Entrevista de {self.usuario.mention} descartada.\n"
                    "Us\u00e1 `/preguntas` de nuevo para iniciar una nueva."
                ),
                view=None,
            )
            logger.info(
                "[ENTREVISTA] Entrevista abandonada: user=%s staff=%s session=%s",
                self.session.user_id, self.session.staff_id, self.session.session_id,
            )


class CategoriaSelectView(View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(CategoriaSelect())


class CategoriaSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="GENERAL", value="GENERAL", description="Preguntas de conocimiento general"),
            discord.SelectOption(label="ARMERIA", value="ARMERIA", description="Preguntas sobre armamento y t\u00e1ser"),
            discord.SelectOption(label="CASOS_PRACTICOS", value="CASOS_PRACTICOS", description="Preguntas de casos pr\u00e1cticos"),
        ]
        super().__init__(
            placeholder="Elige una categor\u00eda...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        categoria = self.values[0]
        await interaction.response.send_modal(PreguntaModal(categoria))


class PreguntaSelectView(View):
    def __init__(self, preguntas: list[dict], mode: str):
        super().__init__(timeout=120)
        self.preguntas = preguntas
        self.mode = mode
        self.page = 0
        self.page_size = 25
        self.total_pages = max(1, (len(preguntas) + self.page_size - 1) // self.page_size)
        self._refresh()

    def _get_page_preguntas(self) -> list[dict]:
        start = self.page * self.page_size
        end = min(start + self.page_size, len(self.preguntas))
        return self.preguntas[start:end]

    def _page_info(self) -> str:
        total = len(self.preguntas)
        start = self.page * self.page_size
        end = min(start + self.page_size, total)
        return f"P\u00e1gina {self.page + 1}/{self.total_pages} \u2014 Mostrando preguntas {start + 1}\u2013{end} de {total}"

    def _refresh(self):
        self.clear_items()
        page_preguntas = self._get_page_preguntas()
        self.add_item(PreguntaSelect(page_preguntas, self.mode))

        prev = discord.ui.Button(
            label="\u25c0\ufe0e Anterior", style=discord.ButtonStyle.secondary,
            disabled=self.page == 0, row=2,
        )
        prev.callback = self._prev_page
        self.add_item(prev)

        next = discord.ui.Button(
            label="Siguiente \u25b6\ufe0e", style=discord.ButtonStyle.secondary,
            disabled=self.page >= self.total_pages - 1, row=2,
        )
        next.callback = self._next_page
        self.add_item(next)

    async def _prev_page(self, interaction: discord.Interaction):
        self.page -= 1
        self._refresh()
        await interaction.response.edit_message(
            content=self._mensaje_base, view=self,
        )

    async def _next_page(self, interaction: discord.Interaction):
        self.page += 1
        self._refresh()
        await interaction.response.edit_message(
            content=self._mensaje_base, view=self,
        )

    @property
    def _mensaje_base(self) -> str:
        accion = "editar" if self.mode == "editar" else "eliminar"
        return f"Selecciona la pregunta que deseas {accion}:\n{self._page_info()}"


class PreguntaSelect(discord.ui.Select):
    def __init__(self, preguntas: list[dict], mode: str):
        self._mode = mode
        options = []
        for p in preguntas:
            texto = p["pregunta"]
            if len(texto) > 97:
                texto = texto[:94] + "..."
            options.append(
                discord.SelectOption(
                    label=texto,
                    value=str(p["id"]),
                    description=p["categoria"],
                )
            )
        placeholder = "Selecciona una pregunta para editar..." if mode == "editar" else "Selecciona una pregunta para eliminar..."
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        pregunta_id = int(self.values[0])
        self.disabled = True

        try:
            pregunta = entrevistas_db.obtener_pregunta(pregunta_id)
        except Exception:
            logger.exception("Error obteniendo pregunta %s de la base de datos", pregunta_id)
            await interaction.response.edit_message(
                content="\u274c Error al consultar la base de datos.",
                view=None,
            )
            return

        if not pregunta:
            await interaction.response.edit_message(
                content="\u274c La pregunta no fue encontrada en la base de datos.",
                view=None,
            )
            return

        embed = _build_pregunta_embed(pregunta)
        await interaction.response.edit_message(
            embed=embed,
            view=QuestionDetailView(pregunta),
        )


class ConfirmDeleteView(View):
    def __init__(self, pregunta_id: int, texto_resumen: str):
        super().__init__(timeout=120)
        self.pregunta_id = pregunta_id
        self.texto_resumen = texto_resumen

    @discord.ui.button(label="Confirmar", style=discord.ButtonStyle.danger)
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            eliminado = entrevistas_db.eliminar_pregunta(self.pregunta_id)
            if eliminado:
                await interaction.response.edit_message(
                    content="\u2705 Pregunta eliminada correctamente.",
                    view=None,
                )
                logger.info("Pregunta eliminada: id=%s por %s", self.pregunta_id, interaction.user)
            else:
                await interaction.response.edit_message(
                    content="\u274c No se encontr\u00f3 la pregunta para eliminar.",
                    view=None,
                )
        except Exception as e:
            logger.warning("Error al eliminar pregunta %s: %s", self.pregunta_id, e)
            await interaction.response.edit_message(
                content="\u274c Ocurri\u00f3 un error al eliminar la pregunta.",
                view=None,
            )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="\u274c Operaci\u00f3n cancelada.",
            view=None,
        )


class QuestionDetailView(View):
    def __init__(self, pregunta: dict):
        super().__init__(timeout=120)
        self.pregunta = pregunta

    @discord.ui.button(label="\u270f\ufe0f Editar", style=discord.ButtonStyle.primary)
    async def editar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditarPreguntaModal(
            self.pregunta["id"],
            self.pregunta["pregunta"],
            self.pregunta.get("categoria", ""),
            self.pregunta.get("respuesta_esperada", ""),
        ))

    @discord.ui.button(label="\U0001f5d1\ufe0f Eliminar", style=discord.ButtonStyle.danger)
    async def eliminar(self, interaction: discord.Interaction, button: discord.ui.Button):
        texto = self.pregunta["pregunta"]
        if len(texto) > 100:
            texto = texto[:97] + "..."
        await interaction.response.edit_message(
            content=f"**\u00bfSeguro que quieres eliminar esta pregunta?**\n\n{texto}",
            view=ConfirmDeleteView(self.pregunta["id"], texto),
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Operaci\u00f3n cancelada.",
            view=None,
        )


class ConfigPreguntasGroup(app_commands.Group):
    def __init__(self):
        super().__init__(
            name="config_preguntas",
            description="Administra el banco de preguntas de entrevistas",
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            await interaction.response.send_message("\u274c Este comando solo puede usarse en un servidor.", ephemeral=True)
            return False
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("\u274c No se pudieron verificar tus permisos.", ephemeral=True)
            return False
        if not tiene_permiso_entrevista(interaction.user):
            await interaction.response.send_message(
                "\u274c No ten\u00e9s permisos para administrar preguntas.\n"
                f"Necesit\u00e1s el permiso **Administrador** o el rol <@&{ROL_AUTORIZADO_ID}>.",
                ephemeral=True,
            )
            return False
        return True

    @app_commands.command(name="agregar", description="Agrega una nueva pregunta al banco")
    async def agregar(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Selecciona una categor\u00eda para la nueva pregunta:",
            view=CategoriaSelectView(),
            ephemeral=True,
        )

    @app_commands.command(name="editar", description="Edita una pregunta existente")
    async def editar(self, interaction: discord.Interaction):
        try:
            preguntas = entrevistas_db.listar_preguntas(solo_activas=True)
        except Exception as e:
            logger.warning("Error al listar preguntas: %s", e)
            await interaction.response.send_message("\u274c Error al consultar la base de datos.", ephemeral=True)
            return

        if not preguntas:
            await interaction.response.send_message(
                "\U0001f4ed No hay preguntas registradas. Us\u00e1 `/config_preguntas agregar` para crear una.",
                ephemeral=True,
            )
            return

        view = PreguntaSelectView(preguntas, mode="editar")
        await interaction.response.send_message(
            view._mensaje_base,
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="eliminar", description="Elimina una pregunta del banco")
    async def eliminar(self, interaction: discord.Interaction):
        try:
            preguntas = entrevistas_db.listar_preguntas(solo_activas=True)
        except Exception as e:
            logger.warning("Error al listar preguntas: %s", e)
            await interaction.response.send_message("\u274c Error al consultar la base de datos.", ephemeral=True)
            return

        if not preguntas:
            await interaction.response.send_message(
                "\U0001f4ed No hay preguntas registradas. Us\u00e1 `/config_preguntas agregar` para crear una.",
                ephemeral=True,
            )
            return

        view = PreguntaSelectView(preguntas, mode="eliminar")
        await interaction.response.send_message(
            view._mensaje_base,
            view=view,
            ephemeral=True,
        )


@app_commands.command(name="preguntas", description="Inicia una entrevista de preguntas a un postulante")
@app_commands.describe(usuario="Usuario a entrevistar")
async def preguntas(interaction: discord.Interaction, usuario: discord.Member):
    if not interaction.guild:
        await interaction.response.send_message("\u274c Este comando solo puede usarse en un servidor.", ephemeral=True)
        return

    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("\u274c No se pudieron verificar tus permisos.", ephemeral=True)
        return

    if not tiene_permiso_entrevista(interaction.user):
        await interaction.response.send_message(
            "\u274c No ten\u00e9s permisos para usar este comando.\n"
            f"Necesit\u00e1s el permiso **Administrador** o el rol <@&{ROL_AUTORIZADO_ID}>.",
            ephemeral=True,
        )
        return

    if usuario.id == interaction.user.id:
        await interaction.response.send_message("\u274c No puedes entrevistarte a ti mismo.", ephemeral=True)
        return

    if usuario.bot:
        await interaction.response.send_message("\u274c No puedes entrevistar a un bot.", ephemeral=True)
        return

    if usuario.id in active_interviews:
        await interaction.response.send_message(
            "\u274c Este usuario ya tiene una entrevista en curso. Finalizala antes de iniciar otra.",
            ephemeral=True,
        )
        return

    try:
        sesion_persistida = entrevistas_db.recuperar_sesion_entrevista(str(usuario.id))
    except Exception as e:
        logger.warning("Error al consultar sesi\u00f3n persistida: %s", e)
        sesion_persistida = None

    if sesion_persistida:
        estado_db = sesion_persistida.get("estado", "")
        if estado_db in ESTADOS_RECUPERABLES:
            if str(interaction.user.id) != sesion_persistida.get("staff_id"):
                await interaction.response.send_message(
                    "\u274c Este usuario tiene una entrevista en curso iniciada por otro miembro del staff.\n"
                    "Solo esa persona puede recuperarla.",
                    ephemeral=True,
                )
                return
            sesion_recuperable = _datos_a_session(sesion_persistida, interaction.client)
            logger.info(
                "[ENTREVISTA] Mostrando opciones de recuperaci\u00f3n: "
                "user=%s staff=%s estado=%s session=%s",
                usuario.id, interaction.user.id, estado_db, sesion_recuperable.session_id,
            )
            await interaction.response.send_message(
                content=(
                    f"\u26a0\ufe0f Se encontr\u00f3 una entrevista en curso para {usuario.mention}.\n"
                    "Eleg\u00ed c\u00f3mo quer\u00e9s continuar:"
                ),
                view=RecuperarSesionView(sesion_recuperable, usuario),
                ephemeral=True,
            )
            return
        try:
            entrevistas_db.eliminar_sesion_entrevista(str(usuario.id))
        except Exception as e:
            logger.warning("Error limpiando sesi\u00f3n terminal persistida: %s", e)

    try:
        intentos = entrevistas_db.obtener_intentos(str(usuario.id))
    except Exception as e:
        logger.warning("Error al obtener intentos: %s", e)
        await interaction.response.send_message("\u274c Error al consultar la base de datos.", ephemeral=True)
        return

    if intentos >= entrevistas_db.MAX_INTENTOS:
        await interaction.response.send_message(
            f"\u274c **{usuario.mention}** ya alcanz\u00f3 el m\u00e1ximo de {entrevistas_db.MAX_INTENTOS} intentos.\n"
            "Contact\u00e1 con un administrador para restablecer los intentos.",
            ephemeral=True,
        )
        return

    if intentos > 0:
        try:
            ultimo = entrevistas_db.obtener_ultimo_intento(str(usuario.id))
        except Exception as e:
            logger.warning("Error al obtener \u00faltimo intento: %s", e)
            await interaction.response.send_message("\u274c Error al consultar la base de datos.", ephemeral=True)
            return

        if ultimo:
            ahora = datetime.now(timezone.utc)
            diff = ahora - ultimo
            if diff.total_seconds() < 86400:
                restante = 86400 - int(diff.total_seconds())
                horas = restante // 3600
                minutos = (restante % 3600) // 60
                await interaction.response.send_message(
                    f"\u274c **{usuario.mention}** debe esperar antes de una nueva entrevista.\n"
                    f"Tiempo restante: {horas}h {minutos}m.\n"
                    f"\u00daltimo intento: <t:{int(ultimo.timestamp())}:f>",
                    ephemeral=True,
                )
                return

    try:
        stock = entrevistas_db.contar_preguntas_por_categoria()
    except Exception as e:
        logger.warning("Error al contar preguntas: %s", e)
        await interaction.response.send_message("\u274c Error al consultar la base de datos.", ephemeral=True)
        return

    categorias_faltantes = []
    for cat in sorted(entrevistas_db.CATEGORIAS_VALIDAS):
        if stock.get(cat, 0) < 5:
            categorias_faltantes.append(f"{cat}: {stock.get(cat, 0)}/5")

    if categorias_faltantes:
        embed = discord.Embed(
            title="\u274c No hay suficientes preguntas",
            description=(
                "Faltan preguntas en las siguientes categor\u00edas para iniciar la entrevista "
                f"(m\u00ednimo 5 por categor\u00eda):\n\n"
                + "\n".join(f"\u2022 {c}" for c in categorias_faltantes)
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Us\u00e1 /config_preguntas agregar para a\u00f1adir m\u00e1s preguntas.")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    try:
        generales = entrevistas_db.seleccionar_preguntas_aleatorias("GENERAL", 5)
        armeria = entrevistas_db.seleccionar_preguntas_aleatorias("ARMERIA", 5)
        casos = entrevistas_db.seleccionar_preguntas_aleatorias("CASOS_PRACTICOS", 5)
    except Exception as e:
        logger.warning("Error al seleccionar preguntas: %s", e)
        await interaction.response.send_message("\u274c Error al preparar las preguntas.", ephemeral=True)
        return

    preguntas_totales = generales + armeria + casos

    sesion = InterviewSession(
        user_id=usuario.id,
        staff_id=interaction.user.id,
        channel_id=interaction.channel_id,
        guild_id=interaction.guild_id,
        questions=preguntas_totales,
        intento=intentos + 1,
        client=interaction.client,
    )
    active_interviews[usuario.id] = sesion

    embed = mostrar_pregunta_actual(sesion)
    embed.add_field(name="Entrevistado", value=usuario.mention, inline=True)

    view = QuestionView(sesion)
    await interaction.response.send_message(
        embed=embed,
        view=view,
        ephemeral=True,
    )
    try:
        message = await interaction.original_response()
    except Exception:
        logger.exception("No se pudo obtener el mensaje de la entrevista al iniciarla")
    else:
        sesion.message = message
        sesion.message_id = message.id
        if message.channel is not None:
            sesion.channel_id = message.channel.id

    _persistir_sesion(sesion)
    logger.info(
        "[ENTREVISTA] Sesión creada: user=%s staff=%s intento=%s session=%s",
        usuario.id, interaction.user.id, intentos + 1, sesion.session_id,
    )


@app_commands.command(name="recuperar_entrevista", description="Recupera la entrevista expirada de un postulante")
@app_commands.describe(usuario="Usuario cuya entrevista quer\u00e9s recuperar")
async def recuperar_entrevista(interaction: discord.Interaction, usuario: discord.Member):
    if not interaction.guild:
        await interaction.response.send_message("\u274c Este comando solo puede usarse en un servidor.", ephemeral=True)
        return

    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("\u274c No se pudieron verificar tus permisos.", ephemeral=True)
        return

    if not tiene_permiso_entrevista(interaction.user):
        await interaction.response.send_message(
            "\u274c No ten\u00e9s permisos para usar este comando.\n"
            f"Necesit\u00e1s el permiso **Administrador** o el rol <@&{ROL_AUTORIZADO_ID}>.",
            ephemeral=True,
        )
        return

    if usuario.id in active_interviews:
        sesion_viva = active_interviews[usuario.id]
        if sesion_viva.estado == ESTADO_ACTIVA:
            await interaction.response.send_message(
                "Este usuario ya tiene una entrevista en curso con interfaz activa.",
                ephemeral=True,
            )
            return

    try:
        datos = entrevistas_db.recuperar_sesion_entrevista(str(usuario.id))
    except Exception as e:
        logger.warning("Error al consultar sesi\u00f3n persistida: %s", e)
        await interaction.response.send_message("\u274c Error al consultar la base de datos.", ephemeral=True)
        return

    if not datos or datos.get("estado") not in ESTADOS_RECUPERABLES:
        await interaction.response.send_message(
            "No hay ninguna entrevista recuperable para este usuario.",
            ephemeral=True,
        )
        return

    if str(interaction.user.id) != datos.get("staff_id"):
        await interaction.response.send_message(
            "Esta entrevista fue iniciada por otro miembro del staff. Solo esa persona puede recuperarla.",
            ephemeral=True,
        )
        return

    sesion = _datos_a_session(datos, interaction.client)
    await _recuperar_sesion(interaction, usuario, sesion)


# ---------------------------------------------------------------------------
# Panel de seguimiento en vivo (/entrevista_estado)
# ---------------------------------------------------------------------------

ESTADO_EMOJI = {
    ESTADO_ACTIVA: "\U0001f7e2",      # 🟢
    ESTADO_EXPIRADA: "\U0001f7e1",   # 🟡
    ESTADO_FINALIZADA: "\U0001f534",  # 🔴
    ESTADO_ABANDONADA: "\u26ab",      # ⚫
}

ESTADO_NOMBRE = {
    ESTADO_ACTIVA: "Activa",
    ESTADO_EXPIRADA: "Expirada (recuperable)",
    ESTADO_FINALIZADA: "Finalizada",
    ESTADO_ABANDONADA: "Abandonada",
}

ESTADOS_TERMINALES = (ESTADO_FINALIZADA, ESTADO_ABANDONADA)

PANEL_REFRESH_INTERVAL = 1.5

# panels activos: user_id -> PanelSeguimiento
active_panels: dict[int, "PanelSeguimiento"] = {}


def _humanizar_tiempo(segundos: float) -> str:
    if segundos < 60:
        return f"{int(segundos)}s"
    minutos = int(segundos // 60)
    rest = int(segundos % 60)
    if minutos < 60:
        return f"{minutos}m {rest}s"
    horas = minutos // 60
    minutos = minutos % 60
    return f"{horas}h {minutos}m"


def _timestamp_discord(dt: datetime | None) -> str:
    if dt is None:
        return "Desconocida"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:F>"


def _obtener_snapshot_sesion(
    user_id: int,
    client: discord.Client,
) -> InterviewSession | None:
    """Devuelve el estado actual de una entrevista.

    La fuente de verdad es la sesi\u00f3n en memoria (active_interviews).
    Si no existe (p.ej. tras un reinicio del bot o si expir\u00f3), se reconstruye
    desde la base de datos (sesiones_entrevista).
    """
    sesion = active_interviews.get(user_id)
    if sesion is not None:
        return sesion

    try:
        datos = entrevistas_db.recuperar_sesion_entrevista(str(user_id))
    except Exception:
        logger.exception(
            "[ENTREVISTA] Error consultando sesi\u00f3n persistida para panel: user=%s",
            user_id,
        )
        return None

    if not datos:
        return None

    return _datos_a_session(datos, client)


def _build_panel_embed(
    session: InterviewSession,
    miembro_staff: discord.Member | None,
    miembro_user: discord.Member | None,
) -> discord.Embed:
    estado = session.estado if session.estado in ESTADO_EMOJI else ESTADO_ABANDONADA
    emoji = ESTADO_EMOJI[estado]
    nombre_estado = ESTADO_NOMBRE[estado]

    if estado == ESTADO_ACTIVA:
        color = discord.Color.green()
    elif estado == ESTADO_EXPIRADA:
        color = discord.Color.orange()
    elif estado == ESTADO_FINALIZADA:
        color = discord.Color.red()
    else:
        color = discord.Color.dark_gray()

    embed = discord.Embed(
        title=f"{emoji} Panel de seguimiento - Entrevista",
        description=f"**Estado:** {emoji} {nombre_estado}",
        color=color,
        timestamp=discord.utils.utcnow(),
    )

    staff_str = miembro_staff.mention if miembro_staff else (
        f"<@{session.staff_id}>" if session.staff_id else "Desconocido"
    )
    user_str = miembro_user.mention if miembro_user else (
        f"<@{session.user_id}>" if session.user_id else "Desconocido"
    )
    embed.add_field(name="\U0001f465 Entrevistador", value=staff_str, inline=True)
    embed.add_field(name="\U0001f464 Entrevistado", value=user_str, inline=True)
    embed.add_field(
        name="\U0001f194 Session ID",
        value=f"`{session.session_id}`" if session.session_id else "Desconocido",
        inline=False,
    )

    total = len(session.questions)
    respondidas = len(session.answers)
    embed.add_field(
        name="\U0001f4cb Pregunta actual",
        value=f"**Pregunta {min(session.current_index + 1, total)} de {total}**"
        if 0 <= session.current_index < total
        else f"Pregunta {session.current_index} de {total} (finalizada)",
        inline=True,
    )
    embed.add_field(
        name="\u270f\ufe0f Respuestas realizadas",
        value=f"{respondidas} / {total}",
        inline=True,
    )

    if 0 <= session.current_index < total:
        pregunta = session.questions[session.current_index]
        titulo = pregunta.get("categoria", "")
        if titulo:
            embed.add_field(
                name="Categor\u00eda",
                value=titulo,
                inline=False,
            )
        contenido = (pregunta.get("pregunta") or "").strip()
        if not contenido:
            contenido = "(sin contenido)"
        if len(contenido) > 1024:
            contenido = contenido[:1021] + "..."
        embed.add_field(
            name="Contenido de la pregunta",
            value=contenido,
            inline=False,
        )
        esperada = (pregunta.get("respuesta_esperada") or "").strip()
        if esperada:
            texto = esperada if len(esperada) <= 1024 else esperada[:1021] + "..."
            embed.add_field(
                name="Respuesta esperada",
                value=texto,
                inline=False,
            )

    last_updated = session.last_updated or session.started_at
    ahora = datetime.now(timezone.utc)
    if last_updated is None:
        ultima_actualizacion = "Desconocida"
        tiempo_inactividad = "Desconocido"
    else:
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=timezone.utc)
        delta = (ahora - last_updated).total_seconds()
        if delta < 0:
            delta = 0
        ultima_actualizacion = _timestamp_discord(last_updated)
        tiempo_inactividad = _humanizar_tiempo(delta)

    embed.add_field(
        name="\U0001f552 \u00daltima actualizaci\u00f3n",
        value=ultima_actualizacion,
        inline=True,
    )
    embed.add_field(
        name="\u23f1\ufe0f Tiempo desde \u00faltima actividad",
        value=tiempo_inactividad,
        inline=True,
    )

    if estado == ESTADO_EXPIRADA:
        embed.add_field(
            name="\u26a0\ufe0f Recuperaci\u00f3n",
            value=(
                "La entrevista expir\u00f3.\n"
                "Recuperable mediante `/recuperar_entrevista`."
            ),
            inline=False,
        )
    elif estado == ESTADO_FINALIZADA:
        embed.add_field(
            name="\u2705 Finalizada",
            value="La entrevista finaliz\u00f3. Panel detenido.",
            inline=False,
        )
    elif estado == ESTADO_ABANDONADA:
        embed.add_field(
            name="\u26ab Abandonada",
            value="La entrevista fue abandonada. Panel detenido.",
            inline=False,
        )

    intento_max = entrevistas_db.MAX_INTENTOS
    embed.set_footer(
        text=(
            f"Intento {session.intento}/{intento_max} \u2022 "
            f"Panel de monitoreo (solo lectura) \u2014 no interfiere con el flujo"
        ),
    )
    return embed


class PanelSeguimiento:
    """Mantiene vivo el panel de seguimiento editando el mismo mensaje.

    El panel es de solo monitoreo: nunca invoca botones ni modifica el
    flujo de la entrevista. La fuente de verdad es InterviewSession
    (en memoria) y sesiones_entrevista (DB).
    """

    def __init__(
        self,
        user_id: int,
        message_id: int,
        channel_id: int,
        client: discord.Client,
    ):
        self.user_id = user_id
        self.message_id = message_id
        self.channel_id = channel_id
        self.client = client
        self.task: asyncio.Task | None = None
        self._stop = False

    async def _refrescar(self, session: InterviewSession | None = None):
        channel = self.client.get_channel(self.channel_id)
        if channel is None:
            logger.warning(
                "[ENTREVISTA] Panel: canal %s no encontrado, deteniendo",
                self.channel_id,
            )
            self._stop = True
            active_panels.pop(self.user_id, None)
            return None

        try:
            message = await channel.fetch_message(self.message_id)
        except discord.NotFound:
            logger.warning(
                "[ENTREVISTA] Panel: mensaje %s no encontrado, deteniendo: user=%s",
                self.message_id, self.user_id,
            )
            self._stop = True
            active_panels.pop(self.user_id, None)
            return None
        except (discord.Forbidden, discord.HTTPException):
            logger.exception(
                "[ENTREVISTA] Panel: no se pudo obtener mensaje %s: user=%s",
                self.message_id, self.user_id,
            )
            return None

        if session is None:
            session = _obtener_snapshot_sesion(self.user_id, self.client)
        if session is None:
            # La sesi\u00f3n ya no existe (fue eliminada de la DB).
            embed = discord.Embed(
                title="\u26ab Panel de seguimiento - Entrevista",
                description=(
                    "**Estado:** \u26ab Abandonada\n\n"
                    "No se encontr\u00f3 ninguna sesi\u00f3n activa o persistida."
                ),
                color=discord.Color.dark_gray(),
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text="Panel detenido.")
            try:
                await message.edit(embed=embed)
            except Exception:
                logger.exception(
                    "[ENTREVISTA] Panel: no se pudo editar mensaje final: user=%s",
                    self.user_id,
                )
            self._stop = True
            active_panels.pop(self.user_id, None)
            logger.info(
                "[ENTREVISTA] Panel detenido (sesi\u00f3n inexistente): user=%s",
                self.user_id,
            )
            return message

        guild = None
        if session.guild_id:
            guild = self.client.get_guild(session.guild_id)
        miembro_staff = guild.get_member(session.staff_id) if guild else None
        miembro_user = guild.get_member(session.user_id) if guild else None

        embed = _build_panel_embed(session, miembro_staff, miembro_user)

        try:
            await message.edit(embed=embed)
        except discord.NotFound:
            logger.warning(
                "[ENTREVISTA] Panel: mensaje no encontrado al editar, deteniendo: user=%s",
                self.user_id,
            )
            self._stop = True
            active_panels.pop(self.user_id, None)
            return None
        except (discord.Forbidden, discord.HTTPException):
            logger.exception(
                "[ENTREVISTA] Panel: no se pudo editar el mensaje: user=%s",
                self.user_id,
            )
            return None

        logger.info("[ENTREVISTA] Panel actualizado: user=%s", self.user_id)

        if session.estado in ESTADOS_TERMINALES:
            self._stop = True
            active_panels.pop(self.user_id, None)
            logger.info(
                "[ENTREVISTA] Panel detenido (entrevista finalizada): "
                "user=%s estado=%s session=%s",
                self.user_id, session.estado, session.session_id,
            )
        return message

    async def _loop(self):
        try:
            while not self._stop:
                await self._refrescar()
                if self._stop:
                    break
                await asyncio.sleep(PANEL_REFRESH_INTERVAL)
        except asyncio.CancelledError:
            logger.info(
                "[ENTREVISTA] Panel cancelado expl\u00edcitamente: user=%s",
                self.user_id,
            )
        except Exception:
            logger.exception(
                "[ENTREVISTA] Error inesperado en loop del panel: user=%s",
                self.user_id,
            )
            active_panels.pop(self.user_id, None)

    async def actualizar_y_detener(self, session: InterviewSession):
        """Edita el panel por \u00faltima vez (estado terminal) y lo detiene.

        Garantiza que el mensaje muestre FINALIZADA/ABANDONADA incluso si la
        sesi\u00f3n ya no est\u00e1 en memoria ni en la base de datos.
        """
        self._stop = True
        session.last_updated = datetime.now(timezone.utc)
        try:
            await self._refrescar(session=session)
        except Exception:
            logger.exception(
                "[ENTREVISTA] Panel: error en edici\u00f3n final: user=%s",
                self.user_id,
            )
        finally:
            if self.task is not None and not self.task.done():
                self.task.cancel()
            active_panels.pop(self.user_id, None)

    def iniciar(self):
        if self.task is not None and not self.task.done():
            return
        self.task = asyncio.create_task(self._loop())

    def detener(self):
        self._stop = True
        if self.task is not None and not self.task.done():
            self.task.cancel()
        active_panels.pop(self.user_id, None)


def _detener_panel_existente(user_id: int):
    panel = active_panels.pop(user_id, None)
    if panel is not None:
        panel.detener()


async def _actualizar_y_detener_panel(session: InterviewSession):
    """Actualiza el panel con el estado terminal de la sesi\u00f3n y lo detiene."""
    panel = active_panels.get(session.user_id)
    if panel is None:
        return
    await panel.actualizar_y_detener(session)


@app_commands.command(
    name="entrevista_estado",
    description="Muestra un panel en vivo del estado de una entrevista activa",
)
@app_commands.describe(
    usuario="Usuario entrevistado (se detecta autom\u00e1ticamente si se usa en un ticket)",
)
async def entrevista_estado(
    interaction: discord.Interaction,
    usuario: discord.Member | None = None,
):
    if not interaction.guild:
        await interaction.response.send_message(
            "\u274c Este comando solo puede usarse en un servidor.",
            ephemeral=True,
        )
        return

    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "\u274c No se pudieron verificar tus permisos.",
            ephemeral=True,
        )
        return

    if not tiene_permiso_entrevista(interaction.user):
        await interaction.response.send_message(
            "\u274c No ten\u00e9s permisos para usar este comando.\n"
            f"Necesit\u00e1s el permiso **Administrador** o el rol <@&{ROL_AUTORIZADO_ID}>.",
            ephemeral=True,
        )
        return

    target_id = usuario.id if usuario is not None else None
    if target_id is None:
        # Detecci\u00f3n autom\u00e1tica: buscar sesi\u00f3n activa cuyo ticket sea este canal.
        try:
            session = _obtener_snapshot_sesion_canal(
                interaction.channel_id, interaction.client,
            )
        except Exception:
            logger.exception(
                "[ENTREVISTA] Error al detectar sesi\u00f3n por canal: canal=%s",
                interaction.channel_id,
            )
            session = None
        if session is None:
            await interaction.response.send_message(
                "\u274c No se detect\u00f3 ninguna entrevista en este canal.\n"
                "Indic\u00e1 el usuario con `usuario`.",
                ephemeral=True,
            )
            return
        target_id = session.user_id
    else:
        if usuario and usuario.id == interaction.user.id:
            await interaction.response.send_message(
                "\u274c No puedes monitorear tu propia entrevista.",
                ephemeral=True,
            )
            return

    sesion = _obtener_snapshot_sesion(target_id, interaction.client)
    if sesion is None:
        await interaction.response.send_message(
            "\u274c No se encontr\u00f3 ninguna entrevista para ese usuario.",
            ephemeral=True,
        )
        return

    # El entrevistado nunca puede usar el comando (ya validado por permisos),
    # pero adem\u00e1s bloqueamos expl\u00edcitamente por seguridad.
    if interaction.user.id == sesion.user_id:
        await interaction.response.send_message(
            "\u274c El entrevistado no puede usar este comando.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    miembro_staff = guild.get_member(sesion.staff_id)
    miembro_user = guild.get_member(sesion.user_id)
    embed_inicial = _build_panel_embed(sesion, miembro_staff, miembro_user)

    await interaction.response.send_message(embed=embed_inicial, ephemeral=False)

    try:
        message = await interaction.original_response()
    except Exception:
        logger.exception(
            "[ENTREVISTA] No se pudo obtener el mensaje del panel al crearlo: user=%s",
            target_id,
        )
        return

    _detener_panel_existente(target_id)

    panel = PanelSeguimiento(
        user_id=target_id,
        message_id=message.id,
        channel_id=message.channel.id,
        client=interaction.client,
    )
    active_panels[target_id] = panel
    panel.iniciar()

    logger.info(
        "[ENTREVISTA] Panel de seguimiento creado: user=%s staff=%s session=%s "
        "mensaje=%s canal=%s",
        target_id, interaction.user.id, sesion.session_id,
        message.id, message.channel.id,
    )


def _obtener_snapshot_sesion_canal(
    channel_id: int,
    client: discord.Client,
) -> InterviewSession | None:
    """Detecta una entrevista cuyo ticket coincida con el canal actual.

    Busca primero en memoria y luego en la base de datos.
    """
    for sesion in active_interviews.values():
        if sesion.channel_id == channel_id:
            return sesion

    try:
        datos = entrevistas_db.listar_sesiones_por_canal(str(channel_id))
    except Exception:
        logger.exception(
            "[ENTREVISTA] Error listando sesiones por canal: canal=%s",
            channel_id,
        )
        return None

    if not datos:
        return None

    # Priorizar sesiones a\u00fan recuperables.
    for d in datos:
        if d.get("estado") in ESTADOS_RECUPERABLES:
            return _datos_a_session(d, client)
    return _datos_a_session(datos[0], client)


class ManualIdModal(Modal, title="Configurar canales manualmente"):
    def __init__(self, log_id: int, err_id: int):
        super().__init__()
        self.log_input = TextInput(
            label="ID canal logs",
            placeholder="Ej: 123456789012345678",
            default=str(log_id) if log_id else "",
            required=False,
            max_length=20,
        )
        self.add_item(self.log_input)
        self.err_input = TextInput(
            label="ID canal errores",
            placeholder="Ej: 123456789012345678",
            default=str(err_id) if err_id else "",
            required=False,
            max_length=20,
        )
        self.add_item(self.err_input)

    async def on_submit(self, interaction: discord.Interaction):
        cambios = []
        try:
            for clave, raw, label in [
                ("log_channel_id", self.log_input.value.strip(), "\U0001f4dc Logs"),
                ("errores_channel_id", self.err_input.value.strip(), "\u274c Errores"),
            ]:
                if raw:
                    cid = int(raw)
                    entrevistas_db.actualizar_configuracion(clave, str(cid), str(interaction.user.id))
                    postulacion_config_cache[clave] = cid
                    cambios.append(f"{label}: <#{cid}>")

            if cambios:
                await interaction.response.send_message(
                    "\u2705 Configuraci\u00f3n actualizada:\n" + "\n".join(cambios),
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message("\u2139\ufe0f No se realizaron cambios.", ephemeral=True)
        except Exception as e:
            logger.warning("Error en modal de IDs manuales: %s", e)
            await interaction.response.send_message("\u274c Error al actualizar la configuraci\u00f3n.", ephemeral=True)


class ConfigPostulacionView(View):
    def __init__(self):
        super().__init__(timeout=300)
        self.log_channel_id = postulacion_config_cache.get("log_channel_id", 0)
        self.errores_channel_id = postulacion_config_cache.get("errores_channel_id", 0)

        log_select = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            placeholder="\U0001f4dc Canal de logs...",
        )
        async def _on_log(interaction: discord.Interaction):
            self.log_channel_id = log_select.values[0].id
            await interaction.response.defer()
        log_select.callback = _on_log
        self.add_item(log_select)

        err_select = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            placeholder="\u274c Canal de errores...",
        )
        async def _on_err(interaction: discord.Interaction):
            self.errores_channel_id = err_select.values[0].id
            await interaction.response.defer()
        err_select.callback = _on_err
        self.add_item(err_select)

    @discord.ui.button(label="Guardar configuraci\u00f3n", style=discord.ButtonStyle.success, row=2)
    async def guardar(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("\u274c Este comando solo puede usarse en un servidor.", ephemeral=True)
            return

        pendientes = [
            ("log_channel_id", self.log_channel_id, "\U0001f4dc Canal de logs"),
            ("errores_channel_id", self.errores_channel_id, "\u274c Canal de errores"),
        ]
        errores = []
        exitosos = []

        for clave, cid, label in pendientes:
            if not cid:
                errores.append(f"{label}: ID inv\u00e1lido")
                continue
            canal = guild.get_channel(cid)
            if not canal or not isinstance(canal, discord.TextChannel):
                errores.append(f"{label}: <#{cid}> no es un canal de texto v\u00e1lido")
                continue
            if not canal.permissions_for(guild.me).send_messages:
                errores.append(f"{label}: no tengo permisos para enviar mensajes en <#{cid}>")
                continue
            try:
                entrevistas_db.actualizar_configuracion(clave, str(cid), str(interaction.user.id))
                postulacion_config_cache[clave] = cid
                exitosos.append(f"{label}: <#{cid}>")
            except Exception as e:
                logger.warning("Error guardando %s: %s", clave, e)
                errores.append(f"{label}: error al guardar en DB")

        partes = []
        if exitosos:
            partes.append("\u2705 Configuraci\u00f3n guardada:\n" + "\n".join(exitosos))
        if errores:
            partes.append("\u26a0\ufe0f Errores:\n" + "\n".join(errores))

        await interaction.response.send_message("\n\n".join(partes) if partes else "\u2139\ufe0f Sin cambios.", ephemeral=True)
        logger.info("Config postulaci\u00f3n guardada por %s: exitosos=%s errores=%s", interaction.user, len(exitosos), len(errores))

    @discord.ui.button(label="Ingresar IDs manualmente", style=discord.ButtonStyle.secondary, row=2)
    async def manual_ids(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ManualIdModal(
            log_id=self.log_channel_id,
            err_id=self.errores_channel_id,
        ))


@app_commands.command(name="config_postulacion", description="Configura los canales del sistema de entrevistas")
async def config_postulacion(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("\u274c Este comando solo puede usarse en un servidor.", ephemeral=True)
        return
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("\u274c No se pudieron verificar tus permisos.", ephemeral=True)
        return
    if not tiene_permiso_entrevista(interaction.user):
        await interaction.response.send_message(
            "\u274c No ten\u00e9s permisos para configurar la postulaci\u00f3n.\n"
            f"Necesit\u00e1s el permiso **Administrador** o el rol <@&{ROL_AUTORIZADO_ID}>.",
            ephemeral=True,
        )
        return

    config = postulacion_config_cache
    log_st = f"<#{config['log_channel_id']}>" if config["log_channel_id"] else "\u274c No configurado"
    err_st = f"<#{config['errores_channel_id']}>" if config["errores_channel_id"] else "\u274c No configurado"

    embed = discord.Embed(
        title="\u2699\ufe0f Configuraci\u00f3n de entrevistas",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="\U0001f4dc Canal de logs", value=log_st, inline=False)
    embed.add_field(name="\u274c Canal de errores", value=err_st, inline=False)
    embed.set_footer(text="Seleccion\u00e1 los canales y luego presion\u00e1 Guardar.")

    await interaction.response.send_message(embed=embed, view=ConfigPostulacionView(), ephemeral=True)


async def setup(bot):
    entrevistas_db.init()
    _load_config()

    try:
        eliminadas = entrevistas_db.limpiar_sesiones_antiguas()
        if eliminadas:
            logger.info(
                "[ENTREVISTA] Sesiones viejas limpiadas al iniciar: %s", eliminadas,
            )
    except Exception as e:
        logger.warning("[ENTREVISTA] Error limpiando sesiones viejas: %s", e)

    try:
        pendientes = entrevistas_db.contar_sesiones_recuperables()
        if pendientes:
            logger.info(
                "[ENTREVISTA] Sesiones pendientes de recuperaci\u00f3n en DB al iniciar: %s "
                "(us\u00e9 /recuperar_entrevista para continuarlas)",
                pendientes,
            )
    except Exception as e:
        logger.warning("[ENTREVISTA] Error contando sesiones pendientes: %s", e)

    bot.tree.add_command(ConfigPreguntasGroup())
    bot.tree.add_command(preguntas)
    bot.tree.add_command(recuperar_entrevista)
    bot.tree.add_command(entrevista_estado)
    bot.tree.add_command(config_postulacion)
    logger.info("Comandos /config_preguntas, /config_postulacion, /preguntas, /recuperar_entrevista y /entrevista_estado registrados")
