import logging
import traceback
import discord
from discord.ext import commands

logger = logging.getLogger("ErrorHandler")

OWNER_ID = None  # se setea desde afuera con setup()

def _setup_owner_id(owner_id: int):
    global OWNER_ID
    OWNER_ID = owner_id


async def reportar_error(
    error: Exception,
    contexto: str = "",
    interaction: discord.Interaction | None = None,
    bot=None,
    es_critico: bool = False,
):
    """
    Función central para reportar errores críticos.
    1. Alimenta el monitor (StatusReporter) con contador y resumen.
    2. Loguea con logger.exception()
    3. Intenta enviar DM al OWNER_ID con embed de error.
    4. Si falla el DM, solo loguea warning.
    5. Nunca rompe el bot.
    """
    global OWNER_ID
    from services import status_reporter
    status_reporter.report_error(error, contexto=contexto or None, es_critico=es_critico)

    if OWNER_ID is None:
        logger.warning("OWNER_ID no configurado, no se puede enviar DM de error")
        return

    logger.exception("Error en %s: %s", contexto, error)

    tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))

    # Obtener archivo y línea del marco más externo
    import os
    archivo = "desconocido"
    linea = 0
    tb_iter = error.__traceback__
    while tb_iter:
        frame = tb_iter.tb_frame
        archivo = os.path.basename(frame.f_code.co_filename)
        linea = tb_iter.tb_lineno
        tb_iter = tb_iter.tb_next

    embed = discord.Embed(
        title="🔴 ERROR",
        color=discord.Color.red(),
    )

    desc = f"❌ [{contexto}] Error ocurrido: {error}"
    embed.description = f"```\n{desc[:1024]}\n```"

    if len(tb) > 1024:
        tb = tb[:1000] + "..."

    embed.add_field(name="Traceback", value=f"```py\n{tb}\n```", inline=False)

    embed.set_footer(text=f"Polo Norte | {archivo}:{linea}")

    try:
        user = await _get_owner(interaction, bot)
        if user:
            await user.send(content="Me romp\u00ed \U0001f480", embed=embed)
    except discord.Forbidden:
        logger.warning("No se pudo enviar DM de error al owner (Forbidden)")
    except Exception as e:
        logger.warning("Error enviando DM de error: %s", e)


async def _get_owner(interaction=None, bot=None):
    """Resuelve el owner desde interaction.client, bot, o cualquier referencia disponible."""
    global OWNER_ID
    client = None
    if interaction and interaction.client:
        client = interaction.client
    elif bot is not None and hasattr(bot, "fetch_user"):
        client = bot
    if client is None:
        return None
    try:
        return await client.fetch_user(OWNER_ID)
    except Exception:
        return None


class ErrorCog(commands.Cog):
    """Cog que registra los handlers globales de error."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception):
        await reportar_error(error, contexto=f"Comando {ctx.command}", bot=ctx.bot)

    @commands.Cog.listener()
    async def on_view_error(self, view, error: Exception):
        await reportar_error(error, contexto=f"View {type(view).__name__}", bot=self.bot)


_setup_ejecutado = False


async def setup(bot: commands.Bot):
    global _setup_ejecutado
    if _setup_ejecutado:
        return
    _setup_ejecutado = True

    # Cargar OWNER_ID desde config_manager o variable de entorno
    from utils import config_manager
    config = config_manager.load_config()
    owner_id = config.get("owner_id")
    if owner_id:
        try:
            _setup_owner_id(int(owner_id))
        except (ValueError, TypeError):
            logger.warning("OWNER_ID inválido en config: %r", owner_id)

    await bot.add_cog(ErrorCog(bot))

    # Tree error handler para app_commands (slash commands)
    async def tree_on_error(interaction: discord.Interaction, error: Exception):
        await reportar_error(error, contexto=f"Slash /{interaction.command.name if interaction.command else 'desconocido'}", interaction=interaction)
        if interaction.response.is_done():
            await interaction.followup.send("❌ Ocurrió un error interno.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Ocurrió un error interno.", ephemeral=True)

    bot.tree.on_error = tree_on_error
    logger.info("ErrorHandler iniciado. OWNER_ID=%s", owner_id)
