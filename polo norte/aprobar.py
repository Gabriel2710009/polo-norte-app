import logging
import discord
from discord import app_commands
import log_actions

logger = logging.getLogger("Aprobar")

ROLES_A_ASIGNAR = [
    1306126579482628106,
    1306129853111599106,
    1305968998206148760,
    1307900695264890991,
    1306131154360860674,
    1335265463441162350,
    1415042052051173516,
    1410719738484494346,
]

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
    faltantes = []
    for role_id in ROLES_A_ASIGNAR:
        role = member.guild.get_role(role_id)
        if role is None:
            logger.warning("Rol %s no encontrado en el servidor", role_id)
            continue
        if role not in member.roles:
            faltantes.append(role)
    return faltantes


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


async def _enviar_auditoria(member: discord.Member, admin: discord.Member, tipo_acceso: str, asignados: list[discord.Role], errores: list[discord.Role], bloqueados: list[discord.Role], canal: discord.TextChannel):
    errores_reales = [r for r in errores if r is not None]
    tiene_error = bool(errores_reales) or bool(bloqueados)
    estado = "✅ aprobado" if not tiene_error else "⚠️ aprobado con errores"
    color = discord.Color.green() if not tiene_error else discord.Color.orange()

    roles_asignados = "\n".join(f"<@&{r.id}>" for r in asignados) or "Ninguno"
    roles_error = "\n".join(f"<@&{r.id}>" for r in errores_reales) or "Ninguno"
    roles_bloqueados = "\n".join(f"<@&{r.id}>" for r in bloqueados) or "Ninguno"

    embed = discord.Embed(title="📋 Aprobación de postulación", color=color, timestamp=discord.utils.utcnow())
    embed.add_field(name="👤 Usuario aprobado", value=f"{member.mention}\n`{member.id}`", inline=True)
    embed.add_field(name="🛠 Administrador", value=f"{admin.mention}\n`{admin.id}`", inline=True)
    embed.add_field(name="🔑 Acceso", value=tipo_acceso, inline=True)
    embed.add_field(name="📅 Fecha y hora", value=discord.utils.utcnow().strftime("%d/%m/%Y %H:%M:%S UTC"), inline=False)
    embed.add_field(name="✅ Roles asignados", value=roles_asignados, inline=False)
    if bloqueados:
        embed.add_field(name="⛔ Roles bloqueados (jerarquía)", value=roles_bloqueados, inline=False)
    if errores_reales:
        embed.add_field(name="❌ Roles con error", value=roles_error, inline=False)
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

    if not roles_faltantes:
        await interaction.followup.send(
            f"ℹ️ {usuario.mention} ya tiene todos los roles asignados.",
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

    if not roles_validos:
        await interaction.followup.send(
            "❌ No se puede asignar ningún rol por jerarquía. Revisá la posición del bot.",
            ephemeral=True,
        )
        return

    asignados, errores = await _asignar_roles(usuario, roles_validos)

    try:
        await _enviar_felicitaciones(canal, usuario)
    except Exception as e:
        logger.error("Error enviando felicitaciones: %s", e)
        errores.append(None)

    tipo_acceso = _obtener_tipo_acceso(admin)

    try:
        await _enviar_auditoria(usuario, admin, tipo_acceso, asignados, errores, roles_bloqueados, canal)
    except Exception as e:
        logger.error("Error enviando auditoría: %s", e)

    errores_reales = [r for r in errores if r is not None]
    tiene_error = bool(errores_reales) or bool(roles_bloqueados)

    if not tiene_error:
        await interaction.followup.send(
            f"✅ {usuario.mention} fue aprobado correctamente.\n"
            f"Roles asignados: {len(asignados)}",
            ephemeral=True,
        )
    else:
        roles_ok = len(asignados)
        partes = [f"⚠️ Aprobación completada con **{len(errores_reales) + len(roles_bloqueados)} error(es)**.\n✅ Asignados: {roles_ok}"]
        if roles_bloqueados:
            ids_bloq = ", ".join(f"<@&{r.id}>" for r in roles_bloqueados)
            partes.append(f"⛔ Bloqueados por jerarquía: {ids_bloq}")
        if errores_reales:
            ids_err = ", ".join(f"<@&{r.id}>" for r in errores_reales)
            partes.append(f"❌ Error al asignar: {ids_err}")
        await interaction.followup.send("\n".join(partes), ephemeral=True)


async def setup(bot):
    bot.tree.add_command(aprobar)
    logger.info("Comando /aprobar registrado")
