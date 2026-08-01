import re
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from discord.ext import tasks

import discord
import database as db
from services import log_actions
from utils.parser import parse_embed

TASER_ITEMS = {"weapon_stungun", "stungun", "taser", "weapon_taser"}
CHECK_WINDOW_MINUTES = 10
TASER_DM_ENABLED = True

logger = logging.getLogger("Fichaje")

_pending_checks: dict[str, asyncio.Event] = {}
_player_names: dict[str, str] = {}


def _get_name(user_id: str) -> str:
    name = _player_names.get(user_id)
    if name:
        return name
    name = db.get_username(user_id)
    if name:
        _player_names[user_id] = name
        return name
    return f"<@{user_id}>"


def parse_fichaje_embed(embed) -> dict | None:
    title = (embed.title or "").strip()
    footer_text = embed.footer.text if embed.footer else ""

    m = re.search(r"ID:\s*(\d{17,20})", footer_text)
    if not m:
        return None
    user_id = m.group(1)

    username = ""
    for field in embed.fields:
        if "usuario" in (field.name or "").lower():
            username = field.value or ""

    if "iniciado" in title.lower() or "\U0001f7e2" in title:
        tipo = "INICIO"
    elif "cerrado" in title.lower() or "\U0001f534" in title:
        tipo = "CIERRE"
    else:
        return None

    return {"tipo": tipo, "user_id": user_id, "username": username}


def _is_taser(item_name: str) -> bool:
    return item_name.strip().lower().replace(" ", "_").replace("-", "_") in TASER_ITEMS


async def check_taser_retirado_al_inicio(bot, user_id: str, logs_channel_id: int):
    channel = bot.get_channel(logs_channel_id)
    if not channel:
        return False

    since = datetime.now(timezone.utc) - timedelta(minutes=30)
    try:
        async for msg in channel.history(limit=200, after=since):
            for e in msg.embeds:
                text = f"{e.title or ''}\n{e.description or ''}"
                parsed = parse_embed(text)
                if parsed.get("action") != "RETRIEVE":
                    continue
                if str(parsed.get("discord_id")) != user_id:
                    continue
                for item in parsed.get("items", []):
                    if _is_taser(item.get("name", "")):
                        logger.info("Taser retirado encontrado en historial para %s", user_id)
                        return True
    except Exception as exc:
        logger.warning("Error revisando stash history para %s: %s", user_id, exc)
        await log_actions.log_error("\u274c Error historial stash", f"Usuario <@{user_id}>\n`{exc}`")
    return False


async def handle_clock_in(bot, embed, logs_channel_id: int):
    try:
        data = parse_fichaje_embed(embed)
        if not data:
            return

        if data["username"]:
            _player_names[data["user_id"]] = data["username"]

        record_id = db.insert_clock_in(data["user_id"], data["username"])
        nombre = _get_name(data["user_id"])
        logger.info("Clock-in registrado: %s (id=%s)", data["user_id"], record_id)
        log_actions.log_info("\U0001f7e2 Clock-in registrado", f"{nombre} (<@{data['user_id']}>) inici\u00f3 turno (ID {record_id}).")

        taser = await check_taser_retirado_al_inicio(bot, data["user_id"], logs_channel_id)
        if taser:
            db.set_taser_retirado(record_id)
            logger.info("Taser retirado marcado para %s (record=%s)", data["user_id"], record_id)
            log_actions.log_warning(
                "\U0001f52b T\u00e1ser retirado detectado",
                f"{nombre} (<@{data['user_id']}>) retir\u00f3 un t\u00e1ser antes de su turno. Se controlar\u00e1 su devoluci\u00f3n al cierre."
            )
    except Exception as e:
        logger.warning("Error en handle_clock_in: %s", e)
        await log_actions.log_error("\u274c Error clock-in", f"`{e}`")


