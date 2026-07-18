import logging
import discord
from discord import app_commands

import log_actions

logger = logging.getLogger("HelpCog")

SECCIONES = [
    {
        "titulo": "\u2705 /aprobar",
        "descripcion": (
            "Aprueba la postulación de un usuario.\n\n"
            "Asigna los roles configurados en `/config_aprobar` y elimina "
            "automáticamente los roles de postulación si el usuario los posee.\n\n"
            "**Requiere:** Administrador, Gestionar Roles o rol autorizado."
        ),
    },
    {
        "titulo": "\u2699\ufe0f /config_aprobar",
        "descripcion": (
            "Abre un panel interactivo para configurar los roles del comando `/aprobar`.\n\n"
            "Permite elegir mediante `RoleSelect` los roles que se otorgarán "
            "y los que se eliminarán automáticamente al aprobar.\n\n"
            "La configuración se guarda de forma persistente."
        ),
    },
    {
        "titulo": "\U0001f4dd /config_bienvenida",
        "descripcion": (
            "Abre un modal para editar el mensaje de bienvenida que luego "
            "enviará `/bienvenida`.\n\n"
            "El mensaje se guarda de forma persistente."
        ),
    },
    {
        "titulo": "\U0001f44b /bienvenida",
        "descripcion": (
            "Envía el mensaje de bienvenida configurado en `/config_bienvenida` "
            "directamente en el canal actual.\n\n"
            "**No se envía automáticamente al aprobar.** El entrevistador "
            "decide cuándo ejecutarlo.\n\n"
            "Acepta un usuario opcional para mencionarlo al inicio del mensaje."
        ),
    },
    {
        "titulo": "\U0001f6ab /blacklist",
        "descripcion": (
            "Impide que una persona vuelva a postularse.\n\n"
            "Acepta mención (@Usuario) o Discord ID.\n\n"
            "**Datos IC:** El bot busca automáticamente el Nombre IC "
            "en los mensajes fijados del canal actual. Si no lo encuentra, "
            "te mostrará un formulario para completar los datos "
            "(Nombre IC, Número IC, IBAN IC, Steam URL) "
            "o un botón \"Desconozco\" si no los tienes.\n\n"
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
