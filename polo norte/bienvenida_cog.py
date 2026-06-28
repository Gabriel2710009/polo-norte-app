import logging
import discord
from discord import app_commands
import config_manager
import log_actions

logger = logging.getLogger("Bienvenida")


class BienvenidaModal(discord.ui.Modal, title="Editar mensaje de bienvenida"):
    mensaje = discord.ui.TextInput(
        label="Mensaje de bienvenida",
        style=discord.TextStyle.paragraph,
        placeholder="Escribe el mensaje de bienvenida...",
        max_length=4000,
    )

    def __init__(self, current_message: str):
        super().__init__()
        self.mensaje.default = current_message

    async def on_submit(self, interaction: discord.Interaction):
        nuevo_mensaje = self.mensaje.value
        config_manager.save_bienvenida_config({"mensaje": nuevo_mensaje})
        embed = discord.Embed(
            title="✅ Mensaje de bienvenida actualizado",
            description="El mensaje se guardó correctamente y estará disponible para `/bienvenida`.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Vista previa", value=nuevo_mensaje[:1024], inline=False)
        if len(nuevo_mensaje) > 1024:
            embed.add_field(name="(continuación)", value=nuevo_mensaje[1024:2048], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info("Mensaje de bienvenida actualizado por %s", interaction.user)
        log_actions.log_info(
            "💾 Bienvenida actualizada",
            f"Por {interaction.user.mention}\nLongitud: {len(nuevo_mensaje)} caracteres",
        )


@app_commands.command(name="config_bienvenida", description="Abre el editor del mensaje de bienvenida")
@app_commands.default_permissions(administrator=True)
async def config_bienvenida(interaction: discord.Interaction):
    config = config_manager.load_bienvenida_config()
    current = config.get("mensaje", "")
    modal = BienvenidaModal(current)
    await interaction.response.send_modal(modal)


@app_commands.command(name="bienvenida", description="Envía el mensaje de bienvenida en el canal actual")
@app_commands.describe(usuario="Usuario al que dirigir la bienvenida (opcional)")
@app_commands.default_permissions(administrator=True)
async def bienvenida(interaction: discord.Interaction, usuario: discord.Member = None):
    config = config_manager.load_bienvenida_config()
    mensaje = config.get("mensaje", "")
    if not mensaje:
        await interaction.response.send_message(
            "❌ No hay ningún mensaje de bienvenida configurado. Usá `/config_bienvenida` para crear uno.",
            ephemeral=True,
        )
        return

    content = mensaje
    if usuario:
        content = f"{usuario.mention}\n\n{mensaje}"

    await interaction.response.send_message(content)
    logger.info(
        "Bienvenida enviada por %s en %s%s",
        interaction.user,
        interaction.channel,
        f" para {usuario}" if usuario else "",
    )
    log_actions.log_info(
        "📨 Bienvenida enviada",
        f"Por {interaction.user.mention} en {interaction.channel.mention}"
        + (f"\nPara: {usuario.mention}" if usuario else ""),
    )


async def setup(bot):
    bot.tree.add_command(config_bienvenida)
    bot.tree.add_command(bienvenida)
    logger.info("Comandos /config_bienvenida y /bienvenida registrados")