async def handle_clock_out(bot, embed, logs_channel_id: int, alert_channel_id: int, taser_dm_activo: bool = True):
    try:
        data = parse_fichaje_embed(embed)
        if not data:
            return

        record_id = db.close_clock_in(data["user_id"])
        if record_id is None:
            logger.warning("Clock-out sin matching clock-in: %s", data["user_id"])
            log_actions.log_warning("\u26a0\ufe0f Clock-out sin registro", f"<@{data['user_id']}> cerr\u00f3 turno pero no se encontr\u00f3 un inicio activo.")
            return

        nombre = _get_name(data["user_id"])
        logger.info("Clock-out registrado: %s (record=%s)", data["user_id"], record_id)
        log_actions.log_info("\U0001f534 Clock-out registrado", f"{nombre} (<@{data['user_id']}>) cerr\u00f3 turno (ID {record_id}).")

        evt = asyncio.Event()
        _pending_checks[data["user_id"]] = evt
        asyncio.create_task(
            _esperar_y_verificar(bot, data["user_id"], record_id, alert_channel_id, taser_dm_activo, evt)
        )
    except Exception as e:
        logger.warning("Error en handle_clock_out: %s", e)
        await log_actions.log_error("\u274c Error clock-out", f"`{e}`")


async def _esperar_y_verificar(bot, user_id, record_id, alert_channel_id, taser_dm_activo: bool = True, evt: asyncio.Event = None):
    try:
        await asyncio.wait_for(evt.wait(), timeout=CHECK_WINDOW_MINUTES * 60)
        logger.info("Taser devuelto durante la espera para %s (record=%s)", user_id, record_id)
        return
    except asyncio.TimeoutError:
        pass
    finally:
        _pending_checks.pop(user_id, None)

    try:
        conn = db.get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT taser_retirado, taser_devuelto, alerta_enviada, username FROM fichaje_registros WHERE id = %s",
            (record_id,)
        )
        row = cur.fetchone()
        cur.close()
        db.close_conn(conn)

        if not row:
            logger.warning("Record %s no encontrado en verificaci\u00f3n", record_id)
            return
        if not row[0]:
            logger.info("Record %s: no retir\u00f3 t\u00e1ser, sin acci\u00f3n necesaria.", record_id)
            return
        if row[1]:
            logger.info("Record %s: t\u00e1ser devuelto correctamente.", record_id)
            return
        if row[2]:
            logger.info("Record %s: alerta ya enviada.", record_id)
            return

        nombre = row[3] or f"<@{user_id}>"

        db.mark_alerta_enviada(record_id)
        logger.warning("T\u00e1ser NO devuelto para %s (record=%s)", user_id, record_id)
        log_actions.log_warning(
            "\u26a0\ufe0f T\u00e1ser NO devuelto",
            f"{nombre} (<@{user_id}>) no devolvi\u00f3 el t\u00e1ser tras {CHECK_WINDOW_MINUTES} min del clock-out."
        )

        channel = bot.get_channel(alert_channel_id)
        if channel:
            constancia = discord.Embed(
                title="\u26a0\ufe0f TASER NO DEVUELTO",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
            constancia.add_field(name="\U0001f464 Usuario", value=f"{nombre}\n<@{user_id}>", inline=True)
            constancia.add_field(name="\U0001f4cb Estado", value=f"Retir\u00f3 un t\u00e1ser y no lo devolvi\u00f3 dentro de los {CHECK_WINDOW_MINUTES} minutos posteriores al fichaje de salida.", inline=False)
            await channel.send(embed=constancia)
            logger.info("Constancia enviada a canal %s", alert_channel_id)

        if taser_dm_activo:
            try:
                member = None
                for guild in bot.guilds:
                    try:
                        member = await guild.fetch_member(int(user_id))
                        if member:
                            break
                    except Exception:
                        continue
                if member:
                    await member.send(
                        "\u26a0\ufe0f **TASER NO DEVUELTO**\n\n"
                        "Nuestro sistema detect\u00f3 que retiraste un t\u00e1ser durante tu turno "
                        f"y no lo devolviste dentro de los {CHECK_WINDOW_MINUTES} minutos posteriores al fichaje de salida.\n"
                        "Por favor, devolvelo a la armer\u00eda lo antes posible.\n\n"
                        "\u23f0 *Recibir\u00e1s un recordatorio cada 24 horas hasta que lo devuelvas.*"
                    )
                    logger.info("DM enviado a %s por t\u00e1ser no devuelto", user_id)
                    db.set_ultimo_dm(record_id)
            except Exception as exc:
                logger.warning("No se pudo enviar DM a %s: %s", user_id, exc)
                log_actions.log_warning("\u26a0\ufe0f DM fallido", f"No se pudo enviar DM a <@{user_id}>:\n`{exc}`")

    except Exception as e:
        logger.warning("Error en verificaci\u00f3n de t\u00e1ser para %s: %s", user_id, e)
        await log_actions.log_error("\u274c Error verificaci\u00f3n t\u00e1ser", f"Usuario <@{user_id}>\n`{e}`")


def procesar_stash_para_taser(embed_data: dict) -> dict | None:
    try:
        if embed_data.get("action") != "STASH":
            return None
        for item in embed_data.get("items", []):
            if _is_taser(item.get("name", "")):
                user_id = embed_data.get("discord_id")
                if user_id:
                    player = embed_data.get("player")
                    if player:
                        _player_names[user_id] = player

                    result = db.set_taser_devuelto(user_id)

                    ev = _pending_checks.pop(user_id, None)
                    if ev:
                        ev.set()

                    if result:
                        logger.info(
                            "Taser devuelto por %s (stash detectado, record=%s, alerta_previa=%s)",
                            user_id, result["record_id"], result["tuvo_alerta"],
                        )
                        return {
                            "item_name": item.get("name", ""),
                            "user_id": user_id,
                            "record_id": result["record_id"],
                            "tuvo_alerta": result["tuvo_alerta"],
                        }
    except Exception as e:
        logger.warning("Error en procesar_stash_para_taser: %s", e)
    return None


RETIRO_SIN_FICHAJE_WAIT_MINUTES = 5


async def _esperar_fichaje_para_taser(bot, user_id, item_name, alert_channel_id):
    await asyncio.sleep(RETIRO_SIN_FICHAJE_WAIT_MINUTES * 60)
    try:
        conn = db.get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM fichaje_registros WHERE user_id = %s AND clock_out_at IS NULL LIMIT 1",
            (user_id,)
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE fichaje_registros SET taser_retirado = TRUE WHERE id = %s AND taser_retirado = FALSE",
                (row[0],)
            )
            conn.commit()
            if cur.rowcount:
                logger.info("Taser vinculado retrospectivamente a fichaje: user=%s", user_id)
            cur.close()
            db.close_conn(conn)
            return

        cur.close()
        db.close_conn(conn)

        nombre = _get_name(user_id)
        channel = bot.get_channel(alert_channel_id)
        if channel:
            constancia = discord.Embed(
                title="\u26a0\ufe0f TASER RETIRADO SIN FICHAJE",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow(),
            )
            constancia.add_field(name="\U0001f464 Usuario", value=f"{nombre}\n<@{user_id}>", inline=True)
            constancia.add_field(name="\U0001f52b Item", value=f"`{item_name}`", inline=True)
            constancia.add_field(name="\u23f1 Espera", value=f"Pasaron {RETIRO_SIN_FICHAJE_WAIT_MINUTES} minutos desde el retiro sin que inicie fichaje.", inline=False)
            await channel.send(embed=constancia)
            logger.warning("Taser retirado sin fichaje tras %s min: user=%s", RETIRO_SIN_FICHAJE_WAIT_MINUTES, user_id)
            log_actions.log_warning(
                "\u26a0\ufe0f T\u00e1ser sin fichaje",
                f"{nombre} (<@{user_id}>) retir\u00f3 `{item_name}` y no inici\u00f3 fichaje en {RETIRO_SIN_FICHAJE_WAIT_MINUTES} min."
            )
    except Exception as e:
        logger.warning("Error en _esperar_fichaje_para_taser: %s", e)
        await log_actions.log_error("\u274c Error espera fichaje", f"Usuario <@{user_id}>\n`{e}`")


