import logging
import discord
from discord import app_commands
import log_actions
import config_manager

logger = logging.getLogger("Aprobar")

CANAL_SOLICITUD_ID = 1363287550327783475
ROL_AUTORIZADO_ID = 1307612928211554386


def _tiene_permiso(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    if member.guild_permissions.manage_roles:
        return True
    return any(role.id == ROL_AUTORIZADO_ID for role in member.roles)


def _obtener_tipo_acceso(member: discord.Member) -> str:
    if member.guild_permissions.administrator:
        return "🛡️ Administrador"
    if member.guild_permissions.manage_roles:
        return "⚙️ Gestionar Roles"
    if any(role.id == ROL_AUTORIZADO_ID for role in member.roles):
        return f"🔑 <@&{ROL_AUTORIZADO_ID}>"
    return "❓ Desconocido"


def _validar_jerarquia(guild: discord.Guild, roles: list[discord.Role]) -> tuple[list[discord.Role], list[discord.Role]]:
    bot_top = guild.me.top_role
    validos = []
    bloqueados = []
    for role in roles:
        if role >= bot_top:
            bloqueados.append(role)
        else:
            validos.append(role)
    return validos, bloqueados


def _obtener_roles_faltantes(member: discord.Member) -> list[discord.Role]:
    config = config_manager.load_aprobar_config()
    faltantes = []
    for role_id in config.get("roles_asignar", []):
        role = member.guild.get_role(role_id)
        if role is None:
            logger.warning("Rol %s no encontrado en el servidor", role_id)
            continue
        if role not in member.roles:
            faltantes.append(role)
    return faltantes


def _obtener_roles_a_eliminar(member: discord.Member) -> list[discord.Role]:
    config = config_manager.load_aprobar_config()
    a_eliminar = []
    for role_id in config.get("roles_eliminar", []):
        role = member.guild.get_role(role_id)
        if role is None:
            logger.warning("Rol a eliminar %s no encontrado en el servidor", role_id)
            continue
        if role in member.roles:
            a_eliminar.append(role)
    return a_eliminar


async def _asignar_roles(member: discord.Member, roles: list[discord.Role]) -> tuple[list[discord.Role], list[discord.Role]]:
    asignados = []
    errores = []
    for role in roles:
        try:
            await member.add_roles(role, reason="Aprobación de postulación")
            asignados.append(role)
        except discord.Forbidden:
            logger.error("Permisos insuficientes para asignar %s a %s", role.id, member.id)
            errores.append(role)
        except Exception as e:
            logger.error("Error asignando rol %s a %s: %s", role.id, member.id, e)
            errores.append(role)
    return asignados, errores


async def _eliminar_roles(member: discord.Member, roles: list[discord.Role]) -> tuple[list[discord.Role], list[discord.Role]]:
    eliminados = []
    errores = []
    for role in roles:
        try:
            await member.remove_roles(role, reason="Aprobación de postulación - limpieza")
            eliminados.append(role)
        except discord.Forbidden:
            logger.error("Permisos insuficientes para eliminar %s a %s", role.id, member.id)
            errores.append(role)
        except Exception as e:
            logger.error("Error eliminando rol %s a %s: %s", role.id, member.id, e)
            errores.append(role)
    return eliminados, errores


async def _enviar_auditoria(
    member: discord.Member,
    admin: discord.Member,
    tipo_acceso: str,
    asignados: list[discord.Role],
    eliminados: list[discord.Role],
    errores_asignar: list[discord.Role],
    errores_eliminar: list[discord.Role],
    bloqueados: list[discord.Role],
    canal: discord.TextChannel,
):
    tiene_error = bool(errores_asignar) or bool(errores_eliminar) or bool(bloqueados)
    estado = "✅ aprobado" if not tiene_error else "⚠️ aprobado con errores"
    color = discord.Color.green() if not tiene_error else discord.Color.orange()

    embed = discord.Embed(title="📋 Aprobación de postulación", color=color, timestamp=discord.utils.utcnow())
    embed.add_field(name="👤 Usuario aprobado", value=f"{member.mention}\n`{member.id}`", inline=True)
    embed.add_field(name="🛠 Administrador", value=f"{admin.mention}\n`{admin.id}`", inline=True)
    embed.add_field(name="🔑 Acceso", value=tipo_acceso, inline=True)
    embed.add_field(name="📅 Fecha y hora", value=discord.utils.utcnow().strftime("%d/%m/%Y %H:%M:%S UTC"), inline=False)

    roles_asignados_str = "\n".join(f"<@&{r.id}>" for r in asignados) or "Ninguno"
    embed.add_field(name="✅ Roles asignados", value=roles_asignados_str, inline=False)

    if eliminados:
        roles_eliminados_str = "\n".join(f"<@&{r.id}>" for r in eliminados)
        embed.add_field(name="🗑️ Roles eliminados", value=roles_eliminados_str, inline=False)

    if bloqueados:
        roles_bloqueados_str = "\n".join(f"<@&{r.id}>" for r in bloqueados)
        embed.add_field(name="⛔ Roles bloqueados (jerarquía)", value=roles_bloqueados_str, inline=False)

    if errores_asignar:
        errores_asignar_str = "\n".join(f"<@&{r.id}>" for r in errores_asignar)
        embed.add_field(name="❌ Error al asignar", value=errores_asignar_str, inline=False)

    if errores_eliminar:
        errores_eliminar_str = "\n".join(f"<@&{r.id}>" for r in errores_eliminar)
        embed.add_field(name="❌ Error al eliminar", value=errores_eliminar_str, inline=False)

    embed.add_field(name="📍 Canal", value=canal.mention, inline=True)
    embed.add_field(name="📌 Estado", value=estado, inline=True)
    embed.set_footer(text=f"ID: {member.id}")

    log_channel = await log_actions._get_channel()
    if log_channel:
        await log_channel.send(embed=embed)


async def _enviar_felicitaciones(channel: discord.TextChannel, member: discord.Member):
    guild_id = channel.guild.id
    url_canal = f"https://discord.com/channels/{guild_id}/{CANAL_SOLICITUD_ID}"

    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="🔒 Solicitar canal privado",
        style=discord.ButtonStyle.link,
        url=url_canal,
    ))

    await channel.send(
        content=f"🎉 **¡Felicitaciones {member.mention}!**\n\n"
                f"Tu postulación fue aprobada y ya formas parte de **Polo Norte**.\n\n"
                f"El siguiente paso es solicitar tu **canal privado** para continuar con el proceso.",
        view=view,
    )


