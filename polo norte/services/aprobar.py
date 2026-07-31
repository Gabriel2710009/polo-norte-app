import logging
import discord
from discord import app_commands
from services import log_actions
from utils import config_manager

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
        return "\U0001f6e1\ufe0f Administrador"
    if member.guild_permissions.manage_roles:
        return "\u2699\ufe0f Gestionar Roles"
    if any(role.id == ROL_AUTORIZADO_ID for role in member.roles):
        return f"\U0001f511 <@&{ROL_AUTORIZADO_ID}>"
    return "\u2753 Desconocido"


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
            await member.add_roles(role, reason="Aprobaci\u00f3n de postulaci\u00f3n")
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
            await member.remove_roles(role, reason="Aprobaci\u00f3n de postulaci\u00f3n - limpieza")
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
    estado = "\u2705 aprobado" if not tiene_error else "\u26a0\ufe0f aprobado con errores"
    color = discord.Color.green() if not tiene_error else discord.Color.orange()

    embed = discord.Embed(title="\U0001f4cb Aprobaci\u00f3n de postulaci\u00f3n", color=color, timestamp=discord.utils.utcnow())
    embed.add_field(name="\U0001f464 Usuario aprobado", value=f"{member.mention}\n`{member.id}`", inline=True)
    embed.add_field(name="\U0001f6e0 Administrador", value=f"{admin.mention}\n`{admin.id}`", inline=True)
    embed.add_field(name="\U0001f511 Acceso", value=tipo_acceso, inline=True)
    embed.add_field(name="\U0001f4c5 Fecha y hora", value=discord.utils.utcnow().strftime("%d/%m/%Y %H:%M:%S UTC"), inline=False)

    roles_asignados_str = "\n".join(f"<@&{r.id}>" for r in asignados) or "Ninguno"
    embed.add_field(name="\u2705 Roles asignados", value=roles_asignados_str, inline=False)

    if eliminados:
        roles_eliminados_str = "\n".join(f"<@&{r.id}>" for r in eliminados)
        embed.add_field(name="\U0001f5d1\ufe0f Roles eliminados", value=roles_eliminados_str, inline=False)

    if bloqueados:
        roles_bloqueados_str = "\n".join(f"<@&{r.id}>" for r in bloqueados)
        embed.add_field(name="\u26d4 Roles bloqueados (jerarqu\u00eda)", value=roles_bloqueados_str, inline=False)

    if errores_asignar:
        errores_asignar_str = "\n".join(f"<@&{r.id}>" for r in errores_asignar)
        embed.add_field(name="\u274c Error al asignar", value=errores_asignar_str, inline=False)

    if errores_eliminar:
        errores_eliminar_str = "\n".join(f"<@&{r.id}>" for r in errores_eliminar)
        embed.add_field(name="\u274c Error al eliminar", value=errores_eliminar_str, inline=False)

    embed.add_field(name="\U0001f4cd Canal", value=canal.mention, inline=True)
    embed.add_field(name="\U0001f4cc Estado", value=estado, inline=True)
    embed.set_footer(text=f"ID: {member.id}")

    log_channel = await log_actions._get_channel()
    if log_channel:
        await log_channel.send(embed=embed)


async def _enviar_felicitaciones(channel: discord.TextChannel, member: discord.Member):
    guild_id = channel.guild.id
    url_canal = f"https://discord.com/channels/{guild_id}/{CANAL_SOLICITUD_ID}"

    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="\U0001f512 Solicitar canal privado",
        style=discord.ButtonStyle.link,
        url=url_canal,
    ))

    await channel.send(
        content=f"\U0001f389 \u00a1Felicitaciones {member.mention}!\n\n"
                f"Tu postulaci\u00f3n fue aprobada y ya formas parte de **Polo Norte**.\n\n"
                f"El siguiente paso es solicitar tu **canal privado** para continuar con el proceso.",
        view=view,
    )


async def ejecutar_aprobacion(
    member: discord.Member,
    admin: discord.Member,
    channel: discord.TextChannel,
    origen: str = "comando",
) -> dict:
    guild = channel.guild

    if not guild.me.guild_permissions.manage_roles:
        logger.error("El bot no tiene permisos de gestionar roles en el servidor %s", guild.id)
        return {
            "exito": False, "asignados": 0, "eliminados": 0,
            "errores_asignar": [], "errores_eliminar": [], "bloqueados": [],
            "mensaje": "bot_sin_permisos",
        }

    roles_faltantes = _obtener_roles_faltantes(member)
    roles_a_eliminar = _obtener_roles_a_eliminar(member)

    if not roles_faltantes and not roles_a_eliminar:
        return {"exito": True, "asignados": 0, "eliminados": 0, "errores_asignar": [], "errores_eliminar": [], "bloqueados": [], "mensaje": "sin_cambios"}

    roles_validos, roles_bloqueados = _validar_jerarquia(guild, roles_faltantes)

    if not roles_validos and not roles_a_eliminar:
        return {"exito": False, "asignados": 0, "eliminados": 0, "errores_asignar": [], "errores_eliminar": [], "bloqueados": [str(r.id) for r in roles_bloqueados], "mensaje": "todos_bloqueados"}

    asignados, errores_asignar = await _asignar_roles(member, roles_validos)
    eliminados, errores_eliminar = await _eliminar_roles(member, roles_a_eliminar)

    try:
        await _enviar_felicitaciones(channel, member)
    except Exception as e:
        logger.error("Error enviando felicitaciones: %s", e)

    tipo_acceso = _obtener_tipo_acceso(admin)

    try:
        await _enviar_auditoria(
            member, admin, tipo_acceso,
            asignados, eliminados,
            errores_asignar, errores_eliminar,
            roles_bloqueados, channel,
        )
    except Exception as e:
        logger.error("Error enviando auditor\u00eda: %s", e)

    errores_reales_asignar = [r for r in errores_asignar if r is not None]
    errores_reales_eliminar = [r for r in errores_eliminar if r is not None]
    tiene_error = bool(errores_reales_asignar) or bool(errores_reales_eliminar) or bool(roles_bloqueados)

    return {
        "exito": not tiene_error,
        "asignados": len(asignados),
        "eliminados": len(eliminados),
        "errores_asignar": [str(r.id) for r in errores_reales_asignar],
        "errores_eliminar": [str(r.id) for r in errores_reales_eliminar],
        "bloqueados": [str(r.id) for r in roles_bloqueados] if roles_bloqueados else [],
        "mensaje": "con_cambios",
    }


