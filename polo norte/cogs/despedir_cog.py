import os
import re
import logging
from dataclasses import dataclass

import discord
from discord import app_commands

from utils import config_manager
from services import log_actions

logger = logging.getLogger("DespedirCog")

# Tope de canales "débiles" (solo por permisos) para no inundar la confirmación.
MAX_CANDIDATOS_DEBILES = 5

_MENCION_CANAL = re.compile(r"^<#(\d{17,20})>$")
_ID_CANAL = re.compile(r"^(\d{17,20})$")


def _tiene_permiso(member: discord.Member, config: dict) -> bool:
    if member.guild_permissions.administrator or member.guild_permissions.manage_roles:
        return True
    autorizado = config.get("rol_autorizado")
    return bool(autorizado) and any(rol.id == autorizado for rol in member.roles)


def _formatear(plantilla: str, **valores) -> str:
    try:
        return plantilla.format(**valores)
    except (KeyError, IndexError, ValueError):
        logger.warning("Plantilla con placeholders desconocidos: %s", plantilla)
        return plantilla


def _resolver_canal(guild: discord.Guild, texto: str):
    texto = texto.strip()
    match = _MENCION_CANAL.match(texto) or _ID_CANAL.match(texto)
    if not match:
        return None, "Usá una mención de canal (`#canal`) o un ID numérico."
    canal = guild.get_channel(int(match.group(1)))
    if canal is None:
        return None, "No encontré ese canal en el servidor."
    return canal, None


def _es_dueno_por_overwrites(canal: discord.TextChannel, member: discord.Member) -> bool:
    for objetivo, permisos in canal.overwrites.items():
        if not isinstance(objetivo, discord.Member) or objetivo.id != member.id:
            continue
        if permisos.read_messages and permisos.send_messages:
            return True
    return False


async def _participa_en_primeros_mensajes(canal, member, limite: int) -> bool:
    try:
        async for mensaje in canal.history(limit=limite, oldest_first=True):
            if mensaje.author.id == member.id:
                return True
    except (discord.Forbidden, discord.HTTPException):
        logger.warning("Sin historial en el canal %s para detectar al usuario", canal.id)
    return False


async def _creador_por_audit_log(guild, canal, member) -> bool:
    try:
        async for entrada in guild.audit_logs(
            action=discord.AuditLogAction.channel_create, limit=20
        ):
            if entrada.target.id == canal.id and entrada.user.id == member.id:
                return True
    except Exception:
        logger.debug("No se pudo consultar el audit log para el canal %s", canal.id)
    return False


async def _detectar_candidatos(bot, guild, member, config: dict):
    """Devuelve [(canal, razon)] con los dueños/participantes detectados.

    Primero van los candidatos fuertes (overwrites, primeros mensajes o audit
    log); si no hay ninguno, se ofrecen canales a los que el usuario tiene
    acceso como fallback.
    """
    fuertes = []
    debiles = []
    limite = int(config.get("limite_primeros_mensajes", 10) or 10)
    usar_audit = bool(config.get("usar_audit_log", True))

    for categoria_id in config.get("categorias_busqueda", []):
        categoria = guild.get_channel(int(categoria_id))
        if not isinstance(categoria, discord.CategoryChannel):
            logger.warning("Categoría %s no encontrada o no es categoría", categoria_id)
            continue
        for canal in categoria.channels:
            if not isinstance(canal, discord.TextChannel):
                continue

            razon = None
            if _es_dueno_por_overwrites(canal, member):
                razon = "dueño del canal (permisos)"
            elif await _participa_en_primeros_mensajes(canal, member, limite):
                razon = "autor en los primeros mensajes"
            elif usar_audit and await _creador_por_audit_log(guild, canal, member):
                razon = "creador del canal (audit log)"

            if razon:
                fuertes.append((canal, razon))
                continue

            try:
                permisos = canal.permissions_for(member)
            except Exception:
                continue
            if permisos.read_messages and permisos.send_messages:
                debiles.append((canal, "tiene acceso al canal"))

    return fuertes + debiles[:MAX_CANDIDATOS_DEBILES]


async def _mover_canal(canal, categoria_id, motivo: str):
    if not categoria_id:
        return False, "categoría destino no configurada"
    categoria = canal.guild.get_channel(int(categoria_id))
    if not isinstance(categoria, discord.CategoryChannel):
        return False, f"categoría destino {categoria_id} no encontrada"
    if canal.category_id == categoria.id:
        return True, None
    try:
        await canal.edit(category=categoria, reason=f"Expulsión: {motivo}")
        return True, None
    except discord.Forbidden:
        return False, "el bot no tiene permiso para mover canales"
    except discord.HTTPException as exc:
        return False, f"error de Discord al mover el canal: {exc}"


