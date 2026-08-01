import logging
import discord
from discord import app_commands
from utils import config_manager
from services import log_actions

logger = logging.getLogger("ConfigAprobar")


class RoleSelectView(discord.ui.View):
    def __init__(self, parent_view, config_key, current_ids):
        super().__init__(timeout=120)
        self.parent = parent_view
        self.config_key = config_key
        default_roles = [discord.Object(id=rid) for rid in current_ids]
        self.role_select = discord.ui.RoleSelect(
            placeholder="Selecciona los roles...",
            min_values=0,
            max_values=25,
            default_values=default_roles,
        )
        self.add_item(self.role_select)

    @discord.ui.button(label="Confirmar", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_ids = [role.id for role in self.role_select.values]
        self.parent.config[self.config_key] = role_ids

        embed = self.parent.build_embed()
        try:
            await self.parent.editar_principal(embed)
        except Exception:
            logger.exception("Error editando el panel de aprobacion")

        await interaction.response.send_message(
            content="Roles actualizados.",
            ephemeral=True,
            delete_after=2,
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            content="Cancelado.", ephemeral=True, delete_after=2,
        )


class AprobarConfigView(discord.ui.View):
    def __init__(self, config, channel_id: int, message_id: int, client: discord.Client):
        super().__init__(timeout=300)
        self.config = config
        self.channel_id = channel_id
        self.message_id = message_id
        self.client = client

    def build_embed(self):
        assign_roles = "\n".join(f"<@&{rid}>" for rid in self.config.get("roles_asignar", [])) or "Ninguno"
        remove_roles = "\n".join(f"<@&{rid}>" for rid in self.config.get("roles_eliminar", [])) or "Ninguno"
        embed = discord.Embed(
            title="Configuracion de aprobacion",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Roles otorgados", value=assign_roles, inline=False)
        embed.add_field(name="Roles eliminados", value=remove_roles, inline=False)
        embed.set_footer(text="Los cambios no se guardan hasta presionar Guardar")
        return embed

    async def editar_principal(self, embed: discord.Embed):
        """Edita el mensaje del panel principal usando fetch_message."""
        channel = self.client.get_channel(self.channel_id)
        if channel is None:
            logger.warning("Canal %s no encontrado para panel de aprobacion", self.channel_id)
            return
        try:
            msg = await channel.fetch_message(self.message_id)
            await msg.edit(embed=embed, view=self)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.exception("Error editando panel de aprobacion")

    @discord.ui.button(label="Editar roles otorgados", style=discord.ButtonStyle.primary, emoji="\u2705")
    async def edit_assign(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = self.config.get("roles_asignar", [])
        view = RoleSelectView(self, "roles_asignar", current)
        await interaction.response.send_message(
            "Selecciona los roles que se **otorgaran** al aprobar:",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="Editar roles eliminados", style=discord.ButtonStyle.danger, emoji="\U0001f5d1\ufe0f")
    async def edit_remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = self.config.get("roles_eliminar", [])
        view = RoleSelectView(self, "roles_eliminar", current)
        await interaction.response.send_message(
            "Selecciona los roles que se **eliminaran** al aprobar:",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="Guardar", style=discord.ButtonStyle.success, emoji="\U0001f4be", row=1)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button):
        config_manager.save_aprobar_config(self.config)
        assign_count = len(self.config.get("roles_asignar", []))
        remove_count = len(self.config.get("roles_eliminar", []))
        embed = discord.Embed(
            title="Configuracion guardada",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Roles otorgados", value=str(assign_count), inline=True)
        embed.add_field(name="Roles eliminados", value=str(remove_count), inline=True)
        embed.set_footer(text="La configuracion se aplica al usar /aprobar")
        await interaction.response.edit_message(embed=embed, view=None)
        logger.info("Configuracion de aprobacion actualizada por %s", interaction.user)
        log_actions.log_info(
            "Config aprobar guardada",
            f"Por {interaction.user.mention}\nRoles otorgados: {assign_count}\nRoles eliminados: {remove_count}",
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Configuracion descartada.",
            embed=None,
            view=None,
            delete_after=5,
        )


@app_commands.command(name="config_aprobar", description="Abre el panel de configuracion de aprobacion")
@app_commands.default_permissions(administrator=True)
async def config_aprobar(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Este comando solo puede usarse en un servidor.", ephemeral=True)
        return

    config = config_manager.load_aprobar_config()
    view = AprobarConfigView(config, interaction.channel_id, 0, interaction.client)
    embed = view.build_embed()

    await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
    try:
        message = await interaction.original_response()
    except Exception:
        logger.exception("No se pudo obtener el mensaje del panel de aprobacion")
        return

    view.message_id = message.id
    logger.info("Panel de configuracion de aprobacion abierto por %s", interaction.user.id)


async def setup(bot):
    bot.tree.add_command(config_aprobar)
    logger.info("Comando /config_aprobar registrado")