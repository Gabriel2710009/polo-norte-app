import logging
import discord
from discord import app_commands
import config_manager
import log_actions

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
        await self.parent.original_interaction.edit_original_response(embed=embed, view=self.parent)
        await interaction.response.edit_message(
            content="✅ Roles actualizados.",
            embed=None,
            view=None,
            delete_after=2,
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ Cancelado.", embed=None, view=None, delete_after=2)


class AprobarConfigView(discord.ui.View):
    def __init__(self, config, original_interaction):
        super().__init__(timeout=300)
        self.config = config
        self.original_interaction = original_interaction

    def build_embed(self):
        assign_roles = "\n".join(f"<@&{rid}>" for rid in self.config.get("roles_asignar", [])) or "Ninguno"
        remove_roles = "\n".join(f"<@&{rid}>" for rid in self.config.get("roles_eliminar", [])) or "Ninguno"
        embed = discord.Embed(
            title="⚙️ Configuración de aprobación",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="✅ Roles otorgados", value=assign_roles, inline=False)
        embed.add_field(name="🗑️ Roles eliminados", value=remove_roles, inline=False)
        embed.set_footer(text="Los cambios no se guardan hasta presionar Guardar")
        return embed

    @discord.ui.button(label="Editar roles otorgados", style=discord.ButtonStyle.primary, emoji="✅")
    async def edit_assign(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = self.config.get("roles_asignar", [])
        view = RoleSelectView(self, "roles_asignar", current)
        await interaction.response.send_message(
            "Selecciona los roles que se **otorgarán** al aprobar:",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="Editar roles eliminados", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def edit_remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = self.config.get("roles_eliminar", [])
        view = RoleSelectView(self, "roles_eliminar", current)
        await interaction.response.send_message(
            "Selecciona los roles que se **eliminarán** al aprobar:",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="Guardar", style=discord.ButtonStyle.success, emoji="💾", row=1)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button):
        config_manager.save_aprobar_config(self.config)
        assign_count = len(self.config.get("roles_asignar", []))
        remove_count = len(self.config.get("roles_eliminar", []))
        embed = discord.Embed(
            title="✅ Configuración guardada",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Roles otorgados", value=str(assign_count), inline=True)
        embed.add_field(name="Roles eliminados", value=str(remove_count), inline=True)
        embed.set_footer(text="La configuración se aplica al usar /aprobar")
        await interaction.response.edit_message(embed=embed, view=None)
        logger.info("Configuración de aprobación actualizada por %s", interaction.user)
        log_actions.log_info(
            "💾 Config aprobar guardada",
            f"Por {interaction.user.mention}\nRoles otorgados: {assign_count}\nRoles eliminados: {remove_count}",
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="❌ Configuración descartada.",
            embed=None,
            view=None,
            delete_after=5,
        )


@app_commands.command(name="config_aprobar", description="Abre el panel de configuración de aprobación")
@app_commands.default_permissions(administrator=True)
async def config_aprobar(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("❌ Este comando solo puede usarse en un servidor.", ephemeral=True)
        return

    config = config_manager.load_aprobar_config()
    view = AprobarConfigView(config, interaction)
    embed = view.build_embed()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    bot.tree.add_command(config_aprobar)
    logger.info("Comando /config_aprobar registrado")
