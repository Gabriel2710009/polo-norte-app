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
            panel = await interaction.original_response()
        except Exception:
            logger.exception("No se pudo obtener el mensaje del panel de aprobaci\u00f3n")
        else:
            self.parent.message = panel
            try:
                await self.parent.message.edit(embed=embed, view=self.parent)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.exception("Error editando mensaje del panel de aprobaci\u00f3n")
        await interaction.response.edit_message(
            content="\u2705 Roles actualizados.",
            embed=None,
            view=None,
            delete_after=2,
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="\u274c Cancelado.", embed=None, view=None, delete_after=2)


class AprobarConfigView(discord.ui.View):
    def __init__(self, config):
        super().__init__(timeout=300)
        self.config = config
        self.message: discord.Message | None = None

    def build_embed(self):
        assign_roles = "\n".join(f"<@&{rid}>" for rid in self.config.get("roles_asignar", [])) or "Ninguno"
        remove_roles = "\n".join(f"<@&{rid}>" for rid in self.config.get("roles_eliminar", [])) or "Ninguno"
        embed = discord.Embed(
            title="\u2699\ufe0f Configuraci\u00f3n de aprobaci\u00f3n",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="\u2705 Roles otorgados", value=assign_roles, inline=False)
        embed.add_field(name="\U0001f5d1\ufe0f Roles eliminados", value=remove_roles, inline=False)
        embed.set_footer(text="Los cambios no se guardan hasta presionar Guardar")
        return embed

    @discord.ui.button(label="Editar roles otorgados", style=discord.ButtonStyle.primary, emoji="\u2705")
    async def edit_assign(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = self.config.get("roles_asignar", [])
        view = RoleSelectView(self, "roles_asignar", current)
        await interaction.response.send_message(
            "Selecciona los roles que se **otorgar\u00e1n** al aprobar:",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="Editar roles eliminados", style=discord.ButtonStyle.danger, emoji="\U0001f5d1\ufe0f")
    async def edit_remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = self.config.get("roles_eliminar", [])
        view = RoleSelectView(self, "roles_eliminar", current)
        await interaction.response.send_message(
            "Selecciona los roles que se **eliminar\u00e1n** al aprobar:",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="Guardar", style=discord.ButtonStyle.success, emoji="\U0001f4be", row=1)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button):
        config_manager.save_aprobar_config(self.config)
        assign_count = len(self.config.get("roles_asignar", []))
        remove_count = len(self.config.get("roles_eliminar", []))
        embed = discord.Embed(
            title="\u2705 Configuraci\u00f3n guardada",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Roles otorgados", value=str(assign_count), inline=True)
        embed.add_field(name="Roles eliminados", value=str(remove_count), inline=True)
        embed.set_footer(text="La configuraci\u00f3n se aplica al usar /aprobar")
        await interaction.response.edit_message(embed=embed, view=None)
        logger.info("Configuraci\u00f3n de aprobaci\u00f3n actualizada por %s", interaction.user)
        log_actions.log_info(
            "\U0001f4be Config aprobar guardada",
            f"Por {interaction.user.mention}\nRoles otorgados: {assign_count}\nRoles eliminados: {remove_count}",
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="\u274c Configuraci\u00f3n descartada.",
            embed=None,
            view=None,
            delete_after=5,
        )


@app_commands.command(name="config_aprobar", description="Abre el panel de configuraci\u00f3n de aprobaci\u00f3n")
@app_commands.default_permissions(administrator=True)
async def config_aprobar(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("\u274c Este comando solo puede usarse en un servidor.", ephemeral=True)
        return

    config = config_manager.load_aprobar_config()
    view = AprobarConfigView(config)
    embed = view.build_embed()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    try:
        view.message = await interaction.original_response()
    except Exception:
        logger.exception("No se pudo obtener el mensaje del panel de aprobaci\u00f3n")


async def setup(bot):
    bot.tree.add_command(config_aprobar)
    logger.info("Comando /config_aprobar registrado")
