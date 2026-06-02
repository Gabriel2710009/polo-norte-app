import os
import logging
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import database as db
from validator import validate
from parser import parse_embed
import fichaje

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
LOGS_CHANNEL_ID = int(os.getenv("LOGS_CHANNEL_ID", 0))
ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", 0))
ALERT_ROLE_ID = int(os.getenv("ALERT_ROLE_ID", 0))
FICHAJE_CHANNEL_ID = int(os.getenv("FICHAJE_CHANNEL_ID", 0))

FICHAJE_ACTIVO = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("Main")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="/", intents=intents)


@bot.event
async def on_ready():
    global FICHAJE_ACTIVO
    if not all([TOKEN, DATABASE_URL, LOGS_CHANNEL_ID, ALERT_CHANNEL_ID, ALERT_ROLE_ID]):
        logger.error("Faltan variables de entorno.")
        return

    db.init()
    logger.info("DB inicializada")

    pendientes = db.get_pending_alerts()
    if pendientes:
        logger.info("Alertas pendientes encontradas: %s", len(pendientes))
        await fichaje.verificar_pendientes_al_inicio(bot, ALERT_CHANNEL_ID, ALERT_ROLE_ID)

    await bot.tree.sync()
    logger.info("Comandos slash sincronizados")

    logger.info("Bot conectado como %s (%s)", bot.user, bot.user.id)


@bot.event
async def on_message(message: discord.Message):
    if message.author.id == bot.user.id:
        return
    if not message.embeds:
        return

    for embed in message.embeds:
        title = embed.title or ""
        description = embed.description or ""
        text = f"{title}\n{description}"

        # ── Stash log processing ──
        if message.channel.id == LOGS_CHANNEL_ID:
            result = validate(text)
            parsed = parse_embed(text)

            if FICHAJE_ACTIVO and parsed:
                fichaje.procesar_stash_para_taser(parsed)

            if "ALERT: true" in result:
                channel = bot.get_channel(ALERT_CHANNEL_ID)
                if not channel:
                    continue

                lines = []
                for i in parsed.get("items", []):
                    lines.append(f"• `{i['name']}` x{i['quantity']}")

                alert = discord.Embed(
                    title="🚨 ALERTA - ÍTEM ILEGAL EN STASH",
                    color=discord.Color.red(),
                    timestamp=discord.utils.utcnow(),
                )
                alert.add_field(name="👤 Jugador", value=parsed.get("player", "N/A"), inline=True)
                alert.add_field(name="🎮 Steam", value=parsed.get("identifier", "N/A"), inline=True)
                alert.add_field(name="💬 Discord", value=f"<@{parsed.get('discord_id')}>", inline=True)
                alert.add_field(name="📦 Items", value="\n".join(lines) or "N/A", inline=False)
                alert.add_field(name="🔢 Stash ID", value=parsed.get("stash_id", "N/A"), inline=True)
                alert.add_field(name="📍 Coords", value=parsed.get("coords", "N/A"), inline=True)
                alert.add_field(name="🔗 Log original", value=f"[Ver mensaje]({message.jump_url})", inline=False)

                await channel.send(content=f"<@&{ALERT_ROLE_ID}>", embed=alert)

        # ── Fichaje processing ──
        if FICHAJE_ACTIVO and message.channel.id == FICHAJE_CHANNEL_ID:
            data = fichaje.parse_fichaje_embed(embed)
            if not data:
                continue
            if data["tipo"] == "INICIO":
                await fichaje.handle_clock_in(bot, embed, LOGS_CHANNEL_ID)
            elif data["tipo"] == "CIERRE":
                await fichaje.handle_clock_out(bot, embed, LOGS_CHANNEL_ID, ALERT_CHANNEL_ID, ALERT_ROLE_ID)


@bot.tree.command(name="fichaje", description="Activa o desactiva el tracking de fichajes y táseres")
@app_commands.describe(estado="on para activar, off para desactivar")
async def fichaje_toggle(interaction: discord.Interaction, estado: str = None):
    global FICHAJE_ACTIVO

    if not FICHAJE_CHANNEL_ID:
        await interaction.response.send_message("❌ `FICHAJE_CHANNEL_ID` no configurado en el .env", ephemeral=True)
        return

    if estado is None:
        estado_actual = "🟢 ACTIVO" if FICHAJE_ACTIVO else "🔴 INACTIVO"
        await interaction.response.send_message(
            f"📋 Estado del fichaje: {estado_actual}\nUsá `/fichaje on` o `/fichaje off` para cambiar.",
            ephemeral=True,
        )
        return

    if estado.lower() in ("on", "1", "true", "si"):
        FICHAJE_ACTIVO = True
        logger.info("Fichaje tracking activado")
        await interaction.response.send_message("🟢 **Fichaje tracking ACTIVADO**\nSe están monitoreando los fichajes y el retorno de táseres.", ephemeral=True)
    elif estado.lower() in ("off", "0", "false", "no"):
        FICHAJE_ACTIVO = False
        logger.info("Fichaje tracking desactivado")
        await interaction.response.send_message("🔴 **Fichaje tracking DESACTIVADO**", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Usá `/fichaje on` o `/fichaje off`", ephemeral=True)


if __name__ == "__main__":
    if not all([TOKEN, DATABASE_URL, LOGS_CHANNEL_ID, ALERT_CHANNEL_ID, ALERT_ROLE_ID]):
        logger.error("Faltan variables de entorno. Revisá el .env")
        exit(1)
    bot.run(TOKEN)
