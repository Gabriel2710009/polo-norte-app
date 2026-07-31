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
):
    """
    Función central para reportar errores críticos.
    1. Loguea con logger.exception()
    2. Intenta enviar DM al OWNER_ID con embed de error.
    3. Si falla el DM, solo loguea warning.
    4. Nunca rompe el bot.
    """
    global OWNER_ID
    if OWNER_ID is None:
        logger.warning("OWNER_ID no configurado, no se puede enviar DM de error")
        return

    logger.exception("Error en %s: %s", contexto, error)

    tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))

    # Obtener archivo y línea del marco más externo
    import os
    archivo = "desconocido"
    linea = 0
    if error.__traceback__:
        frame = error.__traceback__.tb_frame
        while frame:
            archivo = os.path.basename(frame.f_code.co_filename)
            linea = frame.f_lineno
            frame = frame.f_next

    embed = discord.Embed(
        title="🔴 ERROR",
        color=discord.Color.red(),
    )

    desc = f"❌ [{contexto}] Error: {error}"
    embed.description = f"```\n{desc[:1024]}\n```"

    if len(tb) > 1024:
        tb = tb[:1000] + "..."

    embed.add_field(name="Traceback", value=f"```py\n{tb}\n```", inline=False)

    embed.set_footer(text=f"Polo Norte | {archivo}:{linea}")

    try:
        user = await _get_owner(interaction)
        if user:
            await user.send(embed=embed)
    except discord.Forbidden:
        logger.warning("No se pudo enviar DM de error al owner (Forbidden)")
    except Exception as e:
        logger.warning("Error enviando DM de error: %s", e)


async def _get_owner(interaction=None):
    """Intenta obtener el usuario owner desde un interaction o desde el bot."""
    global OWNER_ID
    if interaction and interaction.client:
        try:
            return await interaction.client.fetch_user(OWNER_ID)
        except:
            pass
    return None


class ErrorCog(commands.Cog):
    """Cog que registra los handlers globales de error."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception):
        await reportar_error(error, contexto=f"Comando {ctx.command}", interaction=None)

    @commands.Cog.listener()
    async def on_view_error(self, view, error: Exception):
        await reportar_error(error, contexto=f"View {type(view).__name__}", interaction=None)


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