async def verificar_pendientes_al_inicio(bot, alert_channel_id: int):
    try:
        pendientes = db.get_pending_alerts()
        if not pendientes:
            logger.info("No hay alertas pendientes de t\u00e1ser.")
            return
        logger.info("Procesando %s alertas pendientes...", len(pendientes))
        for row in pendientes:
            record_id, user_id = row[0], row[1]
            nombre = _get_name(user_id)
            logger.info("Pendiente: %s (record=%s)", user_id, record_id)
            channel = bot.get_channel(alert_channel_id)
            if channel:
                constancia = discord.Embed(
                    title="\u26a0\ufe0f TASER NO DEVUELTO (PENDIENTE)",
                    color=discord.Color.orange(),
                )
                constancia.add_field(name="\U0001f464 Usuario", value=f"{nombre}\n<@{user_id}>", inline=True)
                constancia.add_field(name="\U0001f4cb Estado", value="Retir\u00f3 un t\u00e1ser en un turno anterior y no lo devolvi\u00f3.", inline=False)
                await channel.send(embed=constancia)
                db.mark_alerta_enviada(record_id)
                log_actions.log_warning("\u23f3 Alerta pendiente reenviada", f"{nombre} (<@{user_id}>) - t\u00e1ser no devuelto de turno anterior.")
    except Exception as e:
        logger.warning("Error en verificar_pendientes_al_inicio: %s", e)
        await log_actions.log_error("\u274c Error pendientes inicio", f"`{e}`")


