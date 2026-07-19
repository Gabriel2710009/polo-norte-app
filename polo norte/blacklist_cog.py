import re
import os
import json
import logging
import asyncio
import discord
from discord import app_commands
from discord.ext import commands

import blacklist_db as db
import log_actions

logger = logging.getLogger("BlacklistCog")

# ── Comportamiento ante fallo de PostgreSQL ──────────────────
# Este módulo depende de PostgreSQL para verificar la blacklist.
# Si la base de datos no responde (caída, timeout, error de red):
#
#   1. db.obtener() lanza excepción → capturada por try/except
#   2. Se loguea el error (logger.error + log_actions si aplica)
#   3. Si BLACKLIST_ALLOW_ROLE_FALLBACK=true (default):
#        - Si el usuario tiene el rol de blacklist físico → se bloquea por rol
#        - Si NO tiene el rol → el ticket se permite (fail open)
#   4. Si BLACKLIST_ALLOW_ROLE_FALLBACK=false:
#        - El ticket se permite siempre (fail open puro)
#   5. db.registrar_intento() también protegido: falla silenciosa con log
#   6. El listener nunca crashea; otros módulos del bot siguen funcionando
#
# Conclusión: con PostgreSQL estable + rol de respaldo habilitado,
# el riesgo de que un blacklistado pase inadvertido es mínimo.
# No se implementa caché local porque el fallback por rol cubre
# el caso crítico y PostgreSQL en producción (Railway, AWS, etc.)
# tiene alta disponibilidad.
# ─────────────────────────────────────────────────────────────

BLACKLIST_POSTULACIONES_ROLE_ID = 0
BLACKLIST_LOG_CHANNEL_ID = 0
POSTULACIONES_CATEGORY_ID = 0
BLACKLIST_BYPASS_ROLE_IDS = set()
BLACKLIST_ALLOW_ROLE_FALLBACK = True
BLACKLIST_STAFF_ALERT_CHANNEL_ID = 0
BLACKLIST_STAFF_ALERT_ROLE_ID = 0

ROL_AUTORIZADO_ID = 1307612928211554386

# Protección contra spam: canales ya notificados en esta sesión
# Persistido a disco para sobrevivir reinicios.
# Cargado al iniciar desde data/tickets_notificados.json y
# guardado cada vez que se notifica un nuevo ticket.
_tickets_notificados: set[int] = set()

logger_scanner = logging.getLogger("BlacklistScanner")

_NOTIFICADOS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
_NOTIFICADOS_FILE = os.path.join(_NOTIFICADOS_DIR, "tickets_notificados.json")


def _persistir_notificados():
    try:
        os.makedirs(_NOTIFICADOS_DIR, exist_ok=True)
        with open(_NOTIFICADOS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(_tickets_notificados), f)
    except Exception as e:
        logger.warning("Error persistiendo notificados: %s", e)