@app_commands.command(name="aprobar", description="Aprueba la postulaci\u00f3n de un usuario")
@app_commands.describe(usuario="Usuario a aprobar")
async def aprobar(interaction: discord.Interaction, usuario: discord.Member):
    if not interaction.guild:
        await interaction.response.send_message("\u274c Este comando solo puede usarse en un servidor.", ephemeral=True)
        return

    admin = interaction.user
    canal = interaction.channel

    if not isinstance(admin, discord.Member):
        await interaction.response.send_message("\u274c No se pudo verificar tus permisos.", ephemeral=True)
        return

    if not _tiene_permiso(admin):
        await interaction.response.send_message(
            "\u274c No ten\u00e9s permisos suficientes para usar este comando.\n"
            "Necesit\u00e1s el permiso **Administrador**, **Gestionar Roles**, "
            f"o el rol <@&{ROL_AUTORIZADO_ID}>.",
            ephemeral=True,
        )
        return

    if not isinstance(canal, discord.TextChannel):
        await interaction.response.send_message("\u274c Este comando debe ejecutarse en un canal de texto.", ephemeral=True)
        return

    if usuario == admin:
        await interaction.response.send_message("\u274c No puedes aprobarte a ti mismo.", ephemeral=True)
        return

    if usuario.bot:
        await interaction.response.send_message("\u274c No puedes aprobar un bot.", ephemeral=True)
        return

    await interaction.response.send_message("\u23f3 Procesando resultado...", ephemeral=True)
    msg = await interaction.original_response()

    resultado = await ejecutar_aprobacion(usuario, admin, canal, origen="comando")

    mensaje = resultado.get("mensaje")
    if "mensaje" not in resultado:
        logger.warning("Resultado de ejecutar_aprobacion sin 'mensaje'. Keys recibidas: %s", list(resultado.keys()))

    if mensaje == "sin_cambios":
        await msg.edit(content=f"\u2139\ufe0f {usuario.mention} ya tiene todos los roles asignados y no posee roles a eliminar.")
        return

    if mensaje == "todos_bloqueados":
        await msg.edit(content="\u274c No se puede asignar ning\u00fan rol por jerarqu\u00eda. Revis\u00e1 la posici\u00f3n del bot.")
        return

    partes = []

    if resultado["bloqueados"]:
        bloqueados_str = "\n".join(f"\u2022 <@&{bid}>" for bid in resultado["bloqueados"])
        partes.append(f"\u26a0\ufe0f Roles bloqueados por jerarqu\u00eda:\n{bloqueados_str}")

    if resultado["exito"]:
        exito_partes = [f"\u2705 {usuario.mention} fue aprobado correctamente."]
        if resultado["asignados"]:
            exito_partes.append(f"Roles asignados: {resultado['asignados']}")
        if resultado["eliminados"]:
            exito_partes.append(f"Roles eliminados: {resultado['eliminados']}")
        partes.append("\n".join(exito_partes))
    else:
        errores_partes = [f"\u26a0\ufe0f Aprobaci\u00f3n completada con errores."]
        errores_partes.append(f"\u2705 Asignados: {resultado['asignados']}")
        if resultado["eliminados"]:
            errores_partes[0] += f" | \U0001f5d1\ufe0f Eliminados: {resultado['eliminados']}"
        if resultado["bloqueados"]:
            ids_bloq = ", ".join(f"<@&{bid}>" for bid in resultado["bloqueados"])
            errores_partes.append(f"\u26d4 Bloqueados por jerarqu\u00eda: {ids_bloq}")
        if resultado["errores_asignar"]:
            ids_err = ", ".join(f"<@&{eid}>" for eid in resultado["errores_asignar"])
            errores_partes.append(f"\u274c Error al asignar: {ids_err}")
        if resultado["errores_eliminar"]:
            ids_err = ", ".join(f"<@&{eid}>" for eid in resultado["errores_eliminar"])
            errores_partes.append(f"\u274c Error al eliminar: {ids_err}")
        partes.append("\n".join(errores_partes))

    await msg.edit(content="\n".join(partes))


async def setup(bot):
    bot.tree.add_command(aprobar)
    logger.info("Comando /aprobar registrado")
