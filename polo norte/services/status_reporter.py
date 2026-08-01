import time
import asyncio
import logging
import discord

import database as db
from services import log_actions

logger = logging.getLogger("StatusReporter")

ADMIN_USER_ID = 691475896019714139
REPORT_INTERVAL_MINUTES = 30

_bot = None
_admin_user_id = ADMIN_USER_ID
_start_time = None
_last_heartbeat = None
_error_count = 0
_warn_count = 0
_disconnect_count = 0
_hubo_error_critico = False
_recent_errors = []
_RECENT_ERRORS_MAX = 10
_report_interval = REPORT_INTERVAL_MINUTES * 60
_loop_started = False
_startup_sent = False


def _es_critico_tipo(error) -> bool:
    try:
        if isinstance(error, (db.OperationalError, discord.ConnectionClosed, discord.GatewayNotFound)):
            return True
    except Exception:
        pass
    return False


def _resumir_error(error, contexto=None):
    try:
        detalle = str(error).strip() or type(error).__name__
    except Exception:
        detalle = type(error).__name__
    resumen = f"{contexto}: {detalle}" if contexto else detalle
    if len(resumen) > 200:
        resumen = resumen[:197] + "..."
    return resumen


def setup(bot):
    global _bot, _report_interval, _start_time, _loop_started
    _bot = bot
    _report_interval = REPORT_INTERVAL_MINUTES * 60
    _start_time = time.time()
    if not _loop_started:
        _loop_started = True
        bot.loop.set_exception_handler(_async_exception_handler)
        bot.loop.create_task(_periodic_loop())
    logger.info(
        "StatusReporter configurado. Intervalo: %s min, Admin: %s",
        REPORT_INTERVAL_MINUTES, _admin_user_id,
    )


def _async_exception_handler(loop, context):
    exc = context.get("exception")
    mensaje = context.get("message", "Error desconocido en task asyncio")
    if exc:
        logger.error("Excepción asyncio no manejada: %s", mensaje, exc_info=exc)
        report_error(exc, contexto="Task/background", es_critico=True)
    else:
        logger.error("Error asyncio: %s", mensaje)
        report_error(Exception(mensaje), contexto="asyncio", es_critico=True)


def report_error(error, contexto=None, es_critico=False):
    global _error_count, _hubo_error_critico
    if error is not None and not es_critico:
        es_critico = _es_critico_tipo(error)
    _error_count += 1
    if es_critico:
        _hubo_error_critico = True
    resumen = _resumir_error(error, contexto)
    _recent_errors.append(resumen)
    if len(_recent_errors) > _RECENT_ERRORS_MAX:
        _recent_errors.pop(0)
    logger.error("Error registrado [%s]: %s", contexto or "sin contexto", resumen)
    if es_critico:
        _programar_aviso_critico(resumen, contexto)


def report_warning(mensaje, contexto=None):
    global _warn_count
    _warn_count += 1
    logger.warning("Advertencia registrada [%s]: %s", contexto or "sin contexto", mensaje)


def register_disconnect():
    global _disconnect_count
    _disconnect_count += 1
    logger.warning("Desconexión del gateway registrada (desde último reporte: %s)", _disconnect_count)


def register_reconnect():
    logger.info("Gateway de Discord reconectado (sesión reanudada)")


def register_connect():
    logger.info("Gateway de Discord conectado")


def _programar_aviso_critico(resumen, contexto):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        loop.create_task(_notify_critical(resumen, contexto or "desconocido"))
    else:
        logger.warning("Sin loop asyncio activo, no se pudo enviar aviso crítico")


async def _notify_critical(resumen, contexto):
    embed = discord.Embed(
        title="🔴 ¡Hubo un error crítico!",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )
    embed.description = f"**Contexto:** {contexto}\n\n```\n{resumen}\n```"
    embed.set_footer(text="PoloLogs sigue intentando funcionar. Revisá este evento.")
    await _send_dm(embed=embed)


async def _get_admin_user():
    if not _bot or not _admin_user_id:
        return None
    try:
        return await _bot.fetch_user(_admin_user_id)
    except Exception as e:
        logger.warning("No se pudo obtener al admin %s: %s", _admin_user_id, e)
        return None


async def _send_dm(embed=None, content=None):
    if not _admin_user_id:
        logger.warning("No hay ID de administrador configurado. Se omite el envío de DM del monitor.")
        return False
    try:
        user = await _get_admin_user()
        if not user:
            return False
        await user.send(content=content, embed=embed)
        return True
    except discord.Forbidden:
        logger.warning("No se pudo enviar DM del monitor (Forbidden).")
    except Exception as e:
        logger.warning("Error enviando DM del monitor: %s", e)
    return False


def _comandos_count():
    if not _bot:
        return 0
    try:
        return len(_bot.tree.get_commands())
    except Exception:
        return 0


def _is_ws_connected():
    if not _bot:
        return False
    try:
        return bool(_bot.is_ws_connected())
    except Exception:
        return False


def _db_ok():
    try:
        pool = db._get_pool()
        conn = pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            return True
        finally:
            pool.putconn(conn)
    except Exception:
        return False


async def _check_db():
    try:
        return await asyncio.wait_for(asyncio.to_thread(_db_ok), timeout=10)
    except Exception:
        return False


