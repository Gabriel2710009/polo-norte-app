import os
import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import database as db
from utils.validator import validate
from utils.parser import parse_embed
from cogs import fichaje
from services import log_actions
from services import aprobar
from cogs import config_aprobar_cog
from cogs import bienvenida_cog
from cogs import blacklist_cog
from cogs import help_cog

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
LOGS_CHANNEL_ID = int(os.getenv("LOGS_CHANNEL_ID", 0))
ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", 0))
FICHAJE_CHANNEL_ID = int(os.getenv("FICHAJE_CHANNEL_ID", 0))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", 0))

BLACKLIST_POSTULACIONES_ROLE_ID = int(os.getenv("BLACKLIST_POSTULACIONES_ROLE_ID", 0))
BLACKLIST_LOG_CHANNEL_ID = int(os.getenv("BLACKLIST_LOG_CHANNEL_ID", 0))
POSTULACIONES_CATEGORY_ID = int(os.getenv("POSTULACIONES_CATEGORY_ID", 0))

_raw_bypass = os.getenv("BLACKLIST_BYPASS_ROLE_IDS", "")
BLACKLIST_BYPASS_ROLE_IDS = set()
for _rid in _raw_bypass.split(","):
    _rid = _rid.strip()
    if _rid:
        BLACKLIST_BYPASS_ROLE_IDS.add(int(_rid))

BLACKLIST_ALLOW_ROLE_FALLBACK = os.getenv("BLACKLIST_ALLOW_ROLE_FALLBACK", "true").lower() == "true"

BLACKLIST_STAFF_ALERT_CHANNEL_ID = int(os.getenv("BLACKLIST_STAFF_ALERT_CHANNEL_ID", 0))
BLACKLIST_STAFF_ALERT_ROLE_ID = int(os.getenv("BLACKLIST_STAFF_ALERT_ROLE_ID", 0))

ITEMS_ACTIVO = False
FICHAJE_ACTIVO = False
TASER_DM_ACTIVO = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("Main")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="/", intents=intents)


