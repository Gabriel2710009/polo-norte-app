import logging
import discord
from discord import app_commands

import log_actions

logger = logging.getLogger("HelpCog")

SECCIONES = [
    {
        "titulo": "\U0001f6ab /blacklist",
        "descripcion": (
            "Permite impedir que una persona vuelva a postularse.\n\n"
            "Acepta mención (@Usuario) o Discord ID como identificador.\n\n"
            "**Ejemplo:**\n"
            "Si un usuario insulta entrevistadores o incumple normas "
            "durante una entrevista, puedes añadirlo a la blacklist."
        ),
    },
    {
        "titulo": "\u2705 /unblacklist",
        "descripcion": (
            "Permite retirar una blacklist aplicada anteriormente.\n\n"
            "**Ejemplo:**\n"
            "Si la situación fue revisada y se decide darle una nueva oportunidad."
        ),
    },
    {
        "titulo": "\U0001f50d /blacklist-info",
        "descripcion": (
            "Muestra toda la información registrada de una blacklist.\n\n"
            "**Ejemplo:**\n"
            "Consultar motivo, fecha y quién aplicó la sanción."
        ),
    },
    {
        "titulo": "\U0001f50e /blacklist-search",
        "descripcion": (
            "Permite buscar usuarios dentro de la blacklist.\n\n"
            "Puedes buscar por:\n"
            "\u2022 Nombre IC\n"
            "\u2022 Discord ID"
        ),
    },
    {
        "titulo": "\U0001f4cb /blacklist-list",
        "descripcion": (
            "Muestra un listado completo de todos los usuarios "
            "que están actualmente en la blacklist."
        ),
    },
    {
        "titulo": "\U0001f504 /blacklist-sync",
        "descripcion": (
            "Revisa si hay diferencias entre la blacklist y los roles "
            "asignados en el servidor, y permite corregirlas."
        ),
    },
    {
        "titulo": "\U0001f4da /help",
        "descripcion": (
            "Muestra esta guía con todos los comandos disponibles.\n\n"
            "Usá este comando cuando tengas dudas sobre qué opciones tienes."
        ),
    },
]


@app_commands.command(name="help", description="Muestra una guía de comandos disponibles")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="\U0001f4da Ayuda de Polo Logs",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    for seccion in SECCIONES:
        embed.add_field(
            name=seccion["titulo"],
            value=seccion["descripcion"],
            inline=False,
        )

    embed.set_footer(text="Usá los comandos escribiendo / seguido del nombre.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    bot.tree.add_command(help_command)
    logger.info("Comando /help registrado")
