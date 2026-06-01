import logging

import discord
from discord import app_commands

from utils import es_armero
from antirrobo import generar_preview_antirrobo

logger = logging.getLogger("ArmamentBot")


def register(tree: app_commands.CommandTree):

    @tree.command(name="help", description="Ver comandos disponibles")
    async def help_command(interaction: discord.Interaction):
        es_arm = es_armero(interaction.user)
        embed  = discord.Embed(
            title="📖 Comandos disponibles",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="📊 Estadísticas",
            value="`/armas` `/balas` `/pistolas` `/arma_blanca` `/otros` `/drogas`",
            inline=False,
        )
        if es_arm:
            embed.add_field(
                name="🚨 Alertas",
                value="`/apagar_alertas` `/encender_alertas` `/configurar_alertas`",
                inline=False,
            )
            embed.add_field(
                name="🧩 Operativos",
                value="`/inicio_operativo` `/terminar_operativo` `/vincular_operativo` `/config_verificacion`",
                inline=False,
            )
            embed.add_field(
                name="📊 Asistencia",
                value="`/asistencia` `/asistencia_semanal_activar` `/asistencia_semanal_desactivar` `/asistencia_semanal_estado` `/debug_interesados`",
                inline=False,
            )
            embed.add_field(
                name="🛡️ Antirrobo",
                value="`/antirrobo` `/whitelist_antirrobo`",
                inline=False,
            )
            embed.add_field(
                name="🔧 Admin",
                value="`/retiros_pendientes` `/sincronizar_historial_texto` `/chemi_activar` `/chemi_desactivar`",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tree.command(name="antirrobo", description="Gestionar el sistema antirrobo")
    async def antirrobo(interaction: discord.Interaction):
        from views.antirrobo_view import AntiRobControlView
        if not es_armero(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        embed = discord.Embed(
            title="🛡️ Sistema Antirrobo",
            description=generar_preview_antirrobo(),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, view=AntiRobControlView(), ephemeral=True)

    @tree.command(name="whitelist_antirrobo", description="Ver o gestionar whitelist del antirrobo")
    async def whitelist_antirrobo(interaction: discord.Interaction):
        from database import obtener_whitelist_antirrobo
        from views.antirrobo_view import AntiRobWhitelistView
        if not es_armero(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        lista = obtener_whitelist_antirrobo()
        if not lista:
            descripcion = "La whitelist está vacía."
        else:
            lineas = [
                f"• `{r['discord_id']}` — {r['nombre'] or 'N/A'} (agregado por {r['added_by'] or 'N/A'})"
                for r in lista[:20]
            ]
            if len(lista) > 20:
                lineas.append(f"… y {len(lista) - 20} más.")
            descripcion = "\n".join(lineas)

        embed = discord.Embed(
            title="🛡️ Whitelist Antirrobo",
            description=descripcion,
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, view=AntiRobWhitelistView(), ephemeral=True)