def _format_uptime():
    if not _start_time:
        return "desconocido"
    segs = int(time.time() - _start_time)
    horas, resto = divmod(segs, 3600)
    mins, _ = divmod(resto, 60)
    if horas:
        return f"{horas} hora{'s' if horas != 1 else ''} {mins} minuto{'s' if mins != 1 else ''}"
    return f"{mins} minuto{'s' if mins != 1 else ''}"


async def send_startup_message():
    global _startup_sent
    if _startup_sent:
        return
    _startup_sent = True
    try:
        db_ok = await _check_db()
        cmds = _comandos_count()
        user = await _get_admin_user()
        nombre = (user.display_name or user.name) if user else None
        embed = _build_startup_embed(db_ok, cmds, nombre)
        enviado = await _send_dm(embed=embed)
        if enviado:
            log_actions.log_info(
                "🟢 Mensaje de inicio del monitor enviado",
                "PoloLogs reportó su arranque al administrador.",
            )
        else:
            log_actions.log_warning(
                "🟢 Mensaje de inicio del monitor",
                "No se pudo enviar (admin no configurado o DM bloqueado).",
            )
    except Exception as e:
        logger.error("Error enviando mensaje de inicio: %s", e)
        report_error(e, contexto="Mensaje de inicio", es_critico=False)


def _build_startup_embed(db_ok, cmds, nombre=None):
    nombre = nombre or "admin"
    embed = discord.Embed(
        title=f"🟢 ¡Hola {nombre}! PoloLogs acaba de iniciar correctamente.",
        color=discord.Color.green() if db_ok else discord.Color.yellow(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="🤖 Estado", value="Online", inline=True)
    embed.add_field(name="⏱️ Inicio", value=time.strftime("%d/%m/%Y %H:%M"), inline=True)
    embed.add_field(name="🗄️ Base de datos", value="Conectada" if db_ok else "⚠️ ERROR", inline=True)
    embed.add_field(name="⚙️ Módulos cargados", value=f"{cmds} comandos", inline=True)
    embed.add_field(
        name="✅ Sistema",
        value="Listo para trabajar." if db_ok else "Funcionando con limitaciones.",
        inline=False,
    )
    embed.set_footer(text="Si ocurre algún problema te avisaré automáticamente.")
    return embed


async def send_status_report():
    global _last_heartbeat, _error_count, _warn_count, _disconnect_count, _hubo_error_critico
    _last_heartbeat = time.time()
    db_ok = await _check_db()
    embed = _build_status_embed(db_ok)
    _error_count = 0
    _warn_count = 0
    _disconnect_count = 0
    _hubo_error_critico = False
    enviado = await _send_dm(embed=embed)
    if enviado:
        log_actions.log_info(
            "📊 Reporte de estado enviado",
            "Reporte periódico del monitor entregado al administrador.",
        )
    return enviado


def _build_status_embed(db_ok):
    ws = _is_ws_connected()
    cmds = _comandos_count()
    errores = _error_count
    advertencias = _warn_count
    desconexiones = _disconnect_count
    ultimo = _recent_errors[-1] if _recent_errors else None
    critico = _hubo_error_critico

    if critico:
        color = discord.Color.red()
        emoji = "🔴"
    elif errores > 0 or advertencias > 0 or desconexiones > 0 or not db_ok or not ws:
        color = discord.Color.yellow()
        emoji = "🟡"
    else:
        color = discord.Color.green()
        emoji = "🟢"

    estado_bot = "Funcionando correctamente" if (db_ok and ws) else "Funcionando con problemas"

    embed = discord.Embed(
        title=f"{emoji} Reporte de estado de PoloLogs",
        color=color,
        timestamp=discord.utils.utcnow(),
    )

    if errores > 0 or critico:
        embed.description = "⚠️ **Se detectaron problemas desde el último reporte.**"
    elif advertencias > 0 or desconexiones > 0 or not db_ok or not ws:
        embed.description = "⚠️ **Hay advertencias menores desde el último reporte.**"
    else:
        embed.description = "Todo parece estar funcionando correctamente 🚀"

    embed.add_field(name="🤖 Bot", value=estado_bot, inline=True)
    embed.add_field(name="⏱️ Tiempo online", value=_format_uptime(), inline=True)
    embed.add_field(name="🌐 Discord Gateway", value="Conectado" if ws else "Desconectado", inline=True)
    embed.add_field(name="🗄️ PostgreSQL", value="OK" if db_ok else "ERROR", inline=True)
    embed.add_field(name="📦 Comandos activos", value=str(cmds), inline=True)

    detalles = f"✅ Errores: {errores}\n⚠️ Advertencias: {advertencias}"
    if desconexiones:
        detalles += f"\n🔌 Desconexiones gateway: {desconexiones}"
    embed.add_field(name="📊 Desde el último reporte", value=detalles, inline=False)

    if ultimo:
        embed.add_field(name="Último error", value=f"`{ultimo}`", inline=False)

    if errores > 0 or critico:
        embed.set_footer(text="El bot sigue funcionando, pero revisá estos eventos.")
    else:
        embed.set_footer(text="PoloLogs · Reporte periódico del monitor")
    return embed


async def _periodic_loop():
    global _last_heartbeat
    while True:
        try:
            await asyncio.sleep(_report_interval)
            _last_heartbeat = time.time()
            await send_status_report()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Error en el loop de reportes: %s", e)
            report_error(e, contexto="Loop de reportes", es_critico=True)
