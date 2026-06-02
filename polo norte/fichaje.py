import re
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord
import database as db
import log_actions
from parser import parse_embed

TASER_ITEMS = {"weapon_stungun", "stungun", "taser", "weapon_taser"}
CHECK_WINDOW_MINUTES = 10

logger = logging.getLogger("Fichaje")


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

    if "iniciado" in title.lower() or "🟢" in title:
        tipo = "INICIO"
    elif "cerrado" in title.lower() or "🔴" in title:
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
        logger.error("Error revisando stash history para %s: %s", user_id, exc)
        await log_actions.log_error("❌ Error historial stash", f"Usuario <@{user_id}>\n`{exc}`")
    return False


async def handle_clock_in(bot, embed, logs_channel_id: int):
    try:
        data = parse_fichaje_embed(embed)
        if not data:
            return

        record_id = db.insert_clock_in(data["user_id"], data["username"])
        logger.info("Clock-in registrado: %s (id=%s)", data["user_id"], record_id)
        log_actions.log_info("🟢 Clock-in registrado", f"<@{data['user_id']}> inició turno (ID {record_id}).")

        taser = await check_taser_retirado_al_inicio(bot, data["user_id"], logs_channel_id)
        if taser:
            db.set_taser_retirado(record_id)
            logger.info("Taser retirado marcado para %s (record=%s)", data["user_id"], record_id)
            log_actions.log_warning(
                "🔫 Táser retirado detectado",
                f"<@{data['user_id']}> retiró un táser antes de su turno. Se controlará su devolución al cierre."
            )
    except Exception as e:
        logger.error("Error en handle_clock_in: %s", e)
        await log_actions.log_error("❌ Error clock-in", f"`{e}`")


async def handle_clock_out(bot, embed, logs_channel_id: int, alert_channel_id: int):
    try:
        data = parse_fichaje_embed(embed)
        if not data:
            return

        record_id = db.close_clock_in(data["user_id"])
        if record_id is None:
            logger.warning("Clock-out sin matching clock-in: %s", data["user_id"])
            log_actions.log_warning("⚠️ Clock-out sin registro", f"<@{data['user_id']}> cerró turno pero no se encontró un inicio activo.")
            return

        logger.info("Clock-out registrado: %s (record=%s)", data["user_id"], record_id)
        log_actions.log_info("🔴 Clock-out registrado", f"<@{data['user_id']}> cerró turno (ID {record_id}).")

        asyncio.create_task(
            _esperar_y_verificar(bot, data["user_id"], record_id, alert_channel_id)
        )
    except Exception as e:
        logger.error("Error en handle_clock_out: %s", e)
        await log_actions.log_error("❌ Error clock-out", f"`{e}`")


async def _esperar_y_verificar(bot, user_id, record_id, alert_channel_id):
    await asyncio.sleep(CHECK_WINDOW_MINUTES * 60)

    try:
        conn = db.get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT taser_retirado, taser_devuelto, alerta_enviada, username FROM fichaje_registros WHERE id = %s",
            (record_id,)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            logger.warning("Record %s no encontrado en verificación", record_id)
            return
        if not row[0]:
            logger.info("Record %s: no retiró táser, sin acción necesaria.", record_id)
            return
        if row[1]:
            logger.info("Record %s: táser devuelto correctamente.", record_id)
            return
        if row[2]:
            logger.info("Record %s: alerta ya enviada.", record_id)
            return

        db.mark_alerta_enviada(record_id)
        logger.warning("Táser NO devuelto para %s (record=%s)", user_id, record_id)
        log_actions.log_warning(
            "⚠️ Táser NO devuelto",
            f"<@{user_id}> no devolvió el táser tras {CHECK_WINDOW_MINUTES} min del clock-out."
        )

        channel = bot.get_channel(alert_channel_id)
        if channel:
            constancia = discord.Embed(
                title="⚠️ TASER NO DEVUELTO",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
            constancia.add_field(name="👤 Usuario", value=f"<@{user_id}>", inline=True)
            constancia.add_field(name="📋 Estado", value=f"Retiró un táser y no lo devolvió dentro de los {CHECK_WINDOW_MINUTES} minutos posteriores al fichaje de salida.", inline=False)
            await channel.send(embed=constancia)
            logger.info("Constancia enviada a canal %s", alert_channel_id)

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
                    "⚠️ **TASER NO DEVUELTO**\n\n"
                    "Nuestro sistema detectó que retiraste un táser durante tu turno "
                    f"y no lo devolviste dentro de los {CHECK_WINDOW_MINUTES} minutos posteriores al fichaje de salida.\n"
                    "Por favor, devolvelo a la armería lo antes posible."
                )
                logger.info("DM enviado a %s por táser no devuelto", user_id)
        except Exception as exc:
            logger.warning("No se pudo enviar DM a %s: %s", user_id, exc)
            log_actions.log_warning("⚠️ DM fallido", f"No se pudo enviar DM a <@{user_id}>:\n`{exc}`")

    except Exception as e:
        logger.error("Error en verificación de táser para %s: %s", user_id, e)
        await log_actions.log_error("❌ Error verificación táser", f"Usuario <@{user_id}>\n`{e}`")


def procesar_stash_para_taser(embed_data: dict) -> str | None:
    try:
        if embed_data.get("action") != "STASH":
            return None
        for item in embed_data.get("items", []):
            if _is_taser(item.get("name", "")):
                user_id = embed_data.get("discord_id")
                if user_id:
                    db.set_taser_devuelto(user_id)
                    item_name = item.get("name", "")
                    logger.info("Taser devuelto por %s (stash detectado)", user_id)
                    return item_name
    except Exception as e:
        logger.error("Error en procesar_stash_para_taser: %s", e)
    return None


async def verificar_pendientes_al_inicio(bot, alert_channel_id: int):
    try:
        pendientes = db.get_pending_alerts()
        if not pendientes:
            logger.info("No hay alertas pendientes de táser.")
            return
        logger.info("Procesando %s alertas pendientes...", len(pendientes))
        for row in pendientes:
            record_id, user_id = row[0], row[1]
            logger.info("Pendiente: %s (record=%s)", user_id, record_id)
            channel = bot.get_channel(alert_channel_id)
            if channel:
                constancia = discord.Embed(
                    title="⚠️ TASER NO DEVUELTO (PENDIENTE)",
                    color=discord.Color.orange(),
                )
                constancia.add_field(name="👤 Usuario", value=f"<@{user_id}>", inline=True)
                constancia.add_field(name="📋 Estado", value="Retiró un táser en un turno anterior y no lo devolvió.", inline=False)
                await channel.send(embed=constancia)
                log_actions.log_warning("⏳ Alerta pendiente reenviada", f"<@{user_id}> - táser no devuelto de turno anterior.")
    except Exception as e:
        logger.error("Error en verificar_pendientes_al_inicio: %s", e)
        await log_actions.log_error("❌ Error pendientes inicio", f"`{e}`")
