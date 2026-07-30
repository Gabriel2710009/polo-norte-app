import os
import logging
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
    original_interaction: discord.Interaction | None = None
    intento: int = 1
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    processing: bool = False
    answered_current: bool = False


active_interviews: dict[int, InterviewSession] = {}

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
        logger.error("Error cargando configuraci\u00f3n de postulaci\u00f3n: %s", e)


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


async def finalizar_entrevista(session: InterviewSession):
    bot = session.original_interaction.client
    guild = bot.get_guild(session.guild_id)
    if not guild:
        logger.error("Guild %s no encontrada para finalizar entrevista", session.guild_id)
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
            logger.error("Error enviando plantilla a canal postulaci\u00f3n: %s", e)

    err_channel = bot.get_channel(postulacion_config_cache["errores_channel_id"]) if postulacion_config_cache["errores_channel_id"] else None

    if err_channel and isinstance(err_channel, discord.TextChannel):
        try:
            await err_channel.send(plantilla_err)
        except Exception as e:
            logger.error("Error enviando plantilla a canal errores: %s", e)

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
                logger.error("Error en aprobaci\u00f3n por entrevista: %s", e)
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
                logger.error("Error enviando log de aprobaci\u00f3n: %s", e)
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
                logger.error("Error enviando mensaje de rechazo: %s", e)

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
                logger.error("Error enviando log de rechazo: %s", e)

    try:
        active_interviews.pop(session.user_id, None)
    except Exception as e:
        logger.error("Error limpiando sesi\u00f3n activa: %s", e)

    logger.info(
        "Entrevista finalizada: user=%s staff=%s resultado=%s errores=%s intento=%s",
        session.user_id, session.staff_id,
        resultado["resultado"], resultado["errores"], session.intento,
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
            logger.error("Error al agregar pregunta: %s", e)
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
            logger.error("Error al editar pregunta %s: %s", self.pregunta_id, e)
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

            await interaction.response.defer()

            if self.session.current_index >= len(self.session.questions):
                embed = discord.Embed(
                    title="\U0001f4cb Entrevista finalizada",
                    description="Procesando resultado...",
                    color=discord.Color.green(),
                )
                try:
                    await self.session.original_interaction.edit_original_response(embed=embed, view=None)
                except Exception:
                    logger.exception("Error editando mensaje al finalizar entrevista")
                await finalizar_entrevista(self.session)
            else:
                embed = mostrar_pregunta_actual(self.session)
                view = QuestionView(self.session)
                try:
                    await self.session.original_interaction.edit_original_response(embed=embed, view=view)
                    self.session.answered_current = False
                except Exception:
                    logger.exception("Error editando mensaje para siguiente pregunta")
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
        active_interviews.pop(self.session.user_id, None)
        logger.info(
            "Entrevista expirada por timeout: user=%s staff=%s",
            self.session.user_id, self.session.staff_id,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
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

            if self.session.current_index >= len(self.session.questions):
                embed = discord.Embed(
                    title="\U0001f4cb Entrevista finalizada",
                    description="Procesando resultado...",
                    color=discord.Color.green(),
                )
                try:
                    await interaction.response.edit_message(embed=embed, view=None)
                except Exception:
                    logger.exception("Error editando mensaje al finalizar entrevista (BIEN)")
                await finalizar_entrevista(self.session)
            else:
                embed = mostrar_pregunta_actual(self.session)
                try:
                    await interaction.response.edit_message(embed=embed, view=self)
                    self.session.answered_current = False
                except Exception:
                    logger.exception("Error editando mensaje para siguiente pregunta (BIEN)")
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
        if self.session.processing:
            await interaction.response.send_message(
                "Esta pregunta ya est\u00e1 siendo procesada.",
                ephemeral=True,
            )
            return
        self.session.processing = True
        await interaction.response.send_modal(ReasonModal(self.session, "MAL"))


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
            logger.error("Error al eliminar pregunta %s: %s", self.pregunta_id, e)
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
            logger.error("Error al listar preguntas: %s", e)
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
            logger.error("Error al listar preguntas: %s", e)
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
        sesion_existente = active_interviews[usuario.id]
        tiempo_transcurrido = datetime.now(timezone.utc) - sesion_existente.started_at
        if tiempo_transcurrido.total_seconds() > 600:
            del active_interviews[usuario.id]
            logger.info(
                "Sesi\u00f3n hu\u00e9rfana limpiada: user=%s (%.0f seg expirada)",
                usuario.id, tiempo_transcurrido.total_seconds(),
            )
        else:
            await interaction.response.send_message(
                "\u274c Este usuario ya tiene una entrevista en curso. Finalizala antes de iniciar otra.",
                ephemeral=True,
            )
            return

    try:
        intentos = entrevistas_db.obtener_intentos(str(usuario.id))
    except Exception as e:
        logger.error("Error al obtener intentos: %s", e)
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
            logger.error("Error al obtener \u00faltimo intento: %s", e)
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
        logger.error("Error al contar preguntas: %s", e)
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
        logger.error("Error al seleccionar preguntas: %s", e)
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
    )
    sesion.original_interaction = interaction
    active_interviews[usuario.id] = sesion

    embed = mostrar_pregunta_actual(sesion)
    embed.add_field(name="Entrevistado", value=usuario.mention, inline=True)

    view = QuestionView(sesion)
    await interaction.response.send_message(
        embed=embed,
        view=view,
        ephemeral=True,
    )
    logger.info(
        "Entrevista iniciada: user=%s staff=%s intento=%s",
        usuario.id, interaction.user.id, intentos + 1,
    )


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
            logger.error("Error en modal de IDs manuales: %s", e)
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
                logger.error("Error guardando %s: %s", clave, e)
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
    bot.tree.add_command(ConfigPreguntasGroup())
    bot.tree.add_command(preguntas)
    bot.tree.add_command(config_postulacion)
    logger.info("Comandos /config_preguntas, /config_postulacion y /preguntas registrados")