async def _reemplazar_roles(member, config: dict, motivo: str) -> dict:
    guild = member.guild
    resultado = {
        "ok": False,
        "eliminados": [],
        "asignados": [],
        "inamovibles": [],
        "errores": [],
    }
    if not guild.me.guild_permissions.manage_roles:
        resultado["errores"].append("el bot no tiene permiso Gestionar Roles")
        return resultado

    despedido = guild.get_role(int(config.get("rol_despedido", 0) or 0))
    verificado = guild.get_role(int(config.get("rol_verificado", 0) or 0))
    if not despedido or not verificado:
        resultado["errores"].append("los roles despedido/verificado no existen en el servidor")
        return resultado

    objetivo = [despedido, verificado]
    anteriores = list(member.roles)
    inamovibles = []
    for rol in anteriores:
        if rol.is_default():
            continue
        if rol >= guild.me.top_role and rol not in objetivo:
            inamovibles.append(rol)

    finales = objetivo + inamovibles
    for rol in objetivo:
        if rol >= guild.me.top_role:
            resultado["errores"].append(f"rol {rol.name} está por encima del bot y no se puede asignar")

    try:
        await member.edit(roles=finales, reason=f"Expulsión: {motivo}")
        resultado["ok"] = True
        resultado["eliminados"] = [
            rol for rol in anteriores if rol not in finales and not rol.is_default()
        ]
        resultado["asignados"] = [
            rol for rol in objetivo if rol not in anteriores
        ]
        resultado["inamovibles"] = inamovibles
    except discord.Forbidden:
        resultado["errores"].append("el bot no tiene jerarquía para modificar los roles de este usuario")
    except discord.HTTPException as exc:
        resultado["errores"].append(f"error de Discord al modificar roles: {exc}")

    return resultado


async def _enviar_auditoria(bot, config: dict, embed: discord.Embed):
    canal_id = int(config.get("canal_logs", 0) or 0) or int(os.getenv("LOG_CHANNEL_ID", 0) or 0)
    canal = bot.get_channel(canal_id) if canal_id else None
    if canal is None:
        logger.warning("Canal de logs %s no encontrado para auditoría de expulsión", canal_id)
        return
    try:
        await canal.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        logger.exception("No se pudo enviar la auditoría de expulsión al canal %s", canal_id)


@dataclass
class EstadoExpulsion:
    staff_id: int
    usuario: discord.Member
    motivo: str
    config: dict
    candidatos: list
    indice: int = 0
    procesado: bool = False


class CanalManualModal(discord.ui.Modal, title="Canal del usuario"):
    canal_input = discord.ui.TextInput(
        label="Mención (#canal) o ID del canal",
        placeholder="123456789012345678",
        required=True,
        max_length=40,
    )

    def __init__(self, estado: EstadoExpulsion, vista: discord.ui.View):
        super().__init__()
        self.estado = estado
        self.vista = vista

    async def on_submit(self, interaction: discord.Interaction):
        canal, error = _resolver_canal(interaction.guild, self.canal_input.value)
        if error:
            await interaction.response.send_message(f"\u274c {error}", ephemeral=True)
            return
        await _procesar_confirmado(interaction, self.estado, canal, self.vista)


