import logging
from datetime import datetime

import discord
from discord import app_commands

import state
from database import guardar_config_alertas
from utils import es_armero
from alertas import generar_preview_alertas

logger = logging.getLogger("ArmamentBot")


def register(tree: app_commands.CommandTree):

    @tree.command(name="apagar_alertas", description="Desactivar todas las alertas de retiro")
    async def apagar_alertas(interaction: discord.Interaction):
        from log_actions import log_accion
        if not es_armero(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        state.ALERTAS_ACTIVAS = False
        guardar_config_alertas(state.OBJETOS_ALERTAR, False, interaction.user.name)
        await log_accion(interaction.user, "Apagó alertas", "", discord.Color.red(), "🔕")
        await interaction.response.send_message("🔕 Alertas **desactivadas**.", ephemeral=True)

    @tree.command(name="encender_alertas", description="Activar todas las alertas de retiro")
    async def encender_alertas(interaction: discord.Interaction):
        from log_actions import log_accion
        if not es_armero(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        state.ALERTAS_ACTIVAS = True
        guardar_config_alertas(state.OBJETOS_ALERTAR, True, interaction.user.name)
        await log_accion(interaction.user, "Encendió alertas", "", discord.Color.green(), "🔔")
        await interaction.response.send_message("🔔 Alertas **activadas**.", ephemeral=True)

    @tree.command(name="configurar_alertas", description="Elegir qué objetos generan alertas")
    async def configurar_alertas(interaction: discord.Interaction):
        from views.alertas_view import ConfigurarAlertasView
        if not es_armero(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        embed = discord.Embed(
            title="⚙️ Configurar alertas",
            description=(
                "Seleccioná los objetos que querés que generen alertas.\n"
                "Si no seleccionás nada → se alertarán **todos** los retiros.\n\n"
                + generar_preview_alertas()
            ),
            color=discord.Color.blue(),
            timestamp=datetime.now(),
        )
        await interaction.response.send_message(embed=embed, view=ConfigurarAlertasView(), ephemeral=True)