@bot.event
async def on_ready():
    global ITEMS_ACTIVO, FICHAJE_ACTIVO, TASER_DM_ACTIVO
    if not all([TOKEN, DATABASE_URL, LOGS_CHANNEL_ID, ALERT_CHANNEL_ID]):
        logger.error("Faltan variables de entorno.")
        return

    log_actions.setup(bot, LOG_CHANNEL_ID)
    await aprobar.setup(bot)

    blacklist_cog.BLACKLIST_POSTULACIONES_ROLE_ID = BLACKLIST_POSTULACIONES_ROLE_ID
    blacklist_cog.BLACKLIST_LOG_CHANNEL_ID = BLACKLIST_LOG_CHANNEL_ID
    blacklist_cog.POSTULACIONES_CATEGORY_ID = POSTULACIONES_CATEGORY_ID
    blacklist_cog.BLACKLIST_BYPASS_ROLE_IDS = BLACKLIST_BYPASS_ROLE_IDS
    blacklist_cog.BLACKLIST_ALLOW_ROLE_FALLBACK = BLACKLIST_ALLOW_ROLE_FALLBACK
    blacklist_cog.BLACKLIST_STAFF_ALERT_CHANNEL_ID = BLACKLIST_STAFF_ALERT_CHANNEL_ID
    blacklist_cog.BLACKLIST_STAFF_ALERT_ROLE_ID = BLACKLIST_STAFF_ALERT_ROLE_ID
    await config_aprobar_cog.setup(bot)
    await bienvenida_cog.setup(bot)
    await blacklist_cog.setup(bot)
    await help_cog.setup(bot)

    try:
        db.init()
        logger.info("DB inicializada correctamente")
        log_actions.log_info("\u2705 DB inicializada", "Conexi\u00f3n a PostgreSQL establecida y tablas listas.")
    except Exception as e:
        logger.critical("Error inicializando DB: %s", e)
        await log_actions.log_error("\u274c Error DB", f"No se pudo inicializar la base de datos:\n`{e}`")
        return

    try:
        ITEMS_ACTIVO = db.get_toggle("items")
        FICHAJE_ACTIVO = db.get_toggle("fichaje")
        TASER_DM_ACTIVO = db.get_toggle("taser_dm")
        fichaje.set_taser_dm_enabled(TASER_DM_ACTIVO)
        logger.info("Toggles cargados: items=%s fichaje=%s taser_dm=%s", ITEMS_ACTIVO, FICHAJE_ACTIVO, TASER_DM_ACTIVO)
    except Exception as e:
        logger.warning("Error cargando toggles, usando defaults: %s", e)
        await log_actions.log_warning("\u26a0\ufe0f Toggles defaults", f"No se pudieron cargar desde DB:\n`{e}`")

    try:
        pendientes = db.get_pending_alerts()
        if pendientes:
            logger.info("Alertas pendientes encontradas: %s", len(pendientes))
            log_actions.log_info("\u23f3 Alertas pendientes", f"Se encontraron {len(pendientes)} alertas de t\u00e1ser sin resolver del turno anterior.")
            await fichaje.verificar_pendientes_al_inicio(bot, ALERT_CHANNEL_ID)
    except Exception as e:
        logger.error("Error verificando pendientes: %s", e)
        await log_actions.log_error("\u274c Error pendientes", f"No se pudieron verificar alertas pendientes:\n`{e}`")

    try:
        await bot.tree.sync()
        logger.info("Comandos slash sincronizados")
    except Exception as e:
        logger.error("Error sincronizando comandos: %s", e)
        await log_actions.log_error("\u274c Error sync", f"No se pudieron sincronizar los comandos slash:\n`{e}`")

    try:
        fichaje.iniciar_recordatorio_loop(bot, ALERT_CHANNEL_ID)
        logger.info("Recordatorio loop iniciado")
    except Exception as e:
        logger.error("Error iniciando recordatorio loop: %s", e)
        await log_actions.log_error("\u274c Error recordatorio loop", f"No se pudo iniciar:\n`{e}`")

    logger.info("Bot conectado como %s (%s)", bot.user, bot.user.id)
    log_actions.log_info("\U0001f7e2 Bot iniciado", f"Conectado como {bot.user} ({bot.user.id})")


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

        if message.channel.id == LOGS_CHANNEL_ID:
            parsed = parse_embed(text)

            if FICHAJE_ACTIVO and parsed:
                if parsed.get("action") == "RETRIEVE":
                    for item in parsed.get("items", []):
                        if fichaje._is_taser(item.get("name", "")):
                            tenia_fichaje = db.mark_taser_retirado_activo(parsed.get("discord_id"))
                            if tenia_fichaje:
                                logger.info("Taser retirado vinculado a fichaje activo: user=%s", parsed.get("discord_id"))
                            else:
                                asyncio.create_task(
                                    fichaje._esperar_fichaje_para_taser(
                                        bot, parsed.get("discord_id"),
                                        item["name"], ALERT_CHANNEL_ID
                                    )
                                )
                                logger.info("Taser retirado sin fichaje - esperando %s min: user=%s",
                                            fichaje.RETIRO_SIN_FICHAJE_WAIT_MINUTES, parsed.get("discord_id"))

                taser_devuelto = fichaje.procesar_stash_para_taser(parsed)
                if taser_devuelto:
                    channel = bot.get_channel(ALERT_CHANNEL_ID)
                    if channel:
                        user_id = taser_devuelto["user_id"]
                        player = parsed.get("player") or ""
                        titulo = "\u2705 TASER DEVUELTO - ALERTA RESUELTA" if taser_devuelto["tuvo_alerta"] else "\u2705 TASER DEVUELTO"
                        desc = (
                            f"El t\u00e1ser fue devuelto y los DMs fueron desactivados autom\u00e1ticamente."
                            if taser_devuelto["tuvo_alerta"]
                            else f"T\u00e1ser devuelto correctamente."
                        )
                        constancia = discord.Embed(
                            title=titulo,
                            color=discord.Color.green(),
                            timestamp=discord.utils.utcnow(),
                        )
                        constancia.add_field(name="\U0001f464 Usuario", value=f"{player}\n<@{user_id}>", inline=True)
                        constancia.add_field(name="\U0001f4e6 Item", value=f"`{taser_devuelto['item_name']}`", inline=True)
                        constancia.add_field(name="\U0001f4cb Detalle", value=desc, inline=False)
                        await channel.send(embed=constancia)

            if ITEMS_ACTIVO:
                result = validate(text)
                if "ALERT: true" in result:
                    channel = bot.get_channel(ALERT_CHANNEL_ID)
                    if not channel:
                        continue

                    lines = []
                    for i in parsed.get("items", []):
                        lines.append(f"\u2022 `{i['name']}` x{i['quantity']}")

                    alert = discord.Embed(
                        title="\U0001f6a8 \u00cdtem ILEGAL EN STASH",
                        color=discord.Color.red(),
                        timestamp=discord.utils.utcnow(),
                    )
                    alert.add_field(name="\U0001f464 Jugador", value=parsed.get("player", "N/A"), inline=True)
                    alert.add_field(name="\U0001f3ae Steam", value=parsed.get("identifier", "N/A"), inline=True)
                    alert.add_field(name="\U0001f4ac Discord", value=f"<@{parsed.get('discord_id')}>", inline=True)
                    alert.add_field(name="\U0001f4e6 Items", value="\n".join(lines) or "N/A", inline=False)
                    alert.add_field(name="\U0001f522 Stash ID", value=parsed.get("stash_id", "N/A"), inline=True)
                    alert.add_field(name="\U0001f4cd Coords", value=parsed.get("coords", "N/A"), inline=True)
                    alert.add_field(name="\U0001f517 Log original", value=f"[Ver mensaje]({message.jump_url})", inline=False)

                    await channel.send(embed=alert)
                    logger.info("Constancia de item ilegal: %s - %s", parsed.get("player"), ", ".join(lines))
                    log_actions.log_warning(
                        "\U0001f6a8 Item ilegal en stash",
                        f"**Jugador:** {parsed.get('player')}\n"
                        f"**Items:** {', '.join(lines)}\n"
                        f"**Steam:** {parsed.get('identifier')}\n"
                        f"[Ver log]({message.jump_url})"
                    )

        if FICHAJE_ACTIVO and message.channel.id == FICHAJE_CHANNEL_ID:
            try:
                data = fichaje.parse_fichaje_embed(embed)
                if not data:
                    continue
                if data["tipo"] == "INICIO":
                    await fichaje.handle_clock_in(bot, embed, LOGS_CHANNEL_ID)
                    logger.info("Clock-in registrado: %s", data["user_id"])
                    log_actions.log_info("\U0001f7e2 Clock-in", f"Usuario <@{data['user_id']}> inici\u00f3 turno.")
                elif data["tipo"] == "CIERRE":
                    await fichaje.handle_clock_out(bot, embed, LOGS_CHANNEL_ID, ALERT_CHANNEL_ID, TASER_DM_ACTIVO)
                    logger.info("Clock-out registrado: %s", data["user_id"])
                    log_actions.log_info("\U0001f534 Clock-out", f"Usuario <@{data['user_id']}> cerr\u00f3 turno.")
            except Exception as e:
                logger.error("Error procesando fichaje: %s", e)
                await log_actions.log_error("\u274c Error fichaje", f"Error procesando embed de fichaje:\n`{e}`")


