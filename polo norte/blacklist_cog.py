import re
import logging
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
# Riesgo aceptado: tras reinicio del bot, _tickets_notificados se limpia.
# Si un ticket previo sigue abierto cuando el bot reinicia y el evento
# on_guild_channel_create NO se vuelve a disparar (el canal ya existe),
# no hay duplicado. El riesgo es solo si el bot procesa el evento después
# de reiniciar, pero canales nuevos se crean mientras el bot está vivo.
_tickets_notificados: set[int] = set()

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


# ── Extracción de Nombre IC ──────────────

def _extraer_nombre_ic(mensajes: list[discord.Message]) -> str:
    patrones = [
        re.compile(r"(?:→\s*)?nombre\s*ic\s*:?\s*(.+)", re.IGNORECASE),
        re.compile(r"(?:→\s*)?nombre\s*ic\s*:?\s*\n\s*(.+)", re.IGNORECASE),
    ]

    for msg in mensajes:
        for patron in patrones:
            m = patron.search(msg.content)
            if m:
                return m.group(1).strip()

    for msg in mensajes:
        for embed in msg.embeds:
            for field in embed.fields:
                for patron in patrones:
                    m = patron.search(field.name + "\n" + (field.value or ""))
                    if m:
                        return m.group(1).strip()
            for patron in patrones:
                m = patron.search((embed.description or "") + "\n" + (embed.title or ""))
                if m:
                    return m.group(1).strip()

    for msg in mensajes:
        for embed in msg.embeds:
            for field in embed.fields:
                if "nombre" in field.name.lower() and "ic" in field.name.lower():
                    val = (field.value or "").strip()
                    if val:
                        return val

    return None


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


# ── Comandos ──────────────────────────────

ENTRIES_PER_PAGE = 10


