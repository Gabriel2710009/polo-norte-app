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
import log_actions

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
LOGS_CHANNEL_ID = int(os.getenv("LOGS_CHANNEL_ID", 0))
ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", 0))
FICHAJE_CHANNEL_ID = int(os.getenv("FICHAJE_CHANNEL_ID", 0))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", 0))

ITEMS_ACTIVO = False
FICHAJE_ACTIVO = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("Main")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="/", intents=intents)


@bot.event
async def on_ready():
    global FICHAJE_ACTIVO
    if not all([TOKEN, DATABASE_URL, LOGS_CHANNEL_ID, ALERT_CHANNEL_ID]):
        logger.error("Faltan variables de entorno.")
        return

    log_actions.setup(bot, LOG_CHANNEL_ID)

    try:
        db.init()
        logger.info("DB inicializada correctamente")
        log_actions.log_info("✅ DB inicializada", "Conexión a PostgreSQL establecida y tablas listas.")
    except Exception as e:
        logger.critical("Error inicializando DB: %s", e)
        await log_actions.log_error("❌ Error DB", f"No se pudo inicializar la base de datos:\n`{e}`")
        return

    try:
        pendientes = db.get_pending_alerts()
        if pendientes:
            logger.info("Alertas pendientes encontradas: %s", len(pendientes))
            log_actions.log_info("⏳ Alertas pendientes", f"Se encontraron {len(pendientes)} alertas de táser sin resolver del turno anterior.")
            await fichaje.verificar_pendientes_al_inicio(bot, ALERT_CHANNEL_ID)
    except Exception as e:
        logger.error("Error verificando pendientes: %s", e)
        await log_actions.log_error("❌ Error pendientes", f"No se pudieron verificar alertas pendientes:\n`{e}`")

    try:
        await bot.tree.sync()
        logger.info("Comandos slash sincronizados")
    except Exception as e:
        logger.error("Error sincronizando comandos: %s", e)
        await log_actions.log_error("❌ Error sync", f"No se pudieron sincronizar los comandos slash:\n`{e}`")

    logger.info("Bot conectado como %s (%s)", bot.user, bot.user.id)
    log_actions.log_info("🟢 Bot iniciado", f"Conectado como {bot.user} ({bot.user.id})")


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
            parsed = parse_embed(text)

            if FICHAJE_ACTIVO and parsed:
                taser_devuelto = fichaje.procesar_stash_para_taser(parsed)
                if taser_devuelto:
                    channel = bot.get_channel(ALERT_CHANNEL_ID)
                    if channel:
                        constancia = discord.Embed(
                            title="✅ TÁSER DEVUELTO",
                            color=discord.Color.green(),
                            timestamp=discord.utils.utcnow(),
                        )
                        constancia.add_field(name="👤 Usuario", value=f"<@{parsed.get('discord_id')}>", inline=True)
                        constancia.add_field(name="📦 Item", value=f"`{taser_devuelto}`", inline=True)
                        await channel.send(embed=constancia)

            if ITEMS_ACTIVO:
                result = validate(text)
                if "ALERT: true" in result:
                    channel = bot.get_channel(ALERT_CHANNEL_ID)
                    if not channel:
                        continue

                    lines = []
                    for i in parsed.get("items", []):
                        lines.append(f"• `{i['name']}` x{i['quantity']}")

                    alert = discord.Embed(
                        title="🚨 ÍTEM ILEGAL EN STASH",
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

                    await channel.send(embed=alert)
                    logger.info("Constancia de item ilegal: %s - %s", parsed.get("player"), ", ".join(lines))
                    log_actions.log_warning(
                        "🚨 Item ilegal en stash",
                        f"**Jugador:** {parsed.get('player')}\n"
                        f"**Items:** {', '.join(lines)}\n"
                        f"**Steam:** {parsed.get('identifier')}\n"
                        f"[Ver log]({message.jump_url})"
                    )

        # ── Fichaje processing ──
        if FICHAJE_ACTIVO and message.channel.id == FICHAJE_CHANNEL_ID:
            try:
                data = fichaje.parse_fichaje_embed(embed)
                if not data:
                    continue
                if data["tipo"] == "INICIO":
                    await fichaje.handle_clock_in(bot, embed, LOGS_CHANNEL_ID)
                    logger.info("Clock-in registrado: %s", data["user_id"])
                    log_actions.log_info("🟢 Clock-in", f"Usuario <@{data['user_id']}> inició turno.")
                elif data["tipo"] == "CIERRE":
                    await fichaje.handle_clock_out(bot, embed, LOGS_CHANNEL_ID, ALERT_CHANNEL_ID)
                    logger.info("Clock-out registrado: %s", data["user_id"])
                    log_actions.log_info("🔴 Clock-out", f"Usuario <@{data['user_id']}> cerró turno.")
            except Exception as e:
                logger.error("Error procesando fichaje: %s", e)
                await log_actions.log_error("❌ Error fichaje", f"Error procesando embed de fichaje:\n`{e}`")


@bot.tree.command(name="items", description="Activa o desactiva la constancia de items ilegales en stash")
@app_commands.describe(estado="on para activar, off para desactivar")
async def items_toggle(interaction: discord.Interaction, estado: str = None):
    global ITEMS_ACTIVO

    if estado is None:
        estado_actual = "🟢 ACTIVO" if ITEMS_ACTIVO else "🔴 INACTIVO"
        await interaction.response.send_message(
            f"📋 Estado de items ilegales: {estado_actual}\nUsá `/items on` o `/items off` para cambiar.",
            ephemeral=True,
        )
        return

    if estado.lower() in ("on", "1", "true", "si"):
        ITEMS_ACTIVO = True
        logger.info("Items ilegales activado por %s", interaction.user)
        log_actions.log_info("🟢 Items ACTIVADO", f"Por {interaction.user.mention} (`{interaction.user.id}`)")
        await interaction.response.send_message("🟢 **Constancia de items ilegales ACTIVADA**\nSe dejará constancia de items ilegales en stash.", ephemeral=True)
    elif estado.lower() in ("off", "0", "false", "no"):
        ITEMS_ACTIVO = False
        logger.info("Items ilegales desactivado por %s", interaction.user)
        log_actions.log_info("🔴 Items DESACTIVADO", f"Por {interaction.user.mention} (`{interaction.user.id}`)")
        await interaction.response.send_message("🔴 **Constancia de items ilegales DESACTIVADA**", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Usá `/items on` o `/items off`", ephemeral=True)


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
        logger.info("Fichaje tracking activado por %s", interaction.user)
        log_actions.log_info("🟢 Fichaje ACTIVADO", f"Por {interaction.user.mention} (`{interaction.user.id}`)")
        await interaction.response.send_message("🟢 **Fichaje tracking ACTIVADO**\nSe están monitoreando los fichajes y el retorno de táseres.", ephemeral=True)
    elif estado.lower() in ("off", "0", "false", "no"):
        FICHAJE_ACTIVO = False
        logger.info("Fichaje tracking desactivado por %s", interaction.user)
        log_actions.log_info("🔴 Fichaje DESACTIVADO", f"Por {interaction.user.mention} (`{interaction.user.id}`)")
        await interaction.response.send_message("🔴 **Fichaje tracking DESACTIVADO**", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Usá `/fichaje on` o `/fichaje off`", ephemeral=True)


@bot.event
async def on_error(event: str, *args, **kwargs):
    logger.error("Error no manejado en evento %s", event, exc_info=True)
    await log_actions.log_error(f"❌ Error en evento {event}", f"```py\n{args}\n{kwargs}\n```")


if __name__ == "__main__":
    if not all([TOKEN, DATABASE_URL, LOGS_CHANNEL_ID, ALERT_CHANNEL_ID]):
        logger.error("Faltan variables de entorno. Revisá el .env")
        exit(1)
    bot.run(TOKEN)