@bot.tree.command(name="items", description="Activa o desactiva la constancia de items ilegales en stash")
@app_commands.describe(estado="on para activar, off para desactivar")
async def items_toggle(interaction: discord.Interaction, estado: str = None):
    global ITEMS_ACTIVO

    if estado is None:
        estado_actual = "\U0001f7e2 ACTIVO" if ITEMS_ACTIVO else "\U0001f534 INACTIVO"
        await interaction.response.send_message(
            f"\U0001f4cb Estado de items ilegales: {estado_actual}\nUs\u00e1 `/items on` o `/items off` para cambiar.",
            ephemeral=True,
        )
        return

    if estado.lower() in ("on", "1", "true", "si"):
        ITEMS_ACTIVO = True
        db.set_toggle("items", True)
        logger.info("Items ilegales activado por %s", interaction.user)
        log_actions.log_info("\U0001f7e2 Items ACTIVADO", f"Por {interaction.user.mention} (`{interaction.user.id}`)")
        await interaction.response.send_message("\U0001f7e2 **Constancia de items ilegales ACTIVADA**\nSe dejar\u00e1 constancia de items ilegales en stash.", ephemeral=True)
    elif estado.lower() in ("off", "0", "false", "no"):
        ITEMS_ACTIVO = False
        db.set_toggle("items", False)
        logger.info("Items ilegales desactivado por %s", interaction.user)
        log_actions.log_info("\U0001f534 Items DESACTIVADO", f"Por {interaction.user.mention} (`{interaction.user.id}`)")
        await interaction.response.send_message("\U0001f534 **Constancia de items ilegales DESACTIVADA**", ephemeral=True)
    else:
        await interaction.response.send_message("\u274c Us\u00e1 `/items on` o `/items off`", ephemeral=True)


