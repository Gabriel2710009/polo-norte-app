import logging
import discord
from discord import app_commands

from services import log_actions

logger = logging.getLogger("HelpCog")

SECCIONES = [
    {
        "titulo": "\u2705 /aprobar",
        "descripcion": (
            "Aprueba la postulaci\u00f3n de un usuario.\n\n"
            "Asigna los roles configurados en `/config_aprobar` y elimina "
            "autom\u00e1ticamente los roles de postulaci\u00f3n si el usuario los posee.\n\n"
            "**Requiere:** Administrador, Gestionar Roles o rol autorizado."
        ),
    },
    {
        "titulo": "\u2699\ufe0f /config_aprobar",
        "descripcion": (
            "Abre un panel interactivo para configurar los roles del comando `/aprobar`.\n\n"
            "Permite elegir mediante `RoleSelect` los roles que se otorgar\u00e1n "
            "y los que se eliminar\u00e1n autom\u00e1ticamente al aprobar.\n\n"
            "La configuraci\u00f3n se guarda de forma persistente."
        ),
    },
    {
        "titulo": "\U0001f4dd /config_bienvenida",
        "descripcion": (
            "Abre un modal para editar el mensaje de bienvenida que luego "
            "enviar\u00e1 `/bienvenida`.\n\n"
            "El mensaje se guarda de forma persistente."
        ),
    },
    {
        "titulo": "\U0001f44b /bienvenida",
        "descripcion": (
            "Env\u00eda el mensaje de bienvenida configurado en `/config_bienvenida` "
            "directamente en el canal actual.\n\n"
            "**No se env\u00eda autom\u00e1ticamente al aprobar.** El entrevistador "
            "decide cu\u00e1ndo ejecutarlo.\n\n"
            "Acepta un usuario opcional para mencionarlo al inicio del mensaje."
        ),
    },
    {
        "titulo": "\U0001f6ab /blacklist",
        "descripcion": (
            "Impide que una persona vuelva a postularse.\n\n"
            "Acepta menci\u00f3n (@Usuario) o Discord ID.\n\n"
            "**Datos IC:** El bot busca autom\u00e1ticamente el Nombre IC "
            "en los mensajes fijados del canal actual. Si no lo encuentra, "
            "te mostrar\u00e1 un formulario para completar los datos "
            "(Nombre IC, N\u00famero IC, IBAN IC, Steam URL) "
            "o un bot\u00f3n \"Desconozco\" si no los tienes.\n\n"
            "**Ejemplo:**\n"
            "Si un usuario insulta entrevistadores o incumple normas "
            "durante una entrevista, puedes a\u00f1adirlo a la blacklist."
        ),
    },
    {
        "titulo": "\u2705 /unblacklist",
        "descripcion": (
            "Permite retirar una blacklist aplicada anteriormente.\n\n"
            "**Ejemplo:**\n"
            "Si la situaci\u00f3n fue revisada y se decide darle una nueva oportunidad."
        ),
    },
    {
        "titulo": "\U0001f50d /blacklist-info",
        "descripcion": (
            "Muestra toda la informaci\u00f3n registrada de una blacklist.\n\n"
            "**Ejemplo:**\n"
            "Consultar motivo, fecha y qui\u00e9n aplic\u00f3 la sanci\u00f3n."
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
            "que est\u00e1n actualmente en la blacklist."
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
        "titulo": "\u2699\ufe0f /config_postulacion",
        "descripcion": (
            "Abre un panel interactivo para configurar los canales "
            "utilizados por el sistema de entrevistas.\n\n"
            "Permite elegir mediante `ChannelSelect`:\n"
            "\u2022 Canal de logs\n"
            "\u2022 Canal de postulaciones\n"
            "\u2022 Canal de errores\n\n"
            "Incluye un bot\u00f3n para ingresar IDs manualmente como fallback.\n"
            "La configuraci\u00f3n se guarda de forma persistente en la base de datos "
            "y se actualiza en memoria sin necesidad de reinicio.\n\n"
            "**Requiere:** Administrador o rol autorizado."
        ),
    },
    {
        "titulo": "\U0001f4da /help",
        "descripcion": (
            "Muestra esta gu\u00eda con todos los comandos disponibles.\n\n"
            "Us\u00e1 este comando cuando tengas dudas sobre qu\u00e9 opciones tienes."
        ),
    },
]


@app_commands.command(name="help", description="Muestra una gu\u00eda de comandos disponibles")
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

    embed.set_footer(text="Us\u00e1 los comandos escribiendo / seguido del nombre.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    bot.tree.add_command(help_command)
    logger.info("Comando /help registrado")