@app_commands.command(name="aprobar", description="Aprueba la postulación de un usuario")
@app_commands.describe(usuario="Usuario a aprobar")
@app_commands.default_permissions(administrator=True)
async def aprobar(interaction: discord.Interaction, usuario: discord.Member):
    if not interaction.guild:
        await interaction.response.send_message("❌ Este comando solo puede usarse en un servidor.", ephemeral=True)
        return

    admin = interaction.user
    canal = interaction.channel

    if not isinstance(admin, discord.Member):
        await interaction.response.send_message("❌ No se pudo verificar tus permisos.", ephemeral=True)
        return

    if not _tiene_permiso(admin):
        await interaction.response.send_message(
            "❌ No tenés permisos suficientes para usar este comando.\n"
            "Necesitás el permiso **Administrador**, **Gestionar Roles**, "
            f"o el rol <@&{ROL_AUTORIZADO_ID}>.",
            ephemeral=True,
        )
        return

    if not isinstance(canal, discord.TextChannel):
        await interaction.response.send_message("❌ Este comando debe ejecutarse en un canal de texto.", ephemeral=True)
        return

    if usuario == admin:
        await interaction.response.send_message("❌ No puedes aprobarte a ti mismo.", ephemeral=True)
        return

    if usuario.bot:
        await interaction.response.send_message("❌ No puedes aprobar un bot.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    roles_faltantes = _obtener_roles_faltantes(usuario)
    roles_a_eliminar = _obtener_roles_a_eliminar(usuario)

    if not roles_faltantes and not roles_a_eliminar:
        await interaction.followup.send(
            f"ℹ️ {usuario.mention} ya tiene todos los roles asignados y no posee roles a eliminar.",
            ephemeral=True,
        )
        return

    roles_validos, roles_bloqueados = _validar_jerarquia(interaction.guild, roles_faltantes)

    if roles_bloqueados:
        bloqueados_str = "\n".join(f"• <@&{r.id}> (posición {r.position})" for r in roles_bloqueados)
        await interaction.followup.send(
            f"⚠️ No se pueden asignar los siguientes roles porque están por encima del bot en la jerarquía:\n"
            f"{bloqueados_str}\n\n"
            f"Mové el rol del bot por encima de estos roles e intentá de nuevo.",
            ephemeral=True,
        )

    if not roles_validos and not roles_a_eliminar:
        await interaction.followup.send(
            "❌ No se puede asignar ningún rol por jerarquía. Revisá la posición del bot.",
            ephemeral=True,
        )
        return

    asignados, errores_asignar = await _asignar_roles(usuario, roles_validos)
    eliminados, errores_eliminar = await _eliminar_roles(usuario, roles_a_eliminar)

    try:
        await _enviar_felicitaciones(canal, usuario)
    except Exception as e:
        logger.error("Error enviando felicitaciones: %s", e)
        errores_asignar.append(None)

    tipo_acceso = _obtener_tipo_acceso(admin)

    try:
        await _enviar_auditoria(
            usuario, admin, tipo_acceso,
            asignados, eliminados,
            errores_asignar, errores_eliminar,
            roles_bloqueados, canal,
        )
    except Exception as e:
        logger.error("Error enviando auditoría: %s", e)

    errores_reales_asignar = [r for r in errores_asignar if r is not None]
    errores_reales_eliminar = [r for r in errores_eliminar if r is not None]
    tiene_error = bool(errores_reales_asignar) or bool(errores_reales_eliminar) or bool(roles_bloqueados)

    if not tiene_error:
        partes = [f"✅ {usuario.mention} fue aprobado correctamente."]
        partes.append(f"Roles asignados: {len(asignados)}")
        if eliminados:
            partes.append(f"Roles eliminados: {len(eliminados)}")
        await interaction.followup.send("\n".join(partes), ephemeral=True)
    else:
        partes = [
            f"⚠️ Aprobación completada con errores.\n"
            f"✅ Asignados: {len(asignados)}"
        ]
        if eliminados:
            partes[0] += f" | 🗑️ Eliminados: {len(eliminados)}"
        if roles_bloqueados:
            ids_bloq = ", ".join(f"<@&{r.id}>" for r in roles_bloqueados)
            partes.append(f"⛔ Bloqueados por jerarquía: {ids_bloq}")
        if errores_reales_asignar:
            ids_err = ", ".join(f"<@&{r.id}>" for r in errores_reales_asignar)
            partes.append(f"❌ Error al asignar: {ids_err}")
        if errores_reales_eliminar:
            ids_err = ", ".join(f"<@&{r.id}>" for r in errores_reales_eliminar)
            partes.append(f"❌ Error al eliminar: {ids_err}")
        await interaction.followup.send("\n".join(partes), ephemeral=True)


async def setup(bot):
    bot.tree.add_command(aprobar)
    logger.info("Comando /aprobar registrado")