@bot.tree.command(name="fichaje", description="Activa o desactiva el tracking de fichajes y t\u00e1seres")
@app_commands.describe(estado="on para activar, off para desactivar")
async def fichaje_toggle(interaction: discord.Interaction, estado: str = None):
    global FICHAJE_ACTIVO

    if not FICHAJE_CHANNEL_ID:
        await interaction.response.send_message("\u274c `FICHAJE_CHANNEL_ID` no configurado en el .env", ephemeral=True)
        return

    if estado is None:
        estado_actual = "\U0001f7e2 ACTIVO" if FICHAJE_ACTIVO else "\U0001f534 INACTIVO"
        await interaction.response.send_message(
            f"\U0001f4cb Estado del fichaje: {estado_actual}\nUs\u00e1 `/fichaje on` o `/fichaje off` para cambiar.",
            ephemeral=True,
        )
        return

    if estado.lower() in ("on", "1", "true", "si"):
        FICHAJE_ACTIVO = True
        db.set_toggle("fichaje", True)
        logger.info("Fichaje tracking activado por %s", interaction.user)
        log_actions.log_info("\U0001f7e2 Fichaje ACTIVADO", f"Por {interaction.user.mention} (`{interaction.user.id}`)")
        await interaction.response.send_message("\U0001f7e2 **Fichaje tracking ACTIVADO**\nSe est\u00e1n monitoreando los fichajes y el retorno de t\u00e1seres.", ephemeral=True)
    elif estado.lower() in ("off", "0", "false", "no"):
        FICHAJE_ACTIVO = False
        db.set_toggle("fichaje", False)
        logger.info("Fichaje tracking desactivado por %s", interaction.user)
        log_actions.log_info("\U0001f534 Fichaje DESACTIVADO", f"Por {interaction.user.mention} (`{interaction.user.id}`)")
        await interaction.response.send_message("\U0001f534 **Fichaje tracking DESACTIVADO**", ephemeral=True)
    else:
        await interaction.response.send_message("\u274c Us\u00e1 `/fichaje on` o `/fichaje off`", ephemeral=True)


@bot.tree.command(name="taser-dm", description="Activa o desactiva los mensajes al DM por t\u00e1ser no devuelto")
@app_commands.describe(estado="on para activar, off para desactivar")
async def taser_dm_toggle(interaction: discord.Interaction, estado: str = None):
    global TASER_DM_ACTIVO

    if estado is None:
        estado_actual = "\U0001f7e2 ACTIVOS" if TASER_DM_ACTIVO else "\U0001f534 INACTIVOS"
        await interaction.response.send_message(
            f"\U0001f4cb DM por t\u00e1ser: {estado_actual}\nUs\u00e1 `/taser-dm on` o `/taser-dm off` para cambiar.",
            ephemeral=True,
        )
        return

    if estado.lower() in ("on", "1", "true", "si"):
        TASER_DM_ACTIVO = True
        fichaje.set_taser_dm_enabled(True)
        db.set_toggle("taser_dm", True)
        logger.info("DM de t\u00e1ser activado por %s", interaction.user)
        log_actions.log_info("\U0001f7e2 Taser-DM ACTIVADO", f"Por {interaction.user.mention} (`{interaction.user.id}`)")
        await interaction.response.send_message("\U0001f7e2 **DM de t\u00e1ser ACTIVADO**\nSe enviar\u00e1 DM a quienes no devuelvan el t\u00e1ser.", ephemeral=True)
    elif estado.lower() in ("off", "0", "false", "no"):
        TASER_DM_ACTIVO = False
        fichaje.set_taser_dm_enabled(False)
        db.set_toggle("taser_dm", False)
        logger.info("DM de t\u00e1ser desactivado por %s", interaction.user)
        log_actions.log_info("\U0001f534 Taser-DM DESACTIVADO", f"Por {interaction.user.mention} (`{interaction.user.id}`)")
        await interaction.response.send_message("\U0001f534 **DM de t\u00e1ser DESACTIVADO**\nNo se enviar\u00e1n mensajes directos.", ephemeral=True)
    else:
        await interaction.response.send_message("\u274c Us\u00e1 `/taser-dm on` o `/taser-dm off`", ephemeral=True)


@bot.tree.command(name="estado", description="Muestra el estado actual de items, fichaje y DM de t\u00e1ser")
async def estado(interaction: discord.Interaction):
    items_icon = "\U0001f7e2" if ITEMS_ACTIVO else "\U0001f534"
    fichaje_icon = "\U0001f7e2" if FICHAJE_ACTIVO else "\U0001f534"
    taser_icon = "\U0001f7e2" if TASER_DM_ACTIVO else "\U0001f534"
    embed = discord.Embed(
        title="\U0001f4ca Estado del Bot",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name=f"{items_icon} Items ilegales", value="ACTIVO" if ITEMS_ACTIVO else "INACTIVO", inline=True)
    embed.add_field(name=f"{fichaje_icon} Fichaje + t\u00e1ser", value="ACTIVO" if FICHAJE_ACTIVO else "INACTIVO", inline=True)
    embed.add_field(name=f"{taser_icon} DM t\u00e1ser", value="ACTIVO" if TASER_DM_ACTIVO else "INACTIVO", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.event
async def on_error(event: str, *args, **kwargs):
    logger.error("Error no manejado en evento %s", event, exc_info=True)
    await log_actions.log_error(f"\u274c Error en evento {event}", f"```py\n{args}\n{kwargs}\n```")


if __name__ == "__main__":
    if not all([TOKEN, DATABASE_URL, LOGS_CHANNEL_ID, ALERT_CHANNEL_ID]):
        logger.error("Faltan variables de entorno. Revis\u00e1 el .env")
        exit(1)
    bot.run(TOKEN)