def _setup_blacklist_commands(bot: commands.Bot):

    # ── /blacklist ──────────────────────────

    @bot.tree.command(name="blacklist", description="Agrega un usuario a la blacklist de postulaciones")
    @app_commands.describe(usuario="Usuario a blacklistear", motivo="Motivo de la blacklist")
    async def blacklist(interaction: discord.Interaction, usuario: discord.Member, motivo: str):
        if not interaction.guild:
            await interaction.response.send_message("\u274c Solo puede usarse en un servidor.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or not _tiene_permiso(interaction.user):
            await interaction.response.send_message("\u274c No tenés permisos.", ephemeral=True)
            return

        uid = str(usuario.id)

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
            embed.set_footer(text="Usá /unblacklist para remover o /blacklist-info para detalles")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        nombre_ic = "Desconocido"
        ticket_origen_id = None

        categoria = interaction.guild.get_channel(POSTULACIONES_CATEGORY_ID)
        if categoria:
            for channel in categoria.channels:
                if not isinstance(channel, discord.TextChannel):
                    continue
                canal_ticket: discord.TextChannel = channel
                try:
                    permisos = canal_ticket.permissions_for(usuario)
                except Exception:
                    continue
                if not (permisos.read_messages and permisos.send_messages):
                    continue

                mensajes = await _obtener_mensajes_ticket(canal_ticket, limite=30)
                extraido = _extraer_nombre_ic(mensajes)
                if extraido:
                    nombre_ic = extraido
                    ticket_origen_id = str(canal_ticket.id)
                    break

        creado = db.agregar(
            discord_id=uid,
            nombre_ic=nombre_ic,
            motivo=motivo,
            staff_id=str(interaction.user.id),
            ticket_origen_id=ticket_origen_id,
        )

        if not creado:
            await interaction.followup.send(
                "\u274c Error inesperado al crear la blacklist (posible duplicado).", ephemeral=True,
            )
            return

        rol_ok = True
        if BLACKLIST_POSTULACIONES_ROLE_ID:
            rol = interaction.guild.get_role(BLACKLIST_POSTULACIONES_ROLE_ID)
            if rol:
                try:
                    await usuario.add_roles(rol, reason="Blacklist de postulaciones")
                except Exception as e:
                    rol_ok = False
                    logger.error("No se pudo asignar rol de blacklist a %s: %s", usuario.id, e)
                    await log_actions.log_error(
                        "\U0001f6ab Error asignando rol blacklist",
                        f"Usuario: <@{uid}>\nRol: <@&{BLACKLIST_POSTULACIONES_ROLE_ID}>\nError: `{e}`",
                    )

        if nombre_ic == "Desconocido":
            await interaction.followup.send(
                "\u26a0\ufe0f No se pudo extraer automáticamente el Nombre IC. Se registrará como **Desconocido**.",
                ephemeral=True,
            )

        embed = discord.Embed(
            title="\U0001f6ab Blacklist de Postulaciones",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Nombre IC", value=nombre_ic, inline=True)
        embed.add_field(name="Discord", value=f"<@{usuario.id}>\n`{usuario.id}`", inline=False)
        embed.add_field(name="Motivo", value=motivo, inline=False)
        embed.add_field(name="Aplicada por", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=True)
        embed.add_field(name="Fecha", value=discord.utils.utcnow().strftime("%d/%m/%Y %H:%M UTC"), inline=True)
        if ticket_origen_id:
            embed.add_field(name="Ticket origen", value=f"<#{ticket_origen_id}>", inline=True)
        if not rol_ok:
            embed.add_field(name="\u26a0\ufe0f Rol", value="No se pudo asignar (revisar jerarquía).", inline=False)
        embed.set_footer(text=f"ID: {usuario.id}")

        await _enviar_embed_log(bot, embed)

        log_actions.log_info(
            "\U0001f6ab Blacklist aplicada",
            f"**Usuario:** {usuario} (`{usuario.id}`)\n"
            f"**Nombre IC:** {nombre_ic}\n"
            f"**Motivo:** {motivo}\n"
            f"**Staff:** {interaction.user} (`{interaction.user.id}`)",
        )

        if rol_ok:
            await interaction.followup.send("\u2705 Blacklist aplicada correctamente.", ephemeral=True)
        else:
            await interaction.followup.send(
                "\u26a0\ufe0f Blacklist aplicada en DB, pero **no se pudo asignar el rol**."
                " Revisá la jerarquía del bot.",
                ephemeral=True,
            )

    # ── /unblacklist ────────────────────────

    @bot.tree.command(name="unblacklist", description="Quita un usuario de la blacklist de postulaciones")
    @app_commands.describe(usuario="Usuario a desblacklistear")
    async def unblacklist(interaction: discord.Interaction, usuario: discord.Member):
        if not interaction.guild:
            await interaction.response.send_message("\u274c Solo puede usarse en un servidor.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or not _tiene_permiso(interaction.user):
            await interaction.response.send_message("\u274c No tenés permisos.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        uid = str(usuario.id)

        registro_previo = db.obtener(uid)
        eliminado = db.eliminar(uid)

        rol_ok = True
        if BLACKLIST_POSTULACIONES_ROLE_ID:
            rol = interaction.guild.get_role(BLACKLIST_POSTULACIONES_ROLE_ID)
            if rol and rol in usuario.roles:
                try:
                    await usuario.remove_roles(rol, reason="Unblacklist de postulaciones")
                except Exception as e:
                    rol_ok = False
                    logger.error("No se pudo remover rol de blacklist a %s: %s", usuario.id, e)
                    await log_actions.log_error(
                        "\u2705 Error removiendo rol blacklist",
                        f"Usuario: <@{uid}>\nError: `{e}`",
                    )

        if not eliminado and not (rol and rol in usuario.roles if BLACKLIST_POSTULACIONES_ROLE_ID and (rol := interaction.guild.get_role(BLACKLIST_POSTULACIONES_ROLE_ID)) else False):
            await interaction.followup.send(
                f"\u26a0\ufe0f {usuario.mention} no estaba en blacklist ni tenía el rol.", ephemeral=True,
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
        embed_log.add_field(name="Discord", value=f"<@{usuario.id}>\n`{usuario.id}`", inline=False)
        embed_log.add_field(name="Retirada por", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=True)
        embed_log.add_field(name="Fecha", value=discord.utils.utcnow().strftime("%d/%m/%Y %H:%M UTC"), inline=True)
        embed_log.add_field(name="Motivo original", value=motivo_original, inline=False)
        if not eliminado:
            embed_log.add_field(name="\u26a0\ufe0f Nota", value="Solo se removió el rol (no estaba en DB).", inline=False)
        if not rol_ok:
            embed_log.add_field(name="\u26a0\ufe0f Rol", value="No se pudo remover (revisar jerarquía).", inline=False)
        embed_log.set_footer(text=f"ID: {usuario.id}")

        await _enviar_embed_log(bot, embed_log)

        log_actions.log_info(
            "\u2705 Blacklist removida",
            f"**Usuario:** {usuario} (`{usuario.id}`)\n"
            f"**Nombre IC:** {nombre_ic_log}\n"
            f"**Motivo original:** {motivo_original}\n"
            f"**Staff:** {interaction.user} (`{interaction.user.id}`)",
        )

        await interaction.followup.send(f"\u2705 {usuario.mention} procesado.", ephemeral=True)

    # ── /blacklist-info ─────────────────────

    @bot.tree.command(name="blacklist-info", description="Muestra información de blacklist de un usuario")
    @app_commands.describe(usuario="Usuario a consultar")
    async def blacklist_info(interaction: discord.Interaction, usuario: discord.Member):
        if not interaction.guild:
            await interaction.response.send_message("\u274c Solo puede usarse en un servidor.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or not _tiene_permiso(interaction.user):
            await interaction.response.send_message("\u274c No tenés permisos.", ephemeral=True)
            return

        uid = str(usuario.id)
        registro = db.obtener(uid)
        tiene_rol = False
        if BLACKLIST_POSTULACIONES_ROLE_ID:
            rol = interaction.guild.get_role(BLACKLIST_POSTULACIONES_ROLE_ID)
            if rol and rol in usuario.roles:
                tiene_rol = True

        embed = discord.Embed(
            title="\U0001f6ab Información de Blacklist",
            color=discord.Color.red() if registro else discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )

        if registro:
            embed.add_field(name="Nombre IC", value=registro["nombre_ic"], inline=True)
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
            embed.description = f"{usuario.mention} **no** está en la blacklist de postulaciones."
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


async def _on_guild_channel_create(channel: discord.abc.GuildChannel, bot: commands.Bot):
    # ── Filtros rápidos de descarte ────────
    if POSTULACIONES_CATEGORY_ID == 0:
        return
    if channel.category_id != POSTULACIONES_CATEGORY_ID:
        return
    if not isinstance(channel, discord.TextChannel):
        return

    canal: discord.TextChannel = channel

    # Protección spam en memoria (ver §3 en análisis)
    if canal.id in _tickets_notificados:
        return
    _tickets_notificados.add(canal.id)

    # ── Identificar creador del ticket ────
    usuario_id = _identificar_creador(canal, bot)

    if not usuario_id:
        try:
            async for msg in canal.history(limit=5, oldest_first=True):
                if msg.author.id != bot.user.id:
                    usuario_id = msg.author.id
                    break
        except Exception:
            pass

    if not usuario_id:
        return

    guild = canal.guild
    miembro = guild.get_member(usuario_id)
    if not miembro:
        try:
            miembro = await guild.fetch_member(usuario_id)
        except Exception:
            return

    if not miembro:
        return

    # ── Bypass: staff / roles excluidos (Punto 1) ──
    if _es_staff(miembro):
        log_actions.log_info(
            "\u2139\ufe0f Blacklist: usuario ignorado por bypass",
            f"**Usuario:** {miembro} (`{usuario_id}`)\n"
            f"**Ticket:** {canal.mention}\n"
            f"**Razón:** Tiene rol de staff/bypass — no se aplicó blacklist.",
        )
        return

    # ── Verificar blacklist ─────────────────
    en_blacklist = False
    registro = None

    try:
        registro = db.obtener(str(usuario_id))
        if registro:
            en_blacklist = True
    except Exception as e:
        logger.error(
            "Error al consultar blacklist en DB para %s (ticket %s): %s",
            usuario_id, canal.id, e,
        )
        # DB caída: no sabemos si está en blacklist.
        # Si hay fallback por rol y está configurado, lo usamos como respaldo.
        if BLACKLIST_ALLOW_ROLE_FALLBACK and BLACKLIST_POSTULACIONES_ROLE_ID:
            rol = guild.get_role(BLACKLIST_POSTULACIONES_ROLE_ID)
            if rol and rol in miembro.roles:
                en_blacklist = True
                registro = {"motivo": "PostgreSQL no disponible · bloqueado por rol de respaldo"}

    # Configurable: permitir o no usar el rol como respaldo (Punto 7)
    if not en_blacklist and BLACKLIST_ALLOW_ROLE_FALLBACK and BLACKLIST_POSTULACIONES_ROLE_ID:
        rol = guild.get_role(BLACKLIST_POSTULACIONES_ROLE_ID)
        if rol and rol in miembro.roles:
            en_blacklist = True
            if not registro:
                registro = {"motivo": "Sin motivo registrado (solo rol presente) · posible inconsistencia"}

    if en_blacklist and registro:
        await _notificar_blacklist(canal, registro, miembro)

        try:
            db.registrar_intento(str(usuario_id), str(canal.id), registro.get("motivo"))
        except Exception as e:
            logger.error(
                "Error al registrar intento en DB para %s (ticket %s): %s",
                usuario_id, canal.id, e,
            )

        log_actions.log_info(
            "\U0001f6ab Ticket blacklist detectado",
            f"**Usuario:** {miembro} (`{usuario_id}`)\n"
            f"**Ticket:** {canal.mention}\n"
            f"**Motivo:** {registro['motivo']}",
        )

        # ── Alerta opcional a staff (Punto 8) ──
        if BLACKLIST_STAFF_ALERT_CHANNEL_ID:
            alert_channel = guild.get_channel(BLACKLIST_STAFF_ALERT_CHANNEL_ID)
            if alert_channel and isinstance(alert_channel, discord.TextChannel):
                try:
                    # Intentar obtener Nombre IC de los mensajes disponibles
                    nombre_ic = None
                    try:
                        mensajes = await _obtener_mensajes_ticket(canal, limite=10)
                        nombre_ic = _extraer_nombre_ic(mensajes)
                    except Exception:
                        pass

                    link_ticket = f"https://discord.com/channels/{guild.id}/{canal.id}"
                    desc_lines = [
                        f"**Usuario:** {miembro.mention} (`{usuario_id}`)",
                        f"**Discord ID:** `{usuario_id}`",
                    ]
                    if nombre_ic:
                        desc_lines.append(f"**Nombre IC:** {nombre_ic}")
                    desc_lines.append(f"**Motivo:** {registro['motivo']}")
                    desc_lines.append(f"**Ticket:** {canal.mention} ([Abrir]({link_ticket}))")
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


def _setup_ticket_event(bot: commands.Bot):
    async def wrapper(channel):
        await _on_guild_channel_create(channel, bot)
    bot.add_listener(wrapper, "on_guild_channel_create")


class BlacklistCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    db.init()
    _setup_blacklist_commands(bot)
    _setup_ticket_event(bot)
    logger.info("Módulo de blacklist de postulaciones cargado")