@tasks.loop(hours=1)
async def recordatorio_loop(bot, alert_channel_id):
    logger.debug("Ejecutando recordatorio_loop...")
    try:
        records = db.get_records_para_recordatorio()
        for record_id, user_id in records:
            nombre = _get_name(user_id)
            logger.info("Enviando recordatorio 24h para %s (record=%s)", user_id, record_id)

            channel = bot.get_channel(alert_channel_id)
            if channel:
                constancia = discord.Embed(
                    title="\U0001f501 RECORDATORIO T\u00c1SER NO DEVUELTO",
                    color=discord.Color.orange(),
                    timestamp=discord.utils.utcnow(),
                )
                constancia.add_field(name="\U0001f464 Usuario", value=f"{nombre}\n<@{user_id}>", inline=True)
                constancia.add_field(name="\u23f0 Recordatorio", value="Sigue sin devolver el t\u00e1ser tras 24+ horas.", inline=False)
                await channel.send(embed=constancia)
                log_actions.log_warning("\U0001f501 Recordatorio t\u00e1ser", f"{nombre} (<@{user_id}>) - sin devoluci\u00f3n tras 24h.")

            if TASER_DM_ENABLED:
                member = None
                for guild in bot.guilds:
                    try:
                        member = await guild.fetch_member(int(user_id))
                        if member:
                            break
                    except Exception:
                        continue
                if member:
                    try:
                        await member.send(
                            "\U0001f501 **RECORDATORIO - T\u00c1SER NO DEVUELTO**\n\n"
                            "Nuestro sistema detect\u00f3 que a\u00fan no has devuelto el t\u00e1ser que retiraste.\n"
                            "Por favor, devolvelo a la armer\u00eda lo antes posible para evitar sanciones.\n\n"
                            "\u23f0 *Recibir\u00e1s este recordatorio cada 24 horas hasta que lo devuelvas.*"
                        )
                        logger.info("Recordatorio DM enviado a %s", user_id)
                    except Exception as exc:
                        logger.warning("No se pudo enviar recordatorio DM a %s: %s", user_id, exc)

            db.set_ultimo_dm(record_id)
    except Exception as e:
        logger.warning("Error en recordatorio_loop: %s", e)
        await log_actions.log_error("\u274c Error recordatorio loop", f"`{e}`")


def iniciar_recordatorio_loop(bot, alert_channel_id):
    if not recordatorio_loop.is_running():
        recordatorio_loop.start(bot, alert_channel_id)
        logger.info("Recordatorio loop iniciado (cada 1 hora).")


def set_taser_dm_enabled(val: bool):
    global TASER_DM_ENABLED
    TASER_DM_ENABLED = val
    logger.info("TASER_DM_ENABLED = %s", val)
