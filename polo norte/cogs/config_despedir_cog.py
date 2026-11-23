import logging

import discord
from discord import app_commands

from utils import config_manager
from services import log_actions

logger = logging.getLogger("ConfigDespedirCog")


class EditButton(discord.ui.Button):
    """Botón genérico que delega en un handler del panel principal."""

    def __init__(self, label: str, row: int, handler, style=None, emoji=None):
        super().__init__(
            label=label,
            row=row,
            style=style or discord.ButtonStyle.primary,
            emoji=emoji,
        )
        self._handler = handler

    async def callback(self, interaction: discord.Interaction):
        await self._handler(interaction)


class ConfigDespedirView(discord.ui.View):
    """Panel de edición de la config compartida por /despedir y /expulsar."""

    def __init__(self, config: dict, panel: str, channel_id: int, message_id: int, client):
        super().__init__(timeout=300)
        self.config = config
        self.panel = panel
        self.channel_id = channel_id
        self.message_id = message_id
        self.client = client
        self._armar_botones()

    # ── Render ────────────────────────────────────────────────────────────

    def _un_rol(self, key: str) -> str:
        rid = self.config.get(key)
        return f"<@&{rid}>" if rid else "Ninguno"

    def _un_canal(self, key: str) -> str:
        cid = self.config.get(key)
        if not cid:
            return "Ninguno (usa LOG_CHANNEL_ID del .env)" if key == "canal_logs" else "Ninguno"
        return f"<#{cid}>"

    def _muchos_canales(self, key: str) -> str:
        ids = self.config.get(key) or []
        return "\n".join(f"<#{cid}>" for cid in ids) or "Ninguno"

    def build_embed(self) -> discord.Embed:
        titulo = "Configuración /expulsar" if self.panel == "expulsar" else "Configuración /despedir"
        embed = discord.Embed(
            title=titulo,
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )

        if self.panel == "expulsar":
            embed.add_field(name="Rol verificado", value=self._un_rol("rol_verificado"), inline=True)
            embed.add_field(name="Rol despedido", value=self._un_rol("rol_despedido"), inline=True)
            embed.add_field(name="Rol autorizado", value=self._un_rol("rol_autorizado"), inline=True)
            embed.add_field(
                name="Categorías de búsqueda",
                value=self._muchos_canales("categorias_busqueda"),
                inline=False,
            )
            embed.add_field(name="Categoría destino", value=self._un_canal("categoria_destino"), inline=True)
            embed.add_field(name="Canal despedido", value=self._un_canal("canal_despedido"), inline=True)
            embed.add_field(name="Canal de logs", value=self._un_canal("canal_logs"), inline=True)
            embed.add_field(
                name="Plantilla del aviso",
                value=f"```{self.config.get('plantilla_aviso', '')}```",
                inline=False,
            )
            embed.add_field(
                name="Primeros mensajes a revisar",
                value=str(self.config.get("limite_primeros_mensajes", 10)),
                inline=True,
            )
            embed.add_field(
                name="Usar audit log",
                value="Sí" if self.config.get("usar_audit_log") else "No",
                inline=True,
            )
            embed.add_field(
                name="Timeout de confirmación",
                value=f"{self.config.get('timeout_confirmacion', 120)}s",
                inline=True,
            )
        else:
            embed.add_field(name="Días del plazo", value=str(self.config.get("dias", 3)), inline=True)
            embed.add_field(name="Canal de logs", value=self._un_canal("canal_logs"), inline=True)
            embed.add_field(
                name="Plantilla de /despedir",
                value=f"```{self.config.get('plantilla_despedir', '')}```",
                inline=False,
            )

        embed.set_footer(text="Los cambios no se guardan hasta presionar Guardar")
        return embed

    async def editar_principal(self):
        channel = self.client.get_channel(self.channel_id)
        if channel is None:
            logger.warning("Canal %s no encontrado para el panel de despedir", self.channel_id)
            return
        try:
            mensaje = await channel.fetch_message(self.message_id)
            await mensaje.edit(embed=self.build_embed(), view=self)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.exception("Error editando el panel de despedir")

    async def refrescar(self, interaction: discord.Interaction):
        try:
            await self.editar_principal()
        except Exception:
            logger.exception("Error refrescando el panel de despedir")
        try:
            await interaction.response.send_message(
                "Configuración actualizada.", ephemeral=True, delete_after=2
            )
        except discord.InteractionResponded:
            await interaction.followup.send(
                "Configuración actualizada.", ephemeral=True, delete_after=2
            )

    # ── Botones del panel ─────────────────────────────────────────────────

    def _armar_botones(self):
        if self.panel == "expulsar":
            self.add_item(EditButton("Rol verificado", 0, self._edit_rol_verificado))
            self.add_item(EditButton("Rol despedido", 0, self._edit_rol_despedido))
            self.add_item(EditButton("Rol autorizado", 0, self._edit_rol_autorizado))
            self.add_item(EditButton("Categorías búsqueda", 1, self._edit_categorias_busqueda))
            self.add_item(EditButton("Categoría destino", 1, self._edit_categoria_destino))
            self.add_item(EditButton("Canal despedido", 2, self._edit_canal_despedido))
            self.add_item(EditButton("Canal de logs", 2, self._edit_canal_logs))
            self.add_item(EditButton("Plantilla aviso", 3, self._edit_plantilla_aviso))
            self.add_item(EditButton("Límite mensajes", 3, self._edit_limite_mensajes))
            self.add_item(EditButton("Audit log", 3, self._toggle_audit_log))
            self.add_item(EditButton("Timeout confirmación", 3, self._edit_timeout))
        else:
            self.add_item(EditButton("Plantilla /despedir", 0, self._edit_plantilla_despedir))
            self.add_item(EditButton("Días del plazo", 0, self._edit_dias))
            self.add_item(EditButton("Canal de logs", 0, self._edit_canal_logs))

        self.add_item(EditButton("Guardar", 4, self._guardar, discord.ButtonStyle.success, "\U0001f4be"))
        self.add_item(EditButton("Cancelar", 4, self._cancelar, discord.ButtonStyle.secondary))

    async def _abrir_roles(self, interaction, key, titulo, single=True):
        view = RoleSelectView(self, key, single=single)
        await interaction.response.send_message(titulo, view=view, ephemeral=True)

    async def _abrir_canales(self, interaction, key, titulo, channel_types, single=True):
        view = ChannelSelectView(self, key, channel_types, single=single)
        await interaction.response.send_message(titulo, view=view, ephemeral=True)

    async def _edit_rol_verificado(self, interaction):
        await self._abrir_roles(interaction, "rol_verificado", "Rol **verificado**:")

    async def _edit_rol_despedido(self, interaction):
        await self._abrir_roles(interaction, "rol_despedido", "Rol **despedido**:")

    async def _edit_rol_autorizado(self, interaction):
        await self._abrir_roles(interaction, "rol_autorizado", "Rol **autorizado** para usar los comandos:")

    async def _edit_categorias_busqueda(self, interaction):
        await self._abrir_canales(
            interaction,
            "categorias_busqueda",
            "Categorías donde se buscará al usuario:",
            [discord.ChannelType.category],
            single=False,
        )

    async def _edit_categoria_destino(self, interaction):
        await self._abrir_canales(
            interaction, "categoria_destino", "Categoría de destino del canal:",
            [discord.ChannelType.category],
        )

    async def _edit_canal_despedido(self, interaction):
        await self._abrir_canales(
            interaction, "canal_despedido", "Canal donde se publica el aviso:",
            [discord.ChannelType.text],
        )

    async def _edit_canal_logs(self, interaction):
        await self._abrir_canales(
            interaction, "canal_logs", "Canal de auditoría (0 = usar el del .env):",
            [discord.ChannelType.text],
        )

    async def _edit_plantilla_aviso(self, interaction):
        modal = ValueModal(
            self, "plantilla_aviso", "Plantilla del aviso", "Texto del aviso",
            self.config.get("plantilla_aviso", ""),
        )
        await interaction.response.send_modal(modal)

    async def _edit_plantilla_despedir(self, interaction):
        modal = ValueModal(
            self, "plantilla_despedir", "Plantilla de /despedir", "Texto del mensaje",
            self.config.get("plantilla_despedir", ""),
        )
        await interaction.response.send_modal(modal)

    async def _edit_limite_mensajes(self, interaction):
        modal = ValueModal(
            self, "limite_primeros_mensajes", "Primeros mensajes", "Cantidad (entero > 0)",
            self.config.get("limite_primeros_mensajes", 10), integer=True,
        )
        await interaction.response.send_modal(modal)

    async def _edit_dias(self, interaction):
        modal = ValueModal(
            self, "dias", "Días del plazo", "Cantidad (entero > 0)",
            self.config.get("dias", 3), integer=True,
        )
        await interaction.response.send_modal(modal)

    async def _edit_timeout(self, interaction):
        modal = ValueModal(
            self, "timeout_confirmacion", "Timeout de confirmación", "Segundos (entero > 0)",
            self.config.get("timeout_confirmacion", 120), integer=True,
        )
        await interaction.response.send_modal(modal)

    async def _toggle_audit_log(self, interaction):
        self.config["usar_audit_log"] = not self.config.get("usar_audit_log", True)
        await self.refrescar(interaction)

    async def _guardar(self, interaction):
        config_manager.save_despedir_config(
            self.config, actualizado_por=str(interaction.user.id)
        )
        embed = discord.Embed(
            title="\U0001f4be Configuración guardada",
            description="Los cambios ya están activos para `/despedir` y `/expulsar`.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        await interaction.response.edit_message(embed=embed, view=None)
        logger.info("Config despedir guardada por %s", interaction.user.id)
        log_actions.log_info(
            "\U0001f4be Config despedir guardada",
            f"Panel /{self.panel} · por {interaction.user.mention} (`{interaction.user.id}`)",
        )

    async def _cancelar(self, interaction):
        await interaction.response.edit_message(
            content="Configuración descartada.", embed=None, view=None, delete_after=5
        )


class RoleSelectView(discord.ui.View):
    def __init__(self, parent: ConfigDespedirView, key: str, single: bool = True):
        super().__init__(timeout=120)
        self.parent = parent
        self.key = key
        self.single = single
        actual = parent.config.get(key)
        if isinstance(actual, list):
            defaults = [discord.Object(id=rid) for rid in actual]
        elif actual:
            defaults = [discord.Object(id=actual)]
        else:
            defaults = []
        self.select = discord.ui.RoleSelect(
            placeholder="Selecciona los roles...",
            min_values=0,
            max_values=1 if single else 25,
            default_values=defaults,
        )
        self.add_item(self.select)

    @discord.ui.button(label="Confirmar", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        ids = [rol.id for rol in self.select.values]
        if self.single:
            self.parent.config[self.key] = ids[0] if ids else 0
        else:
            self.parent.config[self.key] = ids
        await self.parent.refrescar(interaction)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Cancelado.", ephemeral=True, delete_after=2)


class ChannelSelectView(discord.ui.View):
    def __init__(self, parent, key, channel_types, single=True):
        super().__init__(timeout=120)
        self.parent = parent
        self.key = key
        self.single = single
        actual = parent.config.get(key)
        if isinstance(actual, list):
            defaults = [discord.Object(id=cid) for cid in actual]
        elif actual:
            defaults = [discord.Object(id=actual)]
        else:
            defaults = []
        self.select = discord.ui.ChannelSelect(
            placeholder="Selecciona los canales...",
            min_values=0,
            max_values=1 if single else 25,
            channel_types=channel_types,
            default_values=defaults,
        )
        self.add_item(self.select)

    @discord.ui.button(label="Confirmar", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        ids = [canal.id for canal in self.select.values]
        if self.single:
            self.parent.config[self.key] = ids[0] if ids else 0
        else:
            self.parent.config[self.key] = ids
        await self.parent.refrescar(interaction)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Cancelado.", ephemeral=True, delete_after=2)


class ValueModal(discord.ui.Modal):
    def __init__(self, parent, key, titulo, label, current, integer=False):
        super().__init__(title=titulo)
        self.parent = parent
        self.key = key
        self.integer = integer
        self.campo = discord.ui.TextInput(
            label=label,
            default=str(current),
            required=True,
            max_length=1500,
        )
        self.add_item(self.campo)

    async def on_submit(self, interaction: discord.Interaction):
        valor = self.campo.value.strip()
        if self.integer:
            try:
                numero = int(valor)
                if numero < 1:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message(
                    "\u274c Ingresá un número entero mayor a 0.", ephemeral=True
                )
                return
            valor = numero
        self.parent.config[self.key] = valor
        await self.parent.refrescar(interaction)


def _tiene_permiso(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator or member.guild_permissions.manage_roles:
        return True
    autorizado = config_manager.load_despedir_config().get("rol_autorizado")
    return bool(autorizado) and any(rol.id == autorizado for rol in member.roles)


async def _abrir_panel(interaction: discord.Interaction, panel: str):
    if not interaction.guild:
        await interaction.response.send_message(
            "\u274c Este comando solo puede usarse en un servidor.", ephemeral=True
        )
        return
    if not _tiene_permiso(interaction):
        await interaction.response.send_message(
            "\u274c No tenés permisos para configurar estos comandos.", ephemeral=True
        )
        return

    config = config_manager.load_despedir_config()
    view = ConfigDespedirView(
        config, panel, interaction.channel_id, 0, interaction.client
    )
    await interaction.response.send_message(embed=view.build_embed(), view=view)
    try:
        mensaje = await interaction.original_response()
    except Exception:
        logger.exception("No se pudo obtener el mensaje del panel de despedir")
        return
    view.message_id = mensaje.id
    logger.info("Panel /config_%s abierto por %s", panel, interaction.user.id)


@app_commands.command(name="config_expulsar", description="Configura los roles, canales y textos de /expulsar")
@app_commands.default_permissions(administrator=True)
async def config_expulsar(interaction: discord.Interaction):
    await _abrir_panel(interaction, "expulsar")


@app_commands.command(name="config_despedir", description="Configura el texto y el plazo de /despedir")
@app_commands.default_permissions(administrator=True)
async def config_despedir(interaction: discord.Interaction):
    await _abrir_panel(interaction, "despedir")


async def setup(bot):
    bot.tree.add_command(config_expulsar)
    bot.tree.add_command(config_despedir)
    logger.info("Comandos /config_expulsar y /config_despedir registrados")