def _cargar_notificados():
    try:
        if not os.path.exists(_NOTIFICADOS_FILE):
            logger_scanner.info("No hay registro persistente de notificados previos")
            return
        with open(_NOTIFICADOS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _tickets_notificados.update(int(x) for x in data)
        logger_scanner.info(
            "Notificados persistentes cargados: %s tickets (evitará duplicados)",
            len(_tickets_notificados),
        )
    except Exception as e:
        logger.warning("Error cargando notificados persistentes: %s", e)

# ── Permisos ──────────────────────────────

def _es_staff(member: discord.Member) -> bool:
    """True si el miembro tiene rol de staff, administrador, o bypass."""
    if member.guild_permissions.administrator:
        return True
    if member.guild_permissions.manage_roles:
        return True
    if any(role.id == ROL_AUTORIZADO_ID for role in member.roles):
        return True
    if any(role.id in BLACKLIST_BYPASS_ROLE_IDS for role in member.roles):
        return True
    return False


def _tiene_permiso(member: discord.Member) -> bool:
    if _es_staff(member):
        return True
    return False


def _detectar_inconsistencia(en_db: bool, tiene_rol: bool) -> bool:
    return (en_db and not tiene_rol) or (not en_db and tiene_rol)


# ── Extracción de Datos IC ──────────────

_PATRONES_NOMBRE_IC = [
    re.compile(r"(?:→\s*)?nombre\s*(?:ic)?\s*:?\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:→\s*)?nombre\s*(?:ic)?\s*:?\s*\n\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:→\s*)?nombre\s*(?:ic)?\s*:?\s*\n\s*\n\s*(.+)", re.IGNORECASE),
]

_PATRONES_NUMERO_IC = [
    re.compile(r"(?:→\s*)?(?:n[úu]mero|num|nro|tel[eé]fono|cel|celular)\s*(?:ic)?\s*:?\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:→\s*)?(?:n[úu]mero|num|nro|tel[eé]fono|cel|celular)\s*(?:ic)?\s*:?\s*\n\s*(.+)", re.IGNORECASE),
]

_PATRONES_IBAN_IC = [
    re.compile(r"(?:→\s*)?(?:iban|cuenta|bank|banco|ibam|bban)\s*(?:ic)?\s*:?\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:→\s*)?(?:iban|cuenta|bank|banco|ibam|bban)\s*(?:ic)?\s*:?\s*\n\s*(.+)", re.IGNORECASE),
]

_PATRONES_STEAM = [
    re.compile(r"(?:→\s*)?(?:steam\s*(?:url|nombre|name|id)?|url\s*steam)\s*:?\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:→\s*)?(?:steam\s*(?:url|nombre|name|id)?|url\s*steam)\s*:?\s*\n\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:https?://)?steamcommunity\.com/\S+", re.IGNORECASE),
]


def _extraer_campo_ic(mensajes: list[discord.Message], patrones: list[re.Pattern]) -> str | None:
    for msg in mensajes:
        for patron in patrones:
            m = patron.search(msg.content)
            if m:
                val = m.group(0) if "steamcommunity" in patron.pattern else m.group(1).strip()
                if val:
                    return val.strip()

    for msg in mensajes:
        for embed in msg.embeds:
            text = f"{embed.title or ''}\n{embed.description or ''}"
            for patron in patrones:
                m = patron.search(text)
                if m:
                    val = m.group(0) if "steamcommunity" in patron.pattern else m.group(1).strip()
                    if val:
                        return val.strip()
            for field in embed.fields:
                combined = field.name + "\n" + (field.value or "")
                for patron in patrones:
                    m = patron.search(combined)
                    if m:
                        val = m.group(0) if "steamcommunity" in patron.pattern else m.group(1).strip()
                        if val:
                            return val.strip()

    return None


def _extraer_nombre_ic(mensajes: list[discord.Message]) -> str | None:
    return _extraer_campo_ic(mensajes, _PATRONES_NOMBRE_IC)


def _extraer_datos_ic(mensajes: list[discord.Message]) -> dict:
    return {
        "nombre_ic": _extraer_campo_ic(mensajes, _PATRONES_NOMBRE_IC),
        "numero_ic": _extraer_campo_ic(mensajes, _PATRONES_NUMERO_IC),
        "iban_ic": _extraer_campo_ic(mensajes, _PATRONES_IBAN_IC),
        "steam_url": _extraer_campo_ic(mensajes, _PATRONES_STEAM),
    }


async def _obtener_mensajes_ticket(channel: discord.TextChannel, limite: int = 50) -> list[discord.Message]:
    mensajes = []
    try:
        async for msg in channel.history(limit=limite, oldest_first=True):
            mensajes.append(msg)
    except Exception as e:
        logger.warning("Error obteniendo mensajes del ticket %s: %s", channel.id, e)
    return mensajes


# ── Notificación ──────────────────────────

async def _notificar_blacklist(channel: discord.TextChannel, registro: dict, usuario: discord.Member):
    embed = discord.Embed(
        title="\U0001f6ab Postulación bloqueada",
        description=(
            "No puedes realizar una postulación en este momento.\n\n"
            f"**Motivo:**\n{registro['motivo']}\n\n"
            "Si consideras que se da un error, contacta con un miembro "
            "del equipo de entrevistadores."
        ),
        color=discord.Color.red(),
    )
    embed.set_footer(text="Este ticket deberá ser revisado y cerrado por el equipo correspondiente.")
    try:
        await channel.send(embed=embed)
    except Exception as e:
        logger.error("Error enviando notificación de blacklist a %s: %s", channel.id, e)
        await log_actions.log_error(
            "\U0001f6ab Error notificación blacklist",
            f"No se pudo enviar embed a <#{channel.id}>: `{e}`",
        )


async def _enviar_embed_log(bot: commands.Bot, embed: discord.Embed):
    if BLACKLIST_LOG_CHANNEL_ID == 0:
        return
    canal = bot.get_channel(BLACKLIST_LOG_CHANNEL_ID)
    if not canal:
        logger.warning("BLACKLIST_LOG_CHANNEL_ID %s no encontrado", BLACKLIST_LOG_CHANNEL_ID)
        await log_actions.log_error(
            "\U0001f6ab Error canal blacklist",
            f"El canal <#{BLACKLIST_LOG_CHANNEL_ID}> no existe o el bot no tiene acceso.",
        )
        return
    try:
        await canal.send(embed=embed)
    except Exception as e:
        logger.error("Error enviando embed a canal blacklist %s: %s", BLACKLIST_LOG_CHANNEL_ID, e)
        await log_actions.log_error(
            "\U0001f6ab Error enviando embed blacklist",
            f"No se pudo enviar embed a <#{BLACKLIST_LOG_CHANNEL_ID}>: `{e}`",
        )


# ── IC Modal / View ───────────────────────

class ICModal(discord.ui.Modal, title="Información IC del usuario"):
    nombre_ic = discord.ui.TextInput(
        label="Nombre IC",
        placeholder="Fatido Rodriguez",
        max_length=100,
        required=True,
    )
    numero_ic = discord.ui.TextInput(
        label="Número IC",
        placeholder="4809639162",
        max_length=50,
        required=False,
    )
    iban_ic = discord.ui.TextInput(
        label="IBAN IC (cuenta bancaria)",
        placeholder="NA20 1821 8817 7121 6519",
        max_length=50,
        required=False,
    )
    steam_url = discord.ui.TextInput(
        label="Steam URL / Nombre",
        placeholder="https://steamcommunity.com/profiles/76561199877636058/",
        max_length=200,
        required=False,
    )

    def __init__(self, ctx: dict):
        super().__init__()
        self.ctx = ctx

    async def on_submit(self, interaction: discord.Interaction):
        nombre_ic = self.nombre_ic.value.strip()
        if not nombre_ic:
            await interaction.response.send_message("❌ El nombre IC es obligatorio.", ephemeral=True)
            return
        numero_ic = self.numero_ic.value.strip() or None
        iban_ic = self.iban_ic.value.strip() or None
        steam_url = self.steam_url.value.strip() or None

        await interaction.response.defer(ephemeral=True)
        await _ejecutar_blacklist(
            interaction=interaction,
            uid=self.ctx["uid"],
            usuario_obj=self.ctx["usuario_obj"],
            motivo=self.ctx["motivo"],
            nombre_ic=nombre_ic,
            numero_ic=numero_ic,
            iban_ic=iban_ic,
            steam_url=steam_url,
        )


class ICView(discord.ui.View):
    def __init__(self, ctx: dict):
        super().__init__(timeout=300)
        self.ctx = ctx

    @discord.ui.button(label="📝 Completar IC", style=discord.ButtonStyle.primary)
    async def completar_ic(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.ctx["staff_id"]:
            await interaction.response.send_message("❌ Solo quien ejecutó el comando puede completar el IC.", ephemeral=True)
            return
        await interaction.response.send_modal(ICModal(self.ctx))

    @discord.ui.button(label="❌ Desconozco", style=discord.ButtonStyle.secondary)
    async def desconozco(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.ctx["staff_id"]:
            await interaction.response.send_message("❌ Solo quien ejecutó el comando puede hacer esto.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _ejecutar_blacklist(
            interaction=interaction,
            uid=self.ctx["uid"],
            usuario_obj=self.ctx["usuario_obj"],
            motivo=self.ctx["motivo"],
            nombre_ic="Desconocido",
        )


# ── Blacklist execution ──────────────────

async def _ejecutar_blacklist(
    interaction: discord.Interaction,
    uid: str,
    usuario_obj,
    motivo: str,
    nombre_ic: str,
    ticket_origen_id: str = None,
    numero_ic: str = None,
    iban_ic: str = None,
    steam_url: str = None,
):
    """Crea la blacklist en DB, asigna rol y envía embeds."""
    bot = interaction.client
    guild = interaction.guild

    ticket_origen_encontrado = ticket_origen_id
    if not ticket_origen_encontrado:
        categoria = guild.get_channel(POSTULACIONES_CATEGORY_ID)
        if categoria:
            for channel in categoria.channels:
                if not isinstance(channel, discord.TextChannel):
                    continue
                try:
                    permisos = channel.permissions_for(usuario_obj)
                except Exception:
                    continue
                if not (permisos.read_messages and permisos.send_messages):
                    continue
                mensajes = await _obtener_mensajes_ticket(channel, limite=30)
                extraido = _extraer_nombre_ic(mensajes)
                if extraido:
                    ticket_origen_encontrado = str(channel.id)
                    break

    creado = db.agregar(
        discord_id=uid,
        nombre_ic=nombre_ic,
        motivo=motivo,
        staff_id=str(interaction.user.id),
        ticket_origen_id=ticket_origen_encontrado,
        numero_ic=numero_ic,
        iban_ic=iban_ic,
        steam_url=steam_url,
    )

    if not creado:
        await interaction.followup.send(
            "\u274c Error inesperado al crear la blacklist (posible duplicado).", ephemeral=True,
        )
        return

    es_member = isinstance(usuario_obj, discord.Member)
    rol_ok = True
    if es_member and BLACKLIST_POSTULACIONES_ROLE_ID:
        rol = guild.get_role(BLACKLIST_POSTULACIONES_ROLE_ID)
        if rol:
            try:
                await usuario_obj.add_roles(rol, reason="Blacklist de postulaciones")
            except Exception as e:
                rol_ok = False
                logger.error("No se pudo asignar rol de blacklist a %s: %s", uid, e)
                await log_actions.log_error(
                    "\U0001f6ab Error asignando rol blacklist",
                    f"Usuario: <@{uid}>\nRol: <@&{BLACKLIST_POSTULACIONES_ROLE_ID}>\nError: `{e}`",
                )

    embed = discord.Embed(
        title="\U0001f6ab Blacklist de Postulaciones",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Nombre IC", value=nombre_ic, inline=True)
    if numero_ic:
        embed.add_field(name="Número IC", value=numero_ic, inline=True)
    if iban_ic:
        embed.add_field(name="IBAN IC", value=iban_ic, inline=True)
    if steam_url:
        embed.add_field(name="Steam", value=steam_url, inline=False)
    embed.add_field(name="Discord", value=f"<@{uid}>\n`{uid}`", inline=False)
    embed.add_field(name="Motivo", value=motivo, inline=False)
    embed.add_field(name="Aplicada por", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=True)
    embed.add_field(name="Fecha", value=discord.utils.utcnow().strftime("%d/%m/%Y %H:%M UTC"), inline=True)
    if ticket_origen_encontrado:
        embed.add_field(name="Ticket origen", value=f"<#{ticket_origen_encontrado}>", inline=True)
    if not rol_ok:
        embed.add_field(name="\u26a0\ufe0f Rol", value="No se pudo asignar (revisar jerarquía).", inline=False)
    elif not es_member:
        embed.add_field(name="\u2139\ufe0f Rol", value="Usuario no está en el servidor. Solo se registró en DB.", inline=False)
    embed.set_footer(text=f"ID: {uid}")

    await _enviar_embed_log(bot, embed)

    log_actions.log_info(
        "\U0001f6ab Blacklist aplicada",
        f"**Usuario:** <@{uid}> (`{uid}`)\n"
        f"**Nombre IC:** {nombre_ic}\n"
        f"**Motivo:** {motivo}\n"
        f"**Staff:** {interaction.user} (`{interaction.user.id}`)",
    )

    if not es_member:
        await interaction.followup.send(
            "\u2705 Blacklist aplicada en DB. El usuario no está en el servidor, no se asignó rol.",
            ephemeral=True,
        )
    elif rol_ok:
        await interaction.followup.send("\u2705 Blacklist aplicada correctamente.", ephemeral=True)
    else:
        await interaction.followup.send(
            "\u26a0\ufe0f Blacklist aplicada en DB, pero **no se pudo asignar el rol**."
            " Revisá la jerarquía del bot.",
            ephemeral=True,
        )


# ── Eliminar mensajes de blacklist ────────

async def _borrar_mensajes_blacklist(bot, guild: discord.Guild, uid: str, ticket_origen_id: str = None):
    """Busca y elimina mensajes de notificación de blacklist en tickets y logs."""
    eliminados = 0

    # 1. Ticket origen
    if ticket_origen_id:
        channel = bot.get_channel(int(ticket_origen_id))
        if channel and isinstance(channel, discord.TextChannel):
            try:
                async for msg in channel.history(limit=100):
                    if msg.author.id == bot.user.id and msg.embeds:
                        for embed in msg.embeds:
                            titulo = (embed.title or "") + (embed.description or "")
                            if "\U0001f6ab" in titulo and "bloqueada" in titulo.lower():
                                await msg.delete()
                                eliminados += 1
                                break
            except Exception as e:
                logger.warning("Error borrando mensajes en ticket %s: %s", channel.id, e)

    # 2. Log channel
    log_channel = bot.get_channel(BLACKLIST_LOG_CHANNEL_ID)
    if log_channel and isinstance(log_channel, discord.TextChannel):
        try:
            async for msg in log_channel.history(limit=200):
                if msg.author.id == bot.user.id and msg.embeds:
                    for embed in msg.embeds:
                        if embed.footer and embed.footer.text and uid in embed.footer.text:
                            await msg.delete()
                            eliminados += 1
                            break
        except Exception as e:
            logger.warning("Error borrando mensajes en log %s: %s", log_channel.id, e)

    # 3. Canales de la categoría de postulaciones
    categoria = guild.get_channel(POSTULACIONES_CATEGORY_ID)
    if categoria:
        for channel in categoria.channels:
            if not isinstance(channel, discord.TextChannel):
                continue
            if ticket_origen_id and str(channel.id) == ticket_origen_id:
                continue
            try:
                async for msg in channel.history(limit=50):
                    if msg.author.id == bot.user.id and msg.embeds:
                        for embed in msg.embeds:
                            titulo = (embed.title or "") + (embed.description or "")
                            if "\U0001f6ab" in titulo and "bloqueada" in titulo.lower():
                                await msg.delete()
                                eliminados += 1
                                break
            except Exception:
                continue

    if eliminados:
        logger.info("Mensajes de blacklist eliminados: %s para UID %s", eliminados, uid)


# ── Resolución de usuario ─────────────────

_ID_PATTERN = re.compile(r"^(\d{17,20})$")
_MENTION_PATTERN = re.compile(r"^<@!?(\d{17,20})>$")


async def _resolver_usuario(interaction: discord.Interaction, texto: str):
    """
    Convierte una mención o ID de Discord en (discord_id_str, usuario_obj, error_msg).

    - texto: '@usuario', '<@123>', '<@!123>' o '123456789'
    - Retorna (id_str, Member|User|None, error_str|None)
    """
    texto = texto.strip()

    m = _MENTION_PATTERN.match(texto)
    if m:
        discord_id = m.group(1)
    else:
        m = _ID_PATTERN.match(texto)
        if not m:
            return None, None, (
                "Formato inválido. Usá una mención (@Usuario) o un ID numérico de Discord.\n"
                "Ejemplo: `/blacklist 1389546682076631141 motivo`"
            )
        discord_id = m.group(1)

    usuario = None
    guild = interaction.guild

    # Intentar resolver como Member (dentro del servidor)
    if guild:
        usuario = guild.get_member(int(discord_id))
        if not usuario:
            try:
                usuario = await guild.fetch_member(int(discord_id))
            except discord.NotFound:
                pass
            except (discord.HTTPException, discord.Forbidden):
                pass

    # Resolver como User global (fuera del servidor)
    if not usuario:
        try:
            usuario = await interaction.client.fetch_user(int(discord_id))
        except discord.NotFound:
            return discord_id, None, (
                "No se encontró un usuario de Discord con ese ID.\n"
                "Verificá que el ID sea correcto e intentá de nuevo."
            )
        except (discord.HTTPException, discord.Forbidden) as e:
            return discord_id, None, f"Error al verificar el ID: {e}"

    return discord_id, usuario, None


# ── Comandos ──────────────────────────────

ENTRIES_PER_PAGE = 10


def _setup_blacklist_commands(bot: commands.Bot):

    # ── /blacklist ──────────────────────────

    @bot.tree.command(name="blacklist", description="Agrega un usuario a la blacklist de postulaciones")
    @app_commands.describe(
        usuario="Mención (@Usuario) o Discord ID del usuario a blacklistear",
        motivo="Motivo de la blacklist",
    )
    async def blacklist(interaction: discord.Interaction, usuario: str, motivo: str):
        if not interaction.guild:
            await interaction.response.send_message("\u274c Solo puede usarse en un servidor.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or not _tiene_permiso(interaction.user):
            await interaction.response.send_message("\u274c No tenés permisos.", ephemeral=True)
            return

        discord_id, usuario_obj, error = await _resolver_usuario(interaction, usuario)
        if error:
            await interaction.response.send_message(f"\u274c {error}", ephemeral=True)
            return

        uid = discord_id

        existente = db.obtener(uid)
        if existente:
            embed = discord.Embed(
                title="\u26a0\ufe0f Usuario ya está en blacklist",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Nombre IC", value=existente["nombre_ic"], inline=True)
            embed.add_field(name="Discord ID", value=f"`{uid}`", inline=True)
            embed.add_field(name="Motivo", value=existente["motivo"], inline=False)
            embed.add_field(name="Staff", value=f"<@{existente['staff_id']}>", inline=True)
            embed.add_field(name="Fecha", value=existente["fecha"], inline=True)
            if existente.get("ticket_origen_id"):
                embed.add_field(name="Ticket origen", value=f"<#{existente['ticket_origen_id']}>", inline=True)
            if existente.get("numero_ic"):
                embed.add_field(name="Número IC", value=existente["numero_ic"], inline=True)
            if existente.get("iban_ic"):
                embed.add_field(name="IBAN IC", value=existente["iban_ic"], inline=True)
            if existente.get("steam_url"):
                embed.add_field(name="Steam", value=existente["steam_url"], inline=False)
            embed.set_footer(text="Usá /unblacklist para remover o /blacklist-info para detalles")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # ── Intentar extraer IC del canal actual ──
        nombre_ic = None
        ticket_origen_id = None
        datos_ic = {}

        canal_actual = interaction.channel
        if isinstance(canal_actual, discord.TextChannel):
            try:
                pinned = await canal_actual.pins()
                datos_ic = _extraer_datos_ic(pinned)
                nombre_ic = datos_ic.get("nombre_ic")
            except Exception as e:
                logger.warning("Error revisando pinned en canal actual: %s", e)

            if not nombre_ic:
                mensajes = await _obtener_mensajes_ticket(canal_actual, limite=30)
                datos_ic = _extraer_datos_ic(mensajes)
                nombre_ic = datos_ic.get("nombre_ic")

            if nombre_ic:
                ticket_origen_id = str(canal_actual.id)

        if nombre_ic:
            await interaction.response.defer(ephemeral=True)
            await _ejecutar_blacklist(
                interaction=interaction,
                uid=uid,
                usuario_obj=usuario_obj,
                motivo=motivo,
                nombre_ic=nombre_ic,
                ticket_origen_id=ticket_origen_id,
                numero_ic=datos_ic.get("numero_ic"),
                iban_ic=datos_ic.get("iban_ic"),
                steam_url=datos_ic.get("steam_url"),
            )
            return

        # ── No se encontró IC ── preguntar al staff ──
        ctx = {
            "uid": uid,
            "usuario_obj": usuario_obj,
            "motivo": motivo,
            "staff_id": interaction.user.id,
        }
        view = ICView(ctx)
        await interaction.response.send_message(
            "\u2139\ufe0f No se encontró información IC automáticamente.\n\n"
            "Podés **completar los datos IC** manualmente o indicar que **desconocés** el nombre IC.\n\n"
            "Formato esperado:\n"
            "• **Nombre IC** (obligatorio)\n"
            "• Número IC\n"
            "• IBAN IC (cuenta bancaria)\n"
            "• Steam URL / Nombre",
            view=view,
            ephemeral=True,
        )

    # ── /unblacklist ────────────────────────

    @bot.tree.command(name="unblacklist", description="Quita un usuario de la blacklist de postulaciones")
    @app_commands.describe(usuario="Mención (@Usuario) o Discord ID del usuario a desblacklistear")
    async def unblacklist(interaction: discord.Interaction, usuario: str):
        if not interaction.guild:
            await interaction.response.send_message("\u274c Solo puede usarse en un servidor.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or not _tiene_permiso(interaction.user):
            await interaction.response.send_message("\u274c No tenés permisos.", ephemeral=True)
            return

        discord_id, usuario_obj, error = await _resolver_usuario(interaction, usuario)
        if error:
            await interaction.response.send_message(f"\u274c {error}", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        uid = discord_id

        registro_previo = db.obtener(uid)
        eliminado = db.eliminar(uid)

        tenia_rol = False
        rol_ok = True
        es_member = isinstance(usuario_obj, discord.Member)
        if es_member and BLACKLIST_POSTULACIONES_ROLE_ID:
            rol = interaction.guild.get_role(BLACKLIST_POSTULACIONES_ROLE_ID)
            if rol and rol in usuario_obj.roles:
                tenia_rol = True
                try:
                    await usuario_obj.remove_roles(rol, reason="Unblacklist de postulaciones")
                except Exception as e:
                    rol_ok = False
                    logger.error("No se pudo remover rol de blacklist a %s: %s", uid, e)
                    await log_actions.log_error(
                        "\u2705 Error removiendo rol blacklist",
                        f"Usuario: <@{uid}>\nError: `{e}`",
                    )

        if not eliminado and not tenia_rol:
            await interaction.followup.send(
                f"\u26a0\ufe0f <@{uid}> no estaba en blacklist ni tenía el rol.", ephemeral=True,
            )
            return

        nombre_ic_log = (registro_previo or {}).get("nombre_ic", "Desconocido")
        motivo_original = (registro_previo or {}).get("motivo", "Sin registro")

        embed_log = discord.Embed(
            title="\u2705 Blacklist retirada",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed_log.add_field(name="Nombre IC", value=nombre_ic_log, inline=True)
        embed_log.add_field(name="Discord", value=f"<@{uid}>\n`{uid}`", inline=False)
        embed_log.add_field(name="Retirada por", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=True)
        embed_log.add_field(name="Fecha", value=discord.utils.utcnow().strftime("%d/%m/%Y %H:%M UTC"), inline=True)
        embed_log.add_field(name="Motivo original", value=motivo_original, inline=False)
        if not eliminado:
            embed_log.add_field(name="\u26a0\ufe0f Nota", value="Solo se removió el rol (no estaba en DB).", inline=False)
        if not rol_ok:
            embed_log.add_field(name="\u26a0\ufe0f Rol", value="No se pudo remover (revisar jerarquía).", inline=False)
        embed_log.set_footer(text=f"ID: {uid}")

        await _enviar_embed_log(bot, embed_log)

        log_actions.log_info(
            "\u2705 Blacklist removida",
            f"**Usuario:** <@{uid}> (`{uid}`)\n"
            f"**Nombre IC:** {nombre_ic_log}\n"
            f"**Motivo original:** {motivo_original}\n"
            f"**Staff:** {interaction.user} (`{interaction.user.id}`)",
        )

        # ── Borrar mensajes de blacklist ──
        ticket_origen = (registro_previo or {}).get("ticket_origen_id")
        await _borrar_mensajes_blacklist(bot, interaction.guild, uid, ticket_origen)

        await interaction.followup.send(f"\u2705 <@{uid}> procesado.", ephemeral=True)

    # ── /blacklist-info ─────────────────────

    @bot.tree.command(name="blacklist-info", description="Muestra información de blacklist de un usuario")
    @app_commands.describe(usuario="Mención (@Usuario) o Discord ID del usuario a consultar")
    async def blacklist_info(interaction: discord.Interaction, usuario: str):
        if not interaction.guild:
            await interaction.response.send_message("\u274c Solo puede usarse en un servidor.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or not _tiene_permiso(interaction.user):
            await interaction.response.send_message("\u274c No tenés permisos.", ephemeral=True)
            return

        discord_id, usuario_obj, error = await _resolver_usuario(interaction, usuario)
        if error:
            await interaction.response.send_message(f"\u274c {error}", ephemeral=True)
            return

        uid = discord_id
        registro = db.obtener(uid)

        tiene_rol = False
        if isinstance(usuario_obj, discord.Member) and BLACKLIST_POSTULACIONES_ROLE_ID:
            rol = interaction.guild.get_role(BLACKLIST_POSTULACIONES_ROLE_ID)
            if rol and rol in usuario_obj.roles:
                tiene_rol = True

        embed = discord.Embed(
            title="\U0001f6ab Información de Blacklist",
            color=discord.Color.red() if registro else discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )

        if registro:
            embed.add_field(name="Nombre IC", value=registro["nombre_ic"], inline=True)
            if registro.get("numero_ic"):
                embed.add_field(name="Número IC", value=registro["numero_ic"], inline=True)
            if registro.get("iban_ic"):
                embed.add_field(name="IBAN IC", value=registro["iban_ic"], inline=True)
            if registro.get("steam_url"):
                embed.add_field(name="Steam", value=registro["steam_url"], inline=False)
            embed.add_field(name="Discord ID", value=f"`{registro['discord_id']}`", inline=True)
            embed.add_field(name="Motivo", value=registro["motivo"], inline=False)
            embed.add_field(name="Staff", value=f"<@{registro['staff_id']}>", inline=True)
            embed.add_field(name="Fecha", value=registro["fecha"], inline=True)
            if registro.get("ticket_origen_id"):
                embed.add_field(name="Ticket origen", value=f"<#{registro['ticket_origen_id']}>", inline=True)
            if registro.get("expira_en"):
                embed.add_field(name="Expira", value=registro["expira_en"], inline=True)
            estado = "\U0001f534 En blacklist"
            if not tiene_rol:
                estado += " \u26a0\ufe0f (sin rol - inconsistencia)"
            embed.add_field(name="Estado", value=estado, inline=False)
        else:
            embed.description = f"<@{uid}> **no** está en la blacklist de postulaciones."
            if tiene_rol:
                embed.description += "\n\n\u26a0\ufe0f Sin embargo, tiene el rol de blacklist asignado (inconsistencia)."

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /blacklist-list ─────────────────────

    @bot.tree.command(name="blacklist-list", description="Lista blacklist activas con paginación")
    @app_commands.describe(pagina="Número de página (default: 1)")
    async def blacklist_list(interaction: discord.Interaction, pagina: int = 1):
        if not interaction.guild:
            await interaction.response.send_message("\u274c Solo puede usarse en un servidor.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not _tiene_permiso(interaction.user):
            await interaction.response.send_message("\u274c No tenés permisos.", ephemeral=True)
            return

        pagina = max(pagina, 1)
        registros, total = db.listar(pagina, ENTRIES_PER_PAGE)
        total_paginas = max(1, (total + ENTRIES_PER_PAGE - 1) // ENTRIES_PER_PAGE)

        if not registros:
            await interaction.response.send_message("No hay blacklist activas.", ephemeral=True)
            return

        embed = discord.Embed(
            title="\U0001f6ab Blacklist de Postulaciones",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        for r in registros:
            valor = (
                f"**Motivo:** {r['motivo']}\n"
                f"**Staff:** <@{r['staff_id']}>\n"
                f"**Fecha:** {r['fecha']}"
            )
            embed.add_field(
                name=f"{r['nombre_ic']} (`{r['discord_id']}`)",
                value=valor,
                inline=False,
            )

        embed.set_footer(text=f"Página {pagina}/{total_paginas} — {total} registro(s)")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /blacklist-search ───────────────────

    class SearchModal(discord.ui.Modal, title="\U0001f50d Buscar en Blacklist"):
        discord_id_input = discord.ui.TextInput(
            label="Discord ID",
            placeholder="1389546682076631141",
            required=False,
            max_length=20,
        )
        nombre_ic_input = discord.ui.TextInput(
            label="Nombre IC",
            placeholder="Fabian Rodriguez",
            required=False,
            max_length=100,
        )

        async def on_submit(self, interaction: discord.Interaction):
            if not interaction.guild:
                await interaction.response.send_message("\u274c Solo puede usarse en un servidor.", ephemeral=True)
                return
            if not isinstance(interaction.user, discord.Member) or not _tiene_permiso(interaction.user):
                await interaction.response.send_message("\u274c No tenés permisos.", ephemeral=True)
                return

            discord_id = self.discord_id_input.value.strip() if self.discord_id_input.value else ""
            nombre_ic = self.nombre_ic_input.value.strip() if self.nombre_ic_input.value else ""

            if not discord_id and not nombre_ic:
                await interaction.response.send_message(
                    "\u26a0\ufe0f Debes completar al menos uno de los campos.", ephemeral=True,
                )
                return

            resultados = db.buscar_por_criterios(
                discord_id=discord_id if discord_id else None,
                nombre_ic=nombre_ic if nombre_ic else None,
            )

            if not resultados:
                await interaction.response.send_message(
                    "\U0001f6ab No se encontraron usuarios en la blacklist.", ephemeral=True,
                )
                return

            if len(resultados) == 1:
                r = resultados[0]
                embed = discord.Embed(
                    title="\U0001f6ab Resultado de búsqueda",
                    color=discord.Color.red(),
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(name="Nombre IC", value=r["nombre_ic"], inline=True)
                if r.get("numero_ic"):
                    embed.add_field(name="Número IC", value=r["numero_ic"], inline=True)
                if r.get("iban_ic"):
                    embed.add_field(name="IBAN IC", value=r["iban_ic"], inline=True)
                if r.get("steam_url"):
                    embed.add_field(name="Steam", value=r["steam_url"], inline=False)
                embed.add_field(name="Discord ID", value=f"`{r['discord_id']}`", inline=True)
                embed.add_field(name="Motivo", value=r["motivo"], inline=False)
                embed.add_field(name="Staff", value=f"<@{r['staff_id']}>", inline=True)
                embed.add_field(name="Fecha", value=r["fecha"], inline=True)
                embed.add_field(
                    name="Estado",
                    value="\U0001f534 En blacklist",
                    inline=False,
                )
                if r.get("ticket_origen_id"):
                    embed.add_field(name="Ticket origen", value=f"<#{r['ticket_origen_id']}>", inline=True)
                embed.set_footer(text=f"{len(resultados)} coincidencia(s)")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            paginas = []
            for i, r in enumerate(resultados, 1):
                paginas.append(f"{i}. **{r['nombre_ic']}** — `{r['discord_id']}` — {r['fecha'][:10]}")

            embed = discord.Embed(
                title="\U0001f6ab Resultados de búsqueda",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow(),
            )
            for chunk in [paginas[i:i + 10] for i in range(0, len(paginas), 10)]:
                embed.add_field(name="\u200b", value="\n".join(chunk), inline=False)
            embed.set_footer(text=f"{len(resultados)} coincidencia(s)")
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="blacklist-search", description="Busca en la blacklist por Discord ID o Nombre IC")
    async def blacklist_search(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("\u274c Solo puede usarse en un servidor.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not _tiene_permiso(interaction.user):
            await interaction.response.send_message("\u274c No tenés permisos.", ephemeral=True)
            return

        await interaction.response.send_modal(SearchModal())

    # ── /blacklist-sync ─────────────────────

    @bot.tree.command(name="blacklist-sync", description="Verifica y corrige inconsistencias entre DB y rol")
    @app_commands.describe(
        accion="Qué hacer: 'revisar' (default) o 'corregir'",
    )
    async def blacklist_sync(interaction: discord.Interaction, accion: str = "revisar"):
        if not interaction.guild:
            await interaction.response.send_message("\u274c Solo puede usarse en un servidor.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not _tiene_permiso(interaction.user):
            await interaction.response.send_message("\u274c No tenés permisos.", ephemeral=True)
            return

        accion = accion.lower().strip()
        if accion not in ("revisar", "corregir"):
            await interaction.response.send_message(
                "Usá `revisar` para solo ver inconsistencias o `corregir` para arreglarlas.", ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        en_db = set(db.obtener_todos())
        con_rol = set()
        rol_obj = None
        if BLACKLIST_POSTULACIONES_ROLE_ID:
            rol_obj = guild.get_role(BLACKLIST_POSTULACIONES_ROLE_ID)
            if rol_obj:
                con_rol = {str(m.id) for m in rol_obj.members}

            faltan_rol = en_db - con_rol
            sobran_rol = con_rol - en_db

            if not faltan_rol and not sobran_rol:
                await interaction.followup.send(
                    "\u2705 **No hay inconsistencias.** DB y rol están sincronizados.", ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="\U0001f6ab Sincronización de Blacklist",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(
                name="Registros en DB",
                value=str(len(en_db)),
                inline=True,
            )
            embed.add_field(
                name="Miembros con rol",
                value=str(len(con_rol)),
                inline=True,
            )

            lineas_faltan = []
            if faltan_rol:
                for uid in sorted(faltan_rol)[:10]:
                    r = db.obtener(uid)
                    nombre = r["nombre_ic"] if r else "?"
                    lineas_faltan.append(f"• <@{uid}> ({nombre})")
                if len(faltan_rol) > 10:
                    lineas_faltan.append(f"… y {len(faltan_rol)-10} más")
                embed.add_field(
                    name=f"\u26a0\ufe0f En DB sin rol ({len(faltan_rol)})",
                    value="\n".join(lineas_faltan) or "Ninguno",
                    inline=False,
                )

            lineas_sobran = []
            if sobran_rol:
                for uid in sorted(sobran_rol)[:10]:
                    lineas_sobran.append(f"• <@{uid}>")
                if len(sobran_rol) > 10:
                    lineas_sobran.append(f"… y {len(sobran_rol)-10} más")
                embed.add_field(
                    name=f"\u26a0\ufe0f Con rol sin DB ({len(sobran_rol)})",
                    value="\n".join(lineas_sobran) or "Ninguno",
                    inline=False,
                )

            if accion == "corregir":
                corregidos = 0
                for uid in faltan_rol:
                    try:
                        miembro = guild.get_member(int(uid))
                        if miembro and rol_obj:
                            await miembro.add_roles(rol_obj, reason="Blacklist-sync: corrección automática")
                            corregidos += 1
                    except Exception as e:
                        logger.warning("Sync: no se pudo asignar rol a %s: %s", uid, e)

                for uid in sobran_rol:
                    try:
                        miembro = guild.get_member(int(uid))
                        if miembro and rol_obj:
                            await miembro.remove_roles(rol_obj, reason="Blacklist-sync: corrección automática")
                            corregidos += 1
                    except Exception as e:
                        logger.warning("Sync: no se pudo remover rol a %s: %s", uid, e)

                embed.add_field(
                    name="\u2705 Correcciones aplicadas",
                    value=f"Se procesaron {corregidos} cambio(s).",
                    inline=False,
                )
                log_actions.log_info(
                    "\U0001f6ab Blacklist sync",
                    f"Corregidas {corregidos} inconsistencia(s). Staff: {interaction.user}",
                )

            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        await interaction.followup.send(
            "\u26a0\ufe0f `BLACKLIST_POSTULACIONES_ROLE_ID` no está configurado.", ephemeral=True,
        )


# ── Evento: detección de tickets ──────────

def _identificar_creador(
    channel: discord.TextChannel,
    bot: commands.Bot,
) -> int | None:
    """
    Identifica al usuario que abrió el ticket.
    Prioriza miembros sin roles de staff/bypass para evitar
    falsos positivos con entrevistadores agregados automáticamente.
    """
    candidatos: list[discord.Member] = []
    for target, permisos in channel.overwrites.items():
        if not isinstance(target, discord.Member):
            continue
        if target.id == bot.user.id:
            continue
        if permisos.read_messages and permisos.send_messages:
            candidatos.append(target)

    if not candidatos:
        return None

    # Priorizar el primer candidato que NO sea staff
    for c in candidatos:
        if not _es_staff(c):
            return c.id

    # Si todos son staff (ej: canal de prueba), devolver el primero
    return candidatos[0].id


# ── Función centralizada de verificación ──

async def check_ticket_blacklist(
    channel: discord.TextChannel,
    bot: commands.Bot,
    origen: str = "desconocido",
) -> bool:
    """
    Función centralizada que recibe un canal de ticket, identifica
    al creador, consulta la blacklist y notifica si corresponde.

    Args:
        channel: Canal de ticket a verificar.
        bot:     Instancia del bot.
        origen:  Identificador textual del caller (logs).

    Returns:
        True si se notificó al usuario (estaba en blacklist).
        False en cualquier otro caso (no aplica, ya notificado, error, etc.).
    """
    if POSTULACIONES_CATEGORY_ID == 0:
        logger_scanner.debug("[%s] POSTULACIONES_CATEGORY_ID no configurado", origen)
        return False
    if channel.category_id != POSTULACIONES_CATEGORY_ID:
        logger_scanner.debug(
            "[%s] Canal %s no está en categoría postulaciones (cat=%s)",
            origen, channel.id, channel.category_id,
        )
        return False
    if not isinstance(channel, discord.TextChannel):
        logger_scanner.debug("[%s] Canal %s no es TextChannel", origen, channel.id)
        return False
    if channel.id in _tickets_notificados:
        logger_scanner.debug("[%s] Ticket %s ya notificado previamente", origen, channel.id)
        return False

    logger_scanner.info("[%s] Verificando ticket %s", origen, channel.id)

    # ── Identificar creador ─────────────────
    usuario_id = _identificar_creador(channel, bot)

    if not usuario_id:
        logger_scanner.debug("[%s] Sin creador por overwrites, revisando historial", origen)
        try:
            async for msg in channel.history(limit=5, oldest_first=True):
                if msg.author.id != bot.user.id:
                    usuario_id = msg.author.id
                    logger_scanner.debug("[%s] Creador inferido del historial: %s", origen, usuario_id)
                    break
        except Exception:
            pass

    # Fallback 3: audit log (cuando Ticket Tool no expone owner ni overwrites)
    if not usuario_id:
        logger_scanner.debug("[%s] Sin creador por historial, consultando audit log", origen)
        try:
            async for entry in channel.guild.audit_logs(
                action=discord.AuditLogAction.channel_create,
                limit=5,
            ):
                if entry.target.id == channel.id:
                    usuario_id = entry.user.id
                    logger_scanner.debug("[%s] Creador inferido del audit log: %s", origen, usuario_id)
                    break
        except discord.Forbidden:
            logger_scanner.debug("[%s] Sin permiso para audit log en guild %s", origen, channel.guild.id)
        except Exception as e:
            logger_scanner.debug("[%s] Error en audit log para %s: %s", origen, channel.id, e)

    if not usuario_id:
        logger_scanner.info("[%s] No se pudo identificar creador del ticket %s", origen, channel.id)
        return False

    # ── Resolver Member ─────────────────────
    guild = channel.guild
    miembro = guild.get_member(usuario_id)
    if not miembro:
        try:
            miembro = await guild.fetch_member(usuario_id)
            logger_scanner.debug("[%s] Miembro obtenido via fetch: %s", origen, usuario_id)
        except Exception:
            logger_scanner.warning(
                "[%s] No se pudo resolver miembro %s para ticket %s",
                origen, usuario_id, channel.id,
            )
            return False

    if not miembro:
        logger_scanner.warning("[%s] Miembro %s es None tras fetch", origen, usuario_id)
        return False

    # ── Bypass staff ────────────────────────
    if _es_staff(miembro):
        logger_scanner.info("[%s] Usuario %s es staff/bypass, omitiendo", origen, usuario_id)
        log_actions.log_info(
            "\u2139\ufe0f Blacklist: usuario ignorado por bypass",
            f"**Usuario:** {miembro} (`{usuario_id}`)\n"
            f"**Ticket:** {channel.mention}\n"
            f"**Razón:** Tiene rol de staff/bypass — no se aplicó blacklist.",
        )
        return False

    # ── Consultar blacklist ─────────────────
    en_blacklist = False
    registro = None

    try:
        registro = db.obtener(str(usuario_id))
        if registro:
            en_blacklist = True
    except Exception as e:
        logger_scanner.error(
            "[%s] Error consultando blacklist para %s: %s", origen, usuario_id, e,
        )
        if BLACKLIST_ALLOW_ROLE_FALLBACK and BLACKLIST_POSTULACIONES_ROLE_ID:
            rol = guild.get_role(BLACKLIST_POSTULACIONES_ROLE_ID)
            if rol and rol in miembro.roles:
                en_blacklist = True
                registro = {"motivo": "PostgreSQL no disponible · bloqueado por rol de respaldo"}

    if not en_blacklist and BLACKLIST_ALLOW_ROLE_FALLBACK and BLACKLIST_POSTULACIONES_ROLE_ID:
        rol = guild.get_role(BLACKLIST_POSTULACIONES_ROLE_ID)
        if rol and rol in miembro.roles:
            en_blacklist = True
            if not registro:
                registro = {"motivo": "Sin motivo registrado (solo rol presente) · posible inconsistencia"}

    logger_scanner.info("[%s] Usuario %s en blacklist: %s", origen, usuario_id, en_blacklist)

    if not en_blacklist or not registro:
        return False

    # ── Notificar ────────────────────────────
    _tickets_notificados.add(channel.id)
    _persistir_notificados()

    await _notificar_blacklist(channel, registro, miembro)

    try:
        db.registrar_intento(str(usuario_id), str(channel.id), registro.get("motivo"))
    except Exception as e:
        logger_scanner.error(
            "[%s] Error registrando intento en DB para %s: %s", origen, usuario_id, e,
        )

    log_actions.log_info(
        "\U0001f6ab Ticket blacklist detectado",
        f"**Usuario:** {miembro} (`{usuario_id}`)\n"
        f"**Ticket:** {channel.mention}\n"
        f"**Motivo:** {registro['motivo']}\n"
        f"**Origen:** {origen}",
    )

    # ── Alerta opcional a staff ─────────────
    if BLACKLIST_STAFF_ALERT_CHANNEL_ID:
        alert_channel = guild.get_channel(BLACKLIST_STAFF_ALERT_CHANNEL_ID)
        if alert_channel and isinstance(alert_channel, discord.TextChannel):
            try:
                nombre_ic = None
                try:
                    mensajes = await _obtener_mensajes_ticket(channel, limite=10)
                    nombre_ic = _extraer_nombre_ic(mensajes)
                except Exception:
                    pass

                link_ticket = f"https://discord.com/channels/{guild.id}/{channel.id}"
                desc_lines = [
                    f"**Usuario:** {miembro.mention} (`{usuario_id}`)",
                    f"**Discord ID:** `{usuario_id}`",
                ]
                if nombre_ic:
                    desc_lines.append(f"**Nombre IC:** {nombre_ic}")
                desc_lines.append(f"**Motivo:** {registro['motivo']}")
                desc_lines.append(f"**Ticket:** {channel.mention} ([Abrir]({link_ticket}))")
                desc_lines.append("")
                desc_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
                desc_lines.append(
                    "**⚠ Acción requerida:** revisar y cerrar este ticket "
                    "mediante Ticket Tool."
                )

                alert_embed = discord.Embed(
                    title="\u26a0\ufe0f Ticket de blacklist abierto",
                    description="\n".join(desc_lines),
                    color=0xe74c3c,
                )
                kwargs: dict = {"embed": alert_embed}
                if BLACKLIST_STAFF_ALERT_ROLE_ID:
                    alert_role = guild.get_role(BLACKLIST_STAFF_ALERT_ROLE_ID)
                    if alert_role:
                        kwargs["content"] = alert_role.mention
                await alert_channel.send(**kwargs)
            except Exception:
                pass

    logger_scanner.info("[%s] Aviso enviado en ticket %s para %s", origen, channel.id, usuario_id)
    return True


async def _on_guild_channel_create(channel: discord.abc.GuildChannel, bot: commands.Bot):
    """Wrapper para on_guild_channel_create que delega en check_ticket_blacklist."""
    await check_ticket_blacklist(channel, bot, origen="channel_create")


def _setup_ticket_event(bot: commands.Bot):
    async def wrapper(channel):
        await _on_guild_channel_create(channel, bot)
    bot.add_listener(wrapper, "on_guild_channel_create")


# ── Detección por cambio de categoría ────

async def _on_guild_channel_update(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel, bot: commands.Bot):
    """Detecta cuando Ticket Tool mueve un canal a la categoría de postulaciones
    después de crearlo (on_guild_channel_create se dispara antes de que
    la categoría esté asignada)."""
    if before.category_id == after.category_id:
        return
    if after.category_id != POSTULACIONES_CATEGORY_ID:
        return
    logger_scanner.info(
        "channel_update: canal %s movido a categoría postulaciones (antes: %s)",
        after.id, before.category_id,
    )
    await check_ticket_blacklist(after, bot, origen="channel_update")


def _setup_ticket_update_event(bot: commands.Bot):
    async def wrapper(before, after):
        await _on_guild_channel_update(before, after, bot)
    bot.add_listener(wrapper, "on_guild_channel_update")


# ── Fallback: detección por mensaje ──────

def _setup_ticket_fallback(bot: commands.Bot):
    """Escucha on_message como respaldo adicional. Delega en check_ticket_blacklist."""
    async def wrapper(message: discord.Message):
        if message.author.id == bot.user.id:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return
        if message.channel.category_id != POSTULACIONES_CATEGORY_ID:
            return
        if message.channel.id in _tickets_notificados:
            return
        if _es_staff(message.author):
            return
        await check_ticket_blacklist(message.channel, bot, origen="message_fallback")
    bot.add_listener(wrapper, "on_message")


async def _on_member_join(member: discord.Member, bot: commands.Bot):
    """Asigna el rol de blacklist automáticamente si el miembro está en la DB."""
    if member.bot:
        return
    if BLACKLIST_POSTULACIONES_ROLE_ID == 0:
        return

    try:
        registro = db.obtener(str(member.id))
        if not registro:
            return
    except Exception as e:
        logger.warning("Error consultando blacklist en on_member_join para %s: %s", member.id, e)
        return

    rol = member.guild.get_role(BLACKLIST_POSTULACIONES_ROLE_ID)
    if not rol:
        logger.warning("BLACKLIST_POSTULACIONES_ROLE_ID %s no encontrado en on_member_join", BLACKLIST_POSTULACIONES_ROLE_ID)
        return

    if rol in member.roles:
        return

    try:
        await member.add_roles(rol, reason="Blacklist activa en DB · reingreso al servidor")
        logger.info("Rol de blacklist asignado automáticamente a %s (%s) por reingreso", member, member.id)
        log_actions.log_info(
            "\U0001f6ab Rol blacklist asignado por reingreso",
            f"**Usuario:** {member.mention} (`{member.id}`)\n"
            f"**Nombre IC:** {registro.get('nombre_ic', 'Desconocido')}",
        )
    except Exception as e:
        logger.error("No se pudo asignar rol de blacklist a %s por reingreso: %s", member.id, e)
        await log_actions.log_error(
            "\U0001f6ab Error asignando rol blacklist por reingreso",
            f"Usuario: <@{member.id}>\nRol: <@&{BLACKLIST_POSTULACIONES_ROLE_ID}>\nError: `{e}`",
        )


def _setup_member_join_event(bot: commands.Bot):
    async def wrapper(member):
        await _on_member_join(member, bot)
    bot.add_listener(wrapper, "on_member_join")


# ── Limpieza de notificados antiguos ──────

async def _limpiar_notificados_antiguos(bot: commands.Bot):
    """
    Recorre _tickets_notificados y descarta aquellos canales
    que ya no existen (tickets cerrados/eliminados).
    Evita que el archivo persistente crezca sin límite.
    """
    if not _tickets_notificados:
        return

    antes = len(_tickets_notificados)
    ids_a_remover = []

    for cid in list(_tickets_notificados):
        canal = bot.get_channel(cid)
        if canal is None:
            ids_a_remover.append(cid)

    for cid in ids_a_remover:
        _tickets_notificados.discard(cid)

    despues = len(_tickets_notificados)
    if ids_a_remover:
        _persistir_notificados()
        logger_scanner.info(
            "Limpieza de notificados antiguos: %s eliminados (%s → %s)",
            len(ids_a_remover), antes, despues,
        )


# ── Escaneo inicial tras reinicio ─────────

async def scan_open_tickets(bot: commands.Bot):
    """
    Recorre todos los servidores y canales dentro de la categoría
    de postulaciones, verificando si el creador de cada ticket
    abierto está en blacklist.

    Se ejecuta una vez al iniciar el bot para cubrir tickets que
    quedaron abiertos antes del reinicio.
    """
    await bot.wait_until_ready()

    # Limpiar notificados de canales que ya no existen
    await _limpiar_notificados_antiguos(bot)

    if POSTULACIONES_CATEGORY_ID == 0:
        logger_scanner.warning("scan_open_tickets: POSTULACIONES_CATEGORY_ID no configurado")
        return

    logger_scanner.info("Escaneando tickets abiertos...")
    total = 0
    notificados = 0
    errores = 0

    for guild in bot.guilds:
        categoria = guild.get_channel(POSTULACIONES_CATEGORY_ID)
        if not categoria:
            logger_scanner.debug("Categoría %s no encontrada en guild %s", POSTULACIONES_CATEGORY_ID, guild.id)
            continue

        canales = getattr(categoria, "channels", [])
        for channel in canales:
            if not isinstance(channel, discord.TextChannel):
                continue
            if channel.id in _tickets_notificados:
                continue

            total += 1
            logger_scanner.info("Ticket encontrado: canal=%s guild=%s", channel.id, guild.id)

            try:
                if await check_ticket_blacklist(channel, bot, origen="startup_scan"):
                    notificados += 1
            except Exception as e:
                errores += 1
                logger_scanner.error(
                    "Error escaneando ticket %s en guild %s: %s",
                    channel.id, guild.id, e,
                )

        # Pequeña pausa entre servidores para no saturar la API
        if len(bot.guilds) > 1:
            await asyncio.sleep(0.5)

    logger_scanner.info(
        "Escaneo completado: %s tickets revisados, %s notificaciones enviadas, %s errores",
        total, notificados, errores,
    )
    if notificados > 0:
        log_actions.log_info(
            "\U0001f6ab Escaneo inicial blacklist",
            f"Se revisaron {total} tickets abiertos.\n"
            f"**Notificaciones enviadas:** {notificados}\n"
            f"**Errores:** {errores}",
        )


class BlacklistCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    db.init()
    _setup_blacklist_commands(bot)
    _setup_ticket_event(bot)
    _setup_ticket_update_event(bot)
    _setup_ticket_fallback(bot)
    _setup_member_join_event(bot)
    _cargar_notificados()
    logger.info("Módulo de blacklist de postulaciones cargado")
    # Escaneo inicial de tickets abiertos (recuperación tras reinicio)
    asyncio.create_task(scan_open_tickets(bot))