class CanalManualView(discord.ui.View):
    def __init__(self, estado: EstadoExpulsion):
        super().__init__(timeout=int(estado.config.get("timeout_confirmacion", 120) or 120))
        self.estado = estado

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.estado.staff_id:
            await interaction.response.send_message(
                "\u274c Solo quien ejecutó el comando puede confirmar.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="\u270d\ufe0f Escribir el canal", style=discord.ButtonStyle.primary)
    async def abrir(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CanalManualModal(self.estado, self))


class ConfirmarView(discord.ui.View):
    def __init__(self, estado: EstadoExpulsion, bot):
        super().__init__(timeout=int(estado.config.get("timeout_confirmacion", 120) or 120))
        self.estado = estado
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.estado.staff_id:
            await interaction.response.send_message(
                "\u274c Solo quien ejecutó el comando puede confirmar.", ephemeral=True
            )
            return False
        return True

    def embed_candidato(self) -> discord.Embed:
        canal, razon = self.estado.candidatos[self.estado.indice]
        embed = discord.Embed(
            title="\u2753 Confirmar canal del usuario",
            description=(
                f"**Usuario:** {self.estado.usuario.mention}\n"
                f"**Canal detectado:** {canal.mention}\n"
                f"**Motivo detectado:** {razon}\n\n"
                "¿Es este el canal que hay que mover a *Trabajadores fuera*?"
            ),
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(
            text=f"Candidato {self.estado.indice + 1}/{len(self.estado.candidatos)}"
        )
        return embed

    @discord.ui.button(label="\u2705 Es este", style=discord.ButtonStyle.success)
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        canal, _ = self.estado.candidatos[self.estado.indice]
        await _procesar_confirmado(interaction, self.estado, canal, self)

    @discord.ui.button(label="\u274c No, escribirlo", style=discord.ButtonStyle.secondary)
    async def escribir(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CanalManualModal(self.estado, self))

    @discord.ui.button(label="\u23ed\ufe0f Saltar", style=discord.ButtonStyle.secondary)
    async def saltar(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.estado.indice += 1
        if self.estado.indice >= len(self.estado.candidatos):
            vista = CanalManualView(self.estado)
            await interaction.response.edit_message(
                content=(
                    f"No quedan más candidatos para {self.estado.usuario.mention}. "
                    "Escribí el canal correspondiente:"
                ),
                embed=None,
                view=vista,
            )
            return
        await interaction.response.edit_message(embed=self.embed_candidato(), view=self)


async def _procesar_confirmado(interaction, estado: EstadoExpulsion, canal, vista):
    if estado.procesado:
        await interaction.response.send_message(
            "\u26a0\ufe0f Este canal ya fue procesado.", ephemeral=True
        )
        return
    estado.procesado = True
    for item in getattr(vista, "children", []):
        item.disabled = True

    await interaction.response.defer(ephemeral=True)
    try:
        await interaction.edit_original_response(
            content="\u23f3 Procesando expulsión...", embed=None, view=None
        )
    except Exception:
        logger.debug("No se pudo actualizar el mensaje de progreso")

    config = estado.config
    usuario = estado.usuario
    motivo = estado.motivo
    bot = interaction.client
    errores = []

    movio, error_mover = await _mover_canal(
        canal, config.get("categoria_destino"), motivo
    )
    if error_mover:
        errores.append(f"Canal: {error_mover}")

    roles = await _reemplazar_roles(usuario, config, motivo)
    errores.extend(roles["errores"])

    canal_aviso = bot.get_channel(int(config.get("canal_despedido", 0) or 0))
    aviso_enviado = False
    if canal_aviso is None:
        errores.append("canal despedido no encontrado")
    else:
        texto = _formatear(
            config.get("plantilla_aviso", "{usuario} Despedido por {motivo}"),
            usuario=usuario.mention,
            motivo=motivo,
            dias=config.get("dias", 3),
            canal=canal.mention,
        )
        try:
            await canal_aviso.send(
                texto,
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False
                ),
            )
            aviso_enviado = True
        except (discord.Forbidden, discord.HTTPException):
            errores.append("no se pudo enviar el aviso al canal despedido")

    embed = discord.Embed(
        title="\u2705 Expulsión procesada" if not errores else "\u26a0\ufe0f Expulsión con incidencias",
        color=discord.Color.green() if not errores else discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Usuario", value=f"{usuario.mention}\n`{usuario.id}`", inline=True)
    embed.add_field(
        name="Staff", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=True
    )
    embed.add_field(name="Motivo", value=motivo, inline=False)
    embed.add_field(name="Canal", value=canal.mention, inline=True)
    embed.add_field(name="Movido", value="Sí" if movio else "No", inline=True)
    embed.add_field(name="Aviso enviado", value="Sí" if aviso_enviado else "No", inline=True)

    def nombres(lista):
        return "\n".join(rol.mention for rol in lista) or "Ninguno"

    embed.add_field(name="Roles eliminados", value=nombres(roles["eliminados"]), inline=False)
    embed.add_field(name="Roles asignados", value=nombres(roles["asignados"]), inline=True)
    if roles["inamovibles"]:
        embed.add_field(
            name="\u26d4 Roles no removibles (jerarquía)",
            value=nombres(roles["inamovibles"]),
            inline=False,
        )
    if errores:
        embed.add_field(name="Incidencias", value="\n".join(f"\u2022 {e}" for e in errores), inline=False)
    embed.set_footer(text=f"ID: {usuario.id}")

    try:
        await _enviar_auditoria(bot, config, embed)
    except Exception:
        logger.exception("Error enviando auditoría de expulsión")

    partes = [
        f"\u2705 {usuario.mention} expulsado: canal {canal.mention} movido, "
        "roles reemplazados y aviso enviado."
    ]
    if errores:
        partes.append("\u26a0\ufe0f Incidencias:\n" + "\n".join(f"\u2022 {e}" for e in errores))
    await interaction.edit_original_response(content="\n".join(partes), embed=None, view=None)

    log_actions.log_info(
        "\u2705 Expulsión procesada",
        f"**Usuario:** {usuario} (`{usuario.id}`)\n"
        f"**Canal:** {canal.mention}\n"
        f"**Motivo:** {motivo}\n"
        f"**Staff:** {interaction.user} (`{interaction.user.id}`)",
    )


@app_commands.command(name="expulsar", description="Expulsa a un usuario: mueve su canal, quita roles y avisa")
@app_commands.describe(
    usuario="Usuario a expulsar",
    motivo="Motivo de la expulsión (opcional)",
)
async def expulsar(
    interaction: discord.Interaction,
    usuario: discord.Member,
    motivo: str = "Sin motivo registrado",
):
    if not interaction.guild:
        await interaction.response.send_message(
            "\u274c Solo puede usarse en un servidor.", ephemeral=True
        )
        return
    config = config_manager.load_despedir_config()
    if not isinstance(interaction.user, discord.Member) or not _tiene_permiso(interaction.user, config):
        await interaction.response.send_message(
            "\u274c No tenés permisos para usar este comando.", ephemeral=True
        )
        return
    if usuario.bot:
        await interaction.response.send_message("\u274c No se puede expulsar a un bot.", ephemeral=True)
        return
    if usuario.id == interaction.user.id:
        await interaction.response.send_message("\u274c No podés expulsarte a vos mismo.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    candidatos = await _detectar_candidatos(
        interaction.client, interaction.guild, usuario, config
    )
    estado = EstadoExpulsion(
        staff_id=interaction.user.id,
        usuario=usuario,
        motivo=motivo,
        config=config,
        candidatos=candidatos,
    )

    if not candidatos:
        vista = CanalManualView(estado)
        await interaction.edit_original_response(
            content=(
                f"No encontré canales candidatos para {usuario.mention} en las categorías "
                "configuradas. Indicá el canal correcto:"
            ),
            view=vista,
        )
        return

    vista = ConfirmarView(estado, interaction.client)
    await interaction.edit_original_response(embed=vista.embed_candidato(), view=vista)


@app_commands.command(name="despedir", description="Envía el aviso de despido en el canal donde se ejecuta")
@app_commands.describe(
    usuario="Usuario despedido",
    motivo="Motivo del despido (opcional)",
)
async def despedir(
    interaction: discord.Interaction,
    usuario: discord.Member,
    motivo: str = "Sin motivo registrado",
):
    if not interaction.guild:
        await interaction.response.send_message(
            "\u274c Solo puede usarse en un servidor.", ephemeral=True
        )
        return
    config = config_manager.load_despedir_config()
    if not isinstance(interaction.user, discord.Member) or not _tiene_permiso(interaction.user, config):
        await interaction.response.send_message(
            "\u274c No tenés permisos para usar este comando.", ephemeral=True
        )
        return

    canal = interaction.channel
    if not isinstance(canal, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message(
            "\u274c Este comando debe ejecutarse en un canal de texto.", ephemeral=True
        )
        return
    if not canal.permissions_for(interaction.guild.me).send_messages:
        await interaction.response.send_message(
            "\u274c El bot no puede escribir en este canal.", ephemeral=True
        )
        return

    texto = _formatear(
        config.get("plantilla_despedir", ""),
        usuario=usuario.mention,
        motivo=motivo,
        dias=config.get("dias", 3),
        canal=canal.mention,
    )
    await interaction.response.defer(ephemeral=True)
    try:
        await canal.send(
            texto,
            allowed_mentions=discord.AllowedMentions(
                users=True, roles=False, everyone=False
            ),
        )
    except (discord.Forbidden, discord.HTTPException):
        await interaction.edit_original_response(
            content="\u274c No se pudo enviar el mensaje en este canal."
        )
        return

    embed = discord.Embed(
        title="\U0001f4e4 Aviso de despido enviado",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Usuario", value=f"{usuario.mention}\n`{usuario.id}`", inline=True)
    embed.add_field(
        name="Staff", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=True
    )
    embed.add_field(name="Canal", value=canal.mention, inline=True)
    embed.add_field(name="Motivo", value=motivo, inline=False)
    embed.add_field(name="Mensaje", value=f"```{texto[:1000]}```", inline=False)
    try:
        await _enviar_auditoria(interaction.client, config, embed)
    except Exception:
        logger.exception("Error enviando auditoría de despedir")

    await interaction.edit_original_response(
        content=f"\u2705 Aviso de despido enviado en {canal.mention}."
    )
    log_actions.log_info(
        "\U0001f4e4 Aviso de despido",
        f"**Usuario:** {usuario} (`{usuario.id}`)\n"
        f"**Canal:** {canal.mention}\n"
        f"**Motivo:** {motivo}\n"
        f"**Staff:** {interaction.user} (`{interaction.user.id}`)",
    )


async def setup(bot):
    bot.tree.add_command(expulsar)
    bot.tree.add_command(despedir)
    logger.info("Comandos /expulsar y /despedir registrados")
