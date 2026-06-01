"""
asistencia.py — Sistema de asistencia a operativos via Guild Scheduled Events
Integración con ArmamentBot (VxB). No modifica main.py ni ningún archivo existente.

Flujo:
  1. 5 min antes del evento → snapshot de confirmados (GOING)
  2. Al iniciar el OP     → snapshot final de confirmados + inicio tracking
  3. Durante el OP        → on_weapon_withdraw() registra participantes reales
  4. Al terminar el OP    → clasificación + escritura en Google Sheets
"""

import asyncio
import json
import inspect
import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

import discord
from urllib import parse as urllib_parse
from urllib import error as urllib_error
from urllib import request as urllib_request

from config import ASISTENCIA_SEMANAL_CHANNEL_ID, ASISTENCIA_SEMANAL_OBJETIVO, INACTIVIDAD_JUSTIFICADA_ROLE_ID
from config import ALERTAS_CHANNEL_ID, CATEGORIAS
from config import TOKEN as DISCORD_TOKEN
import state
from database import get_db_connection
from database import actualizar_operativo_programado_db, guardar_operativo_programado_db
from database import cargar_operativos_programados_db, eliminar_operativo_programado_db, invalidar_cache_contexto_justificaciones_db
from database import cargar_config_asistencia_semanal_db, guardar_config_asistencia_semanal_db
from database import reiniciar_asistencia_semanal_semana_db

logger = logging.getLogger("ArmamentBot")
DISCORD_API_BASE = "https://discord.com/api/v10"

# ─── GUILD ────────────────────────────────────────────────────
GUILD_ID = 968286555150110790

# ─── ESTADO GLOBAL DE ASISTENCIA ─────────────────────────────
# Estructura por event_id:
# {
#   event_id: {
#     "event":              discord.ScheduledEvent,
#     "confirmados_previo": set[int],   # Discord IDs que marcaron GOING (snapshot 5 min antes)
#     "confirmados_inicio": set[int],   # Discord IDs que marcaron GOING (snapshot al inicio)
#     "participaron":       set[int],   # Discord IDs que retiraron arma durante el OP
#     "inicio":             datetime,
#     "fin":                datetime | None,
#     "task_preaviso":      asyncio.Task | None,
#   }
# }
_sesiones_asistencia: dict = {}

# Bot reference (se setea desde main.py igual que los otros módulos)
_bot = None
_weekly_task: Optional[asyncio.Task] = None
AUTO_INICIO_OPERATIVO_MINUTOS = 15

try:
    _TZ_ES = ZoneInfo("Europe/Madrid")
except Exception:
    _TZ_ES = timezone(timedelta(hours=2))

def set_bot(bot_instance):
    global _bot
    _bot = bot_instance


def _crear_tarea(coro) -> asyncio.Task:
    """
    Crea una tarea en el loop activo si existe; si no, usa el loop del bot.
    """
    try:
        return asyncio.create_task(coro)
    except RuntimeError:
        if _bot is None:
            raise
        return _bot.loop.create_task(coro)


def _persistir_programado_en_memoria(programado_id: int, **datos):
    state.operativos_programados[int(programado_id)] = {
        **dict(state.operativos_programados.get(int(programado_id), {}) or {}),
        **datos,
    }
    try:
        invalidar_cache_contexto_justificaciones_db()
    except Exception:
        pass


def _cache_event_users(event_id: int, users: set[int], *, complete: bool = True) -> None:
    state.scheduled_event_user_cache[int(event_id)] = {
        "users": set(int(uid) for uid in users),
        "complete": bool(complete),
        "updated_at": datetime.now(tz=timezone.utc),
    }


def registrar_usuario_evento_cache(event_id: int, user_id: int) -> None:
    entry = state.scheduled_event_user_cache.get(int(event_id)) or {
        "users": set(),
        "complete": False,
        "updated_at": None,
    }
    entry["users"] = set(entry.get("users") or set())
    entry["users"].add(int(user_id))
    entry["updated_at"] = datetime.now(tz=timezone.utc)
    state.scheduled_event_user_cache[int(event_id)] = entry


def quitar_usuario_evento_cache(event_id: int, user_id: int) -> None:
    entry = state.scheduled_event_user_cache.get(int(event_id))
    if not entry:
        return
    users = set(entry.get("users") or set())
    users.discard(int(user_id))
    entry["users"] = users
    entry["updated_at"] = datetime.now(tz=timezone.utc)
    state.scheduled_event_user_cache[int(event_id)] = entry


def limpiar_cache_evento(event_id: int) -> None:
    state.scheduled_event_user_cache.pop(int(event_id), None)


def _obtener_cache_evento(event_id: int) -> Optional[dict]:
    entry = state.scheduled_event_user_cache.get(int(event_id))
    if not entry:
        return None
    return {
        "users": set(entry.get("users") or set()),
        "complete": bool(entry.get("complete", False)),
        "updated_at": entry.get("updated_at"),
    }


async def restaurar_operativos_programados_desde_db() -> int:
    """
    Reconstruye en memoria los eventos operativos programados/activos
    para que sobrevivan reinicios del bot.
    """
    if _bot is None:
        logger.warning("⚠️ [Asistencia] No se puede restaurar operativos programados: bot no inicializado")
        return 0

    rows = cargar_operativos_programados_db()
    if not rows:
        logger.info("ℹ️ [Asistencia] No hay operativos programados para restaurar")
        return 0

    state.operativos_programados = {}
    restaurados = 0
    for row in rows:
        try:
            event_id = int(row.get("event_id"))
        except (TypeError, ValueError):
            continue

        event = await fetch_event_by_id(event_id)
        if not event:
            continue

        estado = str(row.get("estado") or "programado").strip().lower()
        tipo = str(row.get("tipo") or "operativo").strip().lower() or "operativo"
        fecha_hora = row.get("fecha_hora") or getattr(event, "start_time", None)
        inicio = row.get("inicio")
        fin = row.get("fin")

        _persistir_programado_en_memoria(
            event_id,
            event_id=event_id,
            nombre=row.get("nombre") or event.name,
            fecha_hora=fecha_hora,
            canal_id=row.get("canal_id"),
            mensaje_id=row.get("mensaje_id"),
            tipo=row.get("tipo") or "operativo",
            estado=estado,
            inicio=inicio,
            fin=fin,
            creado_por=row.get("creado_por"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            event=event,
        )

        if tipo == "evento":
            restaurados += 1
            continue

        sesion = {
            "event": event,
            "confirmados_previo": set(),
            "confirmados_inicio": set(),
            "participaron": set(),
            "inicio": inicio if estado == "activo" else None,
            "fin": fin,
            "task_preaviso": None,
        }
        _sesiones_asistencia[event_id] = sesion

        ahora = datetime.now(tz=timezone.utc)
        if estado == "programado" and getattr(event, "start_time", None):
            try:
                task = _crear_tarea(_tarea_preaviso(event_id))
                sesion["task_preaviso"] = task
            except Exception as e:
                logger.warning(f"⚠️ [Asistencia] No se pudo reprogramar preaviso para {event_id}: {e}")
        elif estado == "activo":
            if not sesion["inicio"]:
                sesion["inicio"] = inicio or ahora

        restaurados += 1

    logger.info(f"✅ [Asistencia] Operativos programados restaurados desde BD: {restaurados}")
    return restaurados


def asistencia_semanal_activa() -> bool:
    return bool(state.ASISTENCIA_SEMANAL_CONFIG.get("activo", False))


def cargar_config_asistencia_semanal() -> dict:
    config = cargar_config_asistencia_semanal_db()
    state.ASISTENCIA_SEMANAL_CONFIG.update(config)
    return dict(state.ASISTENCIA_SEMANAL_CONFIG)


def activar_asistencia_semanal(activado_por: str) -> dict:
    week_start = _semana_objetivo_actual()
    reiniciar_asistencia_semanal_semana_db(week_start)
    config = {
        "activo": True,
        "activado_por": activado_por,
        "activado_at": _ahora_es(),
    }
    state.ASISTENCIA_SEMANAL_CONFIG.update(config)
    guardar_config_asistencia_semanal_db(True, activado_por, config["activado_at"])
    return dict(state.ASISTENCIA_SEMANAL_CONFIG)


def desactivar_asistencia_semanal(activado_por: Optional[str] = None) -> dict:
    config = {
        "activo": False,
        "activado_por": activado_por or state.ASISTENCIA_SEMANAL_CONFIG.get("activado_por"),
    }
    state.ASISTENCIA_SEMANAL_CONFIG.update(config)
    guardar_config_asistencia_semanal_db(False, config["activado_por"], state.ASISTENCIA_SEMANAL_CONFIG.get("activado_at"))
    return dict(state.ASISTENCIA_SEMANAL_CONFIG)


def _ahora_es() -> datetime:
    return datetime.now(tz=_TZ_ES)


def _dt_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _dt_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


async def _buscar_operativo_para_auto_inicio() -> Optional[tuple[int, dict]]:
    ahora = datetime.now(tz=timezone.utc)
    candidatos = []

    for event_id, data in dict(state.operativos_programados or {}).items():
        estado = str(data.get("estado") or "programado").strip().lower()
        tipo = str(data.get("tipo") or "operativo").strip().lower()
        if tipo != "operativo" or estado != "programado":
            continue

        event = data.get("event") or data.get("evento")
        if event is None:
            try:
                event = await fetch_event_by_id(int(event_id))
            except Exception:
                event = None
        inicio = _dt_aware_utc(getattr(event, "start_time", None) if event else data.get("fecha_hora"))
        if inicio is None:
            continue

        segundos_hasta_inicio = (inicio - ahora).total_seconds()
        if -300 <= segundos_hasta_inicio <= AUTO_INICIO_OPERATIVO_MINUTOS * 60:
            candidatos.append((abs(segundos_hasta_inicio), int(event_id), data, event))

    if not candidatos:
        return None

    _, event_id, data, event = sorted(candidatos, key=lambda item: item[0])[0]
    if event is not None:
        data["event"] = event
        data["evento"] = event
    return event_id, data


async def autoiniciar_operativo_por_retiro(datos: dict) -> Optional[int]:
    """
    Si un retiro de pistola ocurre dentro de la ventana previa a un evento
    operativo programado, inicia el operativo y la asistencia automáticamente.
    """
    if state.operativo_activo.get("activo"):
        return None
    if str(datos.get("tipo") or "").upper() != "RETIRO":
        return None
    if datos.get("objeto") not in set(CATEGORIAS.get("pistolas", [])):
        return None

    candidato = await _buscar_operativo_para_auto_inicio()
    if not candidato:
        return None

    event_id, data = candidato
    try:
        from operativo import iniciar_operativo

        iniciado_por = datos.get("discord_id")
        try:
            iniciado_por = int(iniciado_por)
        except (TypeError, ValueError):
            iniciado_por = None

        iniciar_operativo(iniciado_por or 0)
        await handle_op_start(event_id)

        canal = _bot.get_channel(ALERTAS_CHANNEL_ID) if _bot else None
        if canal:
            nombre_evento = data.get("nombre") or getattr(data.get("event") or data.get("evento"), "name", "Operativo")
            await canal.send(
                "Operativo iniciado automáticamente por retiro de pistola "
                f"dentro de los {AUTO_INICIO_OPERATIVO_MINUTOS} minutos previos a **{nombre_evento}**."
            )

        logger.info(
            "[Asistencia] Operativo auto-iniciado por retiro | "
            f"event_id={event_id} | objeto={datos.get('objeto')} | discord_id={datos.get('discord_id')}"
        )
        return event_id
    except Exception as e:
        logger.error(f"Error auto-iniciando operativo por retiro: {e}", exc_info=True)
        return None


def _inicio_semana_actual() -> datetime:
    ahora = _ahora_es().astimezone(_TZ_ES)
    inicio = ahora - timedelta(days=ahora.weekday())
    return datetime(inicio.year, inicio.month, inicio.day, tzinfo=_TZ_ES)


def _clave_semana_actual() -> str:
    return _inicio_semana_actual().date().isoformat()


def _cancelar_tarea_semanal():
    global _weekly_task
    if _weekly_task and not _weekly_task.done():
        _weekly_task.cancel()
    _weekly_task = None


def iniciar_programador_asistencia_semanal():
    global _weekly_task
    if _bot is None:
        logger.warning("⚠️ [Asistencia] No se puede iniciar el programador semanal: bot no inicializado")
        return
    if _weekly_task and not _weekly_task.done():
        return
    _weekly_task = _crear_tarea(_tarea_revision_semanal())


def _semana_objetivo_actual() -> str:
    return _clave_semana_actual()


def _obtener_asistencia_semanal_semana(week_start: str) -> dict[int, dict]:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT discord_id, operativos_realizados, justificado, aviso_enviado
            FROM asistencia_semanal
            WHERE week_start = %s
            """,
            (week_start,),
        )
        rows = cursor.fetchall() or []
        cursor.close()
        conn.close()
        resultado: dict[int, dict] = {}
        for row in rows:
            try:
                did = int(row["discord_id"])
            except (TypeError, ValueError):
                continue
            resultado[did] = {
                "operativos_realizados": int(row.get("operativos_realizados") or 0),
                "justificado": bool(row.get("justificado")),
                "aviso_enviado": bool(row.get("aviso_enviado")),
            }
        return resultado
    except Exception as e:
        logger.error(f"❌ [Asistencia] Error leyendo conteo semanal {week_start}: {e}", exc_info=True)
        return {}


def registrar_operativos_semanales(resultado: dict) -> None:
    """
    Guarda un +1 en el contador semanal para quienes efectivamente participaron del operativo.
    Se cuentan ASISTIO y NO_CONFIRMADO porque ambos retiraron arma durante el OP.
    """
    if not asistencia_semanal_activa():
        return
    try:
        from sheets import sincronizar_asistencia_semanal

        miembros = resultado.get("miembros", {}) or {}
        participantes = [
            did for did, estado in miembros.items()
            if estado in (EstadoAsistencia.ASISTIO, EstadoAsistencia.NO_CONFIRMADO)
        ]
        if not participantes:
            return

        week_start = _semana_objetivo_actual()
        conn = get_db_connection()
        cursor = conn.cursor()
        for discord_id in participantes:
            cursor.execute(
                """
                INSERT INTO asistencia_semanal
                    (week_start, discord_id, operativos_realizados, justificado, aviso_enviado, updated_at)
                VALUES (%s, %s, 1, FALSE, FALSE, NOW())
                ON CONFLICT (week_start, discord_id)
                DO UPDATE SET
                    operativos_realizados = asistencia_semanal.operativos_realizados + 1,
                    updated_at = NOW()
                """,
                (week_start, str(discord_id)),
            )
        conn.commit()
        cursor.close()
        conn.close()
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(sincronizar_asistencia_semanal())
        except Exception as e:
            logger.error(f"❌ [Asistencia] Error sincronizando hoja semanal: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"❌ [Asistencia] Error registrando contador semanal: {e}", exc_info=True)


def _marcar_justificado_semanal(week_start: str, discord_id: int, operativos_realizados: int) -> None:
    """
    Marca a un miembro como justificado en la semana actual y conserva el conteo.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO asistencia_semanal
            (week_start, discord_id, operativos_realizados, justificado, aviso_enviado, updated_at)
        VALUES (%s, %s, %s, TRUE, FALSE, NOW())
        ON CONFLICT (week_start, discord_id)
        DO UPDATE SET
            operativos_realizados = EXCLUDED.operativos_realizados,
            justificado = TRUE,
            aviso_enviado = FALSE,
            updated_at = NOW()
        """,
        (week_start, str(discord_id), operativos_realizados),
    )
    conn.commit()
    cursor.close()
    conn.close()


async def _enviar_dm_aviso(member: discord.Member, faltan: int, realizados: int):
    texto = (
        f"Te aviso para que no te agarre el lunes corriendo: llevás **{realizados}/"
        f"{ASISTENCIA_SEMANAL_OBJETIVO}** operativos esta semana.\n"
        f"Te faltan **{faltan}** para llegar al mínimo.\n"
        f"Si todavía estás a tiempo, metete a uno más y cerrás la semana bien."
    )
    await member.send(texto)


async def revisar_asistencia_semanal() -> Optional[dict]:
    """
    Revisa la semana actual, manda reporte al canal y DMs a quienes van por debajo del objetivo.
    """
    from asistencia_plantilla import get_plantilla_activa
    from sheets import sincronizar_asistencia_semanal

    if _bot is None:
        logger.warning("⚠️ [Asistencia] No se puede revisar asistencia semanal: bot no inicializado")
        return None
    if not asistencia_semanal_activa():
        logger.info("ℹ️ [Asistencia] Revisión semanal desactivada")
        return None

    week_start = _semana_objetivo_actual()
    conteos = _obtener_asistencia_semanal_semana(week_start)
    plantilla_activa = get_plantilla_activa()

    bajo_objetivo: list[tuple[int, dict, int]] = []
    sin_asistencia: list[tuple[int, dict, int]] = []
    justificados: list[tuple[int, dict, int]] = []
    justificados_ids: set[int] = set()

    guild = _bot.get_guild(GUILD_ID) if _bot is not None else None

    for did, info in plantilla_activa.items():
        if not did:
            continue

        is_justificado = False
        if guild is not None:
            member = guild.get_member(int(did))
            if member is None:
                try:
                    member = await guild.fetch_member(int(did))
                except Exception:
                    member = None
            if member is not None:
                is_justificado = any(role.id == INACTIVIDAD_JUSTIFICADA_ROLE_ID for role in getattr(member, "roles", []))

        data = conteos.get(int(did), {"operativos_realizados": 0, "justificado": False, "aviso_enviado": False})
        realizados = int(data.get("operativos_realizados") or 0)
        if is_justificado:
            data["justificado"] = True
            justificados.append((int(did), info, realizados))
            justificados_ids.add(int(did))
            try:
                _marcar_justificado_semanal(week_start, int(did), realizados)
            except Exception as e:
                logger.error(
                    f"❌ [Asistencia] Error marcando justificado en DB para {did}: {e}",
                    exc_info=True,
                )
            continue

        if realizados < ASISTENCIA_SEMANAL_OBJETIVO:
            bajo_objetivo.append((int(did), info, realizados))
        if realizados == 0:
            sin_asistencia.append((int(did), info, realizados))

    bajo_objetivo.sort(key=lambda x: (x[2], x[1].get("nombre_ic", "")))
    sin_asistencia.sort(key=lambda x: x[1].get("nombre_ic", ""))
    justificados.sort(key=lambda x: x[1].get("nombre_ic", ""))

    canal = _bot.get_channel(ASISTENCIA_SEMANAL_CHANNEL_ID)
    if canal:
        embed = discord.Embed(
            title="?? Revisi?n semanal de asistencia",
            description=(
                f"Semana iniciada el **{_inicio_semana_actual().strftime('%d/%m/%Y')}**\n"
                f"Objetivo: **{ASISTENCIA_SEMANAL_OBJETIVO} operativos** por semana."
            ),
            color=discord.Color.blurple(),
            timestamp=_ahora_es(),
        )
        embed.add_field(
            name="?? Criterio",
            value="Se cuenta la participaci?n real en operativos (ASISTI? y NO CONFIRMADO).",
            inline=False,
        )

        if sin_asistencia:
            texto = "\n".join(
                f"? {info.get('nombre_ic', 'N/A')} (<@{did}>)"
                for did, info, _ in sin_asistencia[:25]
            )
            if len(sin_asistencia) > 25:
                texto += f"\n? y {len(sin_asistencia) - 25} m?s"
            embed.add_field(name="? Sin asistencia", value=texto, inline=False)
        else:
            embed.add_field(name="? Sin asistencia", value="Nadie qued? en cero esta semana.", inline=False)

        if justificados:
            texto = "\n".join(
                f"? {info.get('nombre_ic', 'N/A')} (<@{did}>) ? justificado"
                for did, info, _ in justificados[:25]
            )
            if len(justificados) > 25:
                texto += f"\n? y {len(justificados) - 25} m?s"
            embed.add_field(name="?? Justificados", value=texto, inline=False)

        bajo_objetivo_sin_justificar = [item for item in bajo_objetivo if item[0] not in justificados_ids]
        if bajo_objetivo_sin_justificar:
            texto = "\n".join(
                f"? {info.get('nombre_ic', 'N/A')} (<@{did}>) ? {realizados}/{ASISTENCIA_SEMANAL_OBJETIVO}"
                for did, info, realizados in bajo_objetivo_sin_justificar[:25]
            )
            if len(bajo_objetivo_sin_justificar) > 25:
                texto += f"\n? y {len(bajo_objetivo_sin_justificar) - 25} m?s"
            embed.add_field(name="?? Bajo el objetivo", value=texto, inline=False)

        embed.set_footer(text="Se revisa cada domingo 23:00 hora Espa?a")
        try:
            await canal.send(embed=embed)
        except Exception as e:
            logger.error(f"? [Asistencia] Error enviando reporte semanal: {e}", exc_info=True)

    guild = _bot.get_guild(GUILD_ID)
    if guild:
        for discord_id, info, realizados in bajo_objetivo:
            data = conteos.get(discord_id, {"operativos_realizados": 0, "justificado": False, "aviso_enviado": False})
            if data.get("aviso_enviado") or data.get("justificado") or discord_id in justificados_ids:
                continue
            member = guild.get_member(discord_id)
            if member is None:
                try:
                    member = await guild.fetch_member(discord_id)
                except Exception:
                    member = None
            if not member:
                continue
            faltan = max(0, ASISTENCIA_SEMANAL_OBJETIVO - realizados)
            try:
                await _enviar_dm_aviso(member, faltan, realizados)
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO asistencia_semanal
                        (week_start, discord_id, operativos_realizados, justificado, aviso_enviado, updated_at)
                    VALUES (%s, %s, %s, FALSE, TRUE, NOW())
                    ON CONFLICT (week_start, discord_id)
                    DO UPDATE SET
                        operativos_realizados = EXCLUDED.operativos_realizados,
                        justificado = FALSE,
                        aviso_enviado = TRUE,
                        updated_at = NOW()
                    """,
                    (week_start, str(discord_id), realizados),
                )
                conn.commit()
                cursor.close()
                conn.close()
            except discord.Forbidden:
                logger.debug(f"⚠️ [Asistencia] DM bloqueado para {member} ({discord_id})")
            except Exception as e:
                logger.error(f"❌ [Asistencia] Error enviando DM semanal a {discord_id}: {e}", exc_info=True)

    try:
        await sincronizar_asistencia_semanal()
    except Exception as e:
        logger.error(f"❌ [Asistencia] Error sincronizando asistencia semanal en revisión: {e}", exc_info=True)

    return {
        "week_start": week_start,
        "bajo_objetivo": bajo_objetivo,
        "sin_asistencia": sin_asistencia,
    }


async def _tarea_revision_semanal():
    while True:
        try:
            if not asistencia_semanal_activa():
                await asyncio.sleep(300)
                continue
            ahora = _ahora_es()
            # Domingo = 6, 23:00 hora España
            dias_hasta_domingo = (6 - ahora.weekday()) % 7
            objetivo = ahora.replace(hour=23, minute=0, second=0, microsecond=0) + timedelta(days=dias_hasta_domingo)
            if dias_hasta_domingo == 0 and ahora >= objetivo:
                objetivo += timedelta(days=7)
            espera = max(30.0, (objetivo - ahora).total_seconds())
            await asyncio.sleep(espera)
            await revisar_asistencia_semanal()
            # Evita re-disparar en un loop corto; reprograma al siguiente domingo 23:00
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"❌ [Asistencia] Error en tarea semanal: {e}", exc_info=True)
            await asyncio.sleep(300)


# ─── ENUMS ────────────────────────────────────────────────────

class EstadoAsistencia(str, Enum):
    ASISTIO       = "✅ ASISTIÓ"        # Marcó el evento Y participó (retiró arma)
    NO_CONFIRMADO = "⚠️ NO CONFIRMADO"  # Participó pero NO marcó el evento
    FALTO         = "❌ FALTÓ"          # Marcó el evento pero NO participó
    JUSTIFICADO   = "🟦 JUSTIFICADO"    # No marcó ni participó, pero justificó
    AUSENTE       = "⬛ AUSENTE"        # No marcó ni participó


# ─── FETCH DE USUARIOS DEL EVENTO ─────────────────────────────

async def fetch_event_users(event: discord.ScheduledEvent) -> set[int]:
    """
    Devuelve un set de Discord IDs de usuarios con status GOING en el evento.
    Primero intenta el endpoint HTTP oficial de Discord. Si no está disponible
    el token o falla la request, cae a los helpers de discord.py cuando existan.
    """
    cache = _obtener_cache_evento(event.id)
    if cache and cache.get("complete"):
        usuarios_cache = set(cache.get("users") or set())
        logger.info(
            f"📋 [Asistencia] Evento '{event.name}' | "
            f"Confirmados (cache): {len(usuarios_cache)}"
        )
        return usuarios_cache

    confirmados: set[int] = set()

    def _fetch_users_http() -> set[int]:
        if not DISCORD_TOKEN:
            raise RuntimeError("DISCORD_TOKEN no configurado")

        usuarios: set[int] = set()
        after: Optional[int] = None
        while True:
            params = {"limit": 100, "with_member": "false"}
            if after is not None:
                params["after"] = str(after)
            url = (
                f"{DISCORD_API_BASE}/guilds/{GUILD_ID}/scheduled-events/{int(event.id)}/users"
                f"?{urllib_parse.urlencode(params)}"
            )
            req = urllib_request.Request(
                url,
                headers={
                    "Authorization": f"Bot {DISCORD_TOKEN}",
                    "User-Agent": "ArmamentBot (VxB)",
                },
                method="GET",
            )
            try:
                with urllib_request.urlopen(req, timeout=20) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except urllib_error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass
                raise RuntimeError(f"HTTP {e.code} consultando usuarios del evento: {body[:200]}") from e

            if not isinstance(payload, list) or not payload:
                break

            last_id = None
            for item in payload:
                user = item.get("user") or {}
                user_id = user.get("id")
                if not user_id:
                    continue
                try:
                    uid = int(user_id)
                except (TypeError, ValueError):
                    continue
                usuarios.add(uid)
                last_id = uid

            if last_id is None or len(payload) < 100:
                break
            after = last_id

        return usuarios

    try:
        try:
            confirmados = await asyncio.to_thread(_fetch_users_http)
        except Exception as http_exc:
            logger.debug(f"ℹ️ [Asistencia] Fallback HTTP de usuarios no disponible: {http_exc}")
            guild = _bot.get_guild(GUILD_ID) if _bot else None
            fetch_users = getattr(event, "fetch_users", None)
            if callable(fetch_users):
                resultado = fetch_users(limit=None)
                if hasattr(resultado, "__aiter__"):
                    async for user in resultado:
                        confirmados.add(user.id)
                elif inspect.isawaitable(resultado):
                    usuarios = await resultado
                    for user in usuarios or []:
                        confirmados.add(user.id)
                else:
                    for user in resultado or []:
                        confirmados.add(user.id)
            elif guild and hasattr(guild, "fetch_scheduled_event_users"):
                resultado = guild.fetch_scheduled_event_users(event.id, limit=None)
                if hasattr(resultado, "__aiter__"):
                    async for user in resultado:
                        confirmados.add(user.id)
                elif inspect.isawaitable(resultado):
                    usuarios = await resultado
                    for user in usuarios or []:
                        confirmados.add(user.id)
                else:
                    for user in resultado or []:
                        confirmados.add(user.id)
            else:
                logger.warning(
                    f"⚠️ [Asistencia] No hay metodo compatible para leer usuarios del evento {event.id}"
                )
        logger.info(
            f"📋 [Asistencia] Evento '{event.name}' | "
            f"Confirmados: {len(confirmados)}"
        )
        _cache_event_users(event.id, confirmados, complete=True)
    except discord.NotFound:
        logger.warning(f"⚠️ [Asistencia] Evento {event.id} no encontrado al hacer fetch")
    except discord.HTTPException as e:
        logger.error(f"❌ [Asistencia] Error HTTP fetch usuarios evento {event.id}: {e}")
    except Exception as e:
        logger.error(f"❌ [Asistencia] Error inesperado fetch usuarios: {e}", exc_info=True)
    return confirmados


async def fetch_event_by_id(event_id: int) -> Optional[discord.ScheduledEvent]:
    """Obtiene un GuildScheduledEvent por ID desde el guild configurado."""
    try:
        if _bot is None:
            logger.error("❌ [Asistencia] Bot no inicializado al buscar evento")
            return None
        guild = _bot.get_guild(GUILD_ID)
        if not guild:
            logger.error(f"❌ [Asistencia] Guild {GUILD_ID} no encontrado")
            return None
        event = await guild.fetch_scheduled_event(event_id)
        return event
    except discord.NotFound:
        logger.warning(f"⚠️ [Asistencia] Evento {event_id} no existe en Discord")
        return None
    except discord.HTTPException as e:
        logger.error(f"❌ [Asistencia] Error HTTP obteniendo evento {event_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ [Asistencia] Error inesperado: {e}", exc_info=True)
        return None


# ─── PREAVISO (5 MINUTOS ANTES) ───────────────────────────────

async def _tarea_preaviso(event_id: int):
    """
    Espera hasta 5 minutos antes del inicio del evento,
    hace snapshot de confirmados y luego espera al inicio exacto.
    """
    sesion = _sesiones_asistencia.get(event_id)
    if not sesion:
        return

    event     = sesion["event"]
    inicio_dt = event.start_time  # datetime con tzinfo

    # ── Calcular tiempo de espera hasta 5 min antes ────────────
    ahora      = datetime.now(tz=timezone.utc)
    preaviso   = inicio_dt - timedelta(minutes=5)
    espera_seg = (preaviso - ahora).total_seconds()

    if espera_seg > 0:
        logger.info(
            f"⏰ [Asistencia] Esperando {espera_seg:.0f}s para preaviso "
            f"del evento '{event.name}' ({event_id})"
        )
        await asyncio.sleep(espera_seg)

    # ── Snapshot 5 min antes ──────────────────────────────────
    event_refrescado = await fetch_event_by_id(event_id)
    if event_refrescado:
        sesion["event"] = event_refrescado

    confirmados_previo = await fetch_event_users(sesion["event"])
    sesion["confirmados_previo"] = confirmados_previo

    logger.info(
        f"📸 [Asistencia] Snapshot preaviso (5min) | "
        f"Evento: '{sesion['event'].name}' | "
        f"Confirmados: {len(confirmados_previo)}"
    )

    # ── Esperar al inicio exacto ───────────────────────────────
    ahora      = datetime.now(tz=timezone.utc)
    espera_seg = (inicio_dt - ahora).total_seconds()
    if espera_seg > 0:
        await asyncio.sleep(espera_seg)

    sesion_actual = _sesiones_asistencia.get(event_id)
    if not sesion_actual or sesion_actual.get("fin") or sesion_actual.get("inicio"):
        return

    await handle_op_start(event_id)


# ─── INICIO DEL OPERATIVO ─────────────────────────────────────

async def handle_op_start(event_id: int):
    """
    Llamar cuando el OP arranca (puede ser automático desde _tarea_preaviso
    o manual desde /inicio_operativo si se pasó event_id).
    Hace snapshot final de confirmados.
    """
    sesion = _sesiones_asistencia.get(event_id)
    if not sesion:
        logger.warning(f"⚠️ [Asistencia] handle_op_start: no hay sesión para evento {event_id}")
        return
    if sesion.get("inicio") and not sesion.get("fin"):
        logger.info(
            f"ℹ️ [Asistencia] handle_op_start ignorado: el evento {event_id} ya estaba iniciado"
        )
        return

    # Snapshot al inicio
    event_refrescado = await fetch_event_by_id(event_id)
    if event_refrescado:
        sesion["event"] = event_refrescado

    confirmados_inicio = await fetch_event_users(sesion["event"])
    sesion["confirmados_inicio"] = confirmados_inicio
    sesion["inicio"]             = datetime.now(tz=timezone.utc)
    sesion["participaron"]       = set()

    try:
        await asyncio.to_thread(
            actualizar_operativo_programado_db,
            event_id,
            nombre=sesion["event"].name,
            estado="activo",
            inicio=_dt_naive_utc(sesion["inicio"]),
        )
        _persistir_programado_en_memoria(
            event_id,
            event_id=event_id,
            nombre=sesion["event"].name,
            estado="activo",
            inicio=_dt_naive_utc(sesion["inicio"]),
            fecha_hora=_dt_naive_utc(getattr(sesion["event"], "start_time", None)),
            canal_id=getattr(sesion.get("event"), "channel_id", None),
            mensaje_id=getattr(sesion.get("event"), "message_id", None),
            evento=sesion["event"],
        )
    except Exception as e:
        logger.warning(f"⚠️ [Asistencia] No se pudo marcar evento activo en BD: {e}")

    logger.info(
        f"🟢 [Asistencia] OP iniciado | Evento: '{sesion['event'].name}' | "
        f"Confirmados al inicio: {len(confirmados_inicio)}"
    )


# ─── REGISTRO DE PARTICIPACIÓN REAL ───────────────────────────

def on_weapon_withdraw(user_id: int, event_id: Optional[int] = None):
    """
    Llamar desde on_message / guardar_registro cuando se detecta un RETIRO
    durante un operativo activo.

    Si event_id es None, registra en TODAS las sesiones activas.
    Esto permite integración con el sistema actual sin romper nada.
    """
    if event_id is not None:
        sesion = _sesiones_asistencia.get(event_id)
        if sesion and sesion.get("inicio") and not sesion.get("fin"):
            sesion["participaron"].add(user_id)
            logger.debug(f"🔫 [Asistencia] Retiro registrado | User: {user_id} | Evento: {event_id}")
        return

    # Registrar en todas las sesiones activas
    for eid, sesion in _sesiones_asistencia.items():
        if sesion.get("inicio") and not sesion.get("fin"):
            sesion["participaron"].add(user_id)
            logger.debug(f"🔫 [Asistencia] Retiro registrado | User: {user_id} | Evento: {eid}")


def _normalizar_nombre_contexto(valor: str) -> str:
    return " ".join(str(valor or "").strip().lower().split())


def _obtener_ids_justificados_evento(event_id: int, event_name: str) -> set[int]:
    justificados: set[int] = set()
    nombre_norm = _normalizar_nombre_contexto(event_name)

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT discord_id, tipo, subtipo
            FROM justificaciones_texto
            WHERE created_at >= NOW() - INTERVAL '7 days'
            ORDER BY created_at DESC
            """
        )
        rows = cursor.fetchall() or []
        cursor.close()
        conn.close()
    except Exception as e:
        logger.warning(f"⚠️ [Asistencia] No se pudieron leer justificaciones para el evento {event_id}: {e}")
        rows = []

    for row in rows:
        try:
            discord_id = int(row.get("discord_id"))
        except (TypeError, ValueError):
            continue

        subtipo_norm = _normalizar_nombre_contexto(row.get("subtipo") or "")
        tipo_norm = _normalizar_nombre_contexto(row.get("tipo") or "")
        if subtipo_norm and subtipo_norm == nombre_norm:
            justificados.add(discord_id)
            continue
        if tipo_norm == "operativo" and not subtipo_norm:
            justificados.add(discord_id)

    guild = _bot.get_guild(GUILD_ID) if _bot is not None else None
    if guild is not None and INACTIVIDAD_JUSTIFICADA_ROLE_ID:
        for member in getattr(guild, "members", []) or []:
            try:
                if any(role.id == INACTIVIDAD_JUSTIFICADA_ROLE_ID for role in getattr(member, "roles", [])):
                    justificados.add(int(member.id))
            except Exception:
                continue

    return justificados


# ─── FIN DEL OPERATIVO ────────────────────────────────────────

async def handle_op_end(event_id: int) -> Optional[dict]:
    """
    Llamar cuando el OP termina.
    Devuelve el resultado de clasificación para usarlo donde quieras
    (embed Discord, Google Sheets, etc.)

    Returns:
        {
          "evento":   str (nombre del evento),
          "inicio":   datetime,
          "fin":      datetime,
          "miembros": { discord_id: EstadoAsistencia },
          "resumen":  { "asistio": int, "no_confirmado": int, "falto": int, "ausente": int }
        }
        o None si no existe la sesión.
    """
    sesion = _sesiones_asistencia.get(event_id)
    if not sesion:
        logger.warning(f"⚠️ [Asistencia] handle_op_end: no hay sesión para evento {event_id}")
        return None
    if sesion.get("fin") is not None:
        resultado_cierre = sesion.get("resultado_cierre")
        if resultado_cierre:
            return resultado_cierre
        logger.info(
            f"ℹ️ [Asistencia] handle_op_end ignorado: el evento {event_id} ya estaba cerrado"
        )
        return None

    sesion["fin"] = datetime.now(tz=timezone.utc)

    # Snapshot final del evento
    event_refrescado = await fetch_event_by_id(event_id)
    if event_refrescado:
        sesion["event"] = event_refrescado

    confirmados_fin = await fetch_event_users(sesion["event"])

    # Usar confirmados_inicio si existe, sino confirmados_previo, sino snapshot final
    confirmados = (
        sesion.get("confirmados_inicio")
        or sesion.get("confirmados_previo")
        or confirmados_fin
    )

    resultado = clasificar_asistencia(
        confirmados   = confirmados,
        participaron  = sesion["participaron"],
        event_id      = event_id,
        event_name    = sesion["event"].name,
    )

    logger.info(
        f"🏁 [Asistencia] OP terminado | Evento: '{sesion['event'].name}' | "
        f"Asistieron: {resultado['resumen']['asistio']} | "
        f"Faltaron: {resultado['resumen']['falto']} | "
        f"No confirmados: {resultado['resumen']['no_confirmado']}"
    )

    resultado["evento"] = sesion["event"].name
    resultado["inicio"] = sesion.get("inicio")
    resultado["fin"]    = sesion["fin"]
    resultado["event_id"] = event_id
    registrar_operativos_semanales(resultado)

    _persistir_programado_en_memoria(
        event_id,
        event_id=event_id,
        nombre=sesion["event"].name,
        estado="finalizado",
        fin=_dt_naive_utc(sesion["fin"]),
        fecha_hora=_dt_naive_utc(getattr(sesion["event"], "start_time", None)),
        canal_id=getattr(sesion.get("event"), "channel_id", None),
        mensaje_id=getattr(sesion.get("event"), "message_id", None),
        evento=sesion["event"],
    )

    try:
        await asyncio.to_thread(
            actualizar_operativo_programado_db,
            event_id,
            nombre=sesion["event"].name,
            estado="cerrado",
            fin=_dt_naive_utc(sesion["fin"]),
        )
    except Exception as e:
        logger.warning(f"⚠️ [Asistencia] No se pudo marcar evento cerrado en BD: {e}")

    sesion["resultado_cierre"] = resultado
    return resultado


# ─── CLASIFICACIÓN ────────────────────────────────────────────

def clasificar_asistencia(
    confirmados: set[int],
    participaron: set[int],
    event_id: int,
    event_name: str,
) -> dict:
    """
    Clasifica a todos los miembros de la plantilla + participantes no registrados.

    Lógica:
      ASISTIO       = confirmado AND participó
      NO_CONFIRMADO = NO confirmado AND participó
      FALTO         = confirmado AND NO participó
      AUSENTE       = NO confirmado AND NO participó (miembros de plantilla)
    """
    from asistencia_plantilla import get_plantilla_activa  # importación local para evitar circular

    resultado_miembros: dict[int, EstadoAsistencia] = {}
    plantilla_activa = get_plantilla_activa()
    justificados = _obtener_ids_justificados_evento(event_id, event_name)

    # Evaluar plantilla completa
    for discord_id in plantilla_activa:
        en_confirmados = discord_id in confirmados
        en_participaron = discord_id in participaron

        if en_confirmados and en_participaron:
            resultado_miembros[discord_id] = EstadoAsistencia.ASISTIO
        elif en_confirmados and not en_participaron:
            resultado_miembros[discord_id] = EstadoAsistencia.FALTO
        elif not en_confirmados and en_participaron:
            resultado_miembros[discord_id] = EstadoAsistencia.NO_CONFIRMADO
        elif discord_id in justificados:
            resultado_miembros[discord_id] = EstadoAsistencia.JUSTIFICADO
        else:
            resultado_miembros[discord_id] = EstadoAsistencia.AUSENTE

    # Participantes que retiraron arma pero NO están en la plantilla
    for discord_id in participaron:
        if discord_id not in resultado_miembros:
            resultado_miembros[discord_id] = EstadoAsistencia.NO_CONFIRMADO

    resumen = {
        "asistio":       sum(1 for v in resultado_miembros.values() if v in (EstadoAsistencia.ASISTIO, EstadoAsistencia.NO_CONFIRMADO)),
        "no_confirmado": sum(1 for v in resultado_miembros.values() if v == EstadoAsistencia.NO_CONFIRMADO),
        "falto":         sum(1 for v in resultado_miembros.values() if v == EstadoAsistencia.FALTO),
        "justificado":   sum(1 for v in resultado_miembros.values() if v == EstadoAsistencia.JUSTIFICADO),
        "ausente":       sum(1 for v in resultado_miembros.values() if v == EstadoAsistencia.AUSENTE),
    }

    return {
        "miembros": resultado_miembros,
        "resumen":  resumen,
    }


# ─── REGISTRAR EVENTO (punto de entrada principal) ────────────

async def registrar_evento_operativo(
    event_id: int,
    *,
    tipo: str = "operativo",
    canal_id: Optional[int] = None,
    iniciar_tracking: bool = True,
) -> bool:
    """
    Punto de entrada principal. Llamar con el ID del evento de Discord
    para activar el sistema de asistencia completo.

    Ejemplo desde /inicio_operativo:
        from asistencia import registrar_evento_operativo
        await registrar_evento_operativo(int(event_id))

    Returns True si se registró correctamente.
    """
    if iniciar_tracking and event_id in _sesiones_asistencia:
        logger.warning(f"⚠️ [Asistencia] Evento {event_id} ya está registrado")
        return False

    event = await fetch_event_by_id(event_id)
    if not event:
        return False

    try:
        await asyncio.to_thread(
            guardar_operativo_programado_db,
            event_id=event_id,
            nombre=event.name,
            fecha_hora=_dt_naive_utc(event.start_time),
            canal_id=canal_id,
            mensaje_id=getattr(event, "message_id", None),
            tipo=tipo,
            estado="programado",
        )
        _persistir_programado_en_memoria(
            event_id,
            event_id=event_id,
            nombre=event.name,
            fecha_hora=_dt_naive_utc(event.start_time),
            canal_id=canal_id,
            mensaje_id=getattr(event, "message_id", None),
            tipo=tipo,
            estado="programado",
            evento=event,
        )
    except Exception as e:
        logger.warning(f"⚠️ [Asistencia] No se pudo guardar evento programado en BD: {e}")

    if iniciar_tracking:
        _sesiones_asistencia[event_id] = {
            "event":              event,
            "confirmados_previo": set(),
            "confirmados_inicio": set(),
            "participaron":       set(),
            "inicio":             None,
            "fin":                None,
            "task_preaviso":      None,
            "resultado_cierre":   None,
        }

        # Lanzar tarea de preaviso (espera hasta 5 min antes del inicio)
        if _bot is None:
            logger.warning(
                f"⚠️ [Asistencia] No se pudo programar preaviso para {event_id}: bot no inicializado"
            )
            return False
        task = _crear_tarea(_tarea_preaviso(event_id))
        _sesiones_asistencia[event_id]["task_preaviso"] = task

    logger.info(
        f"✅ [Asistencia] Evento registrado | '{event.name}' | "
        f"ID: {event_id} | Inicio: {event.start_time.strftime('%d/%m/%Y %H:%M')} UTC | "
        f"tipo={tipo} | tracking={'sí' if iniciar_tracking else 'no'}"
    )
    return True


async def cancelar_evento_operativo(event_id: int):
    """Cancela el tracking de un evento (si el OP se cancela antes de empezar)."""
    sesion = _sesiones_asistencia.pop(event_id, None)
    if sesion:
        task = sesion.get("task_preaviso")
        if task and not task.done():
            task.cancel()
    try:
        await asyncio.to_thread(eliminar_operativo_programado_db, event_id)
        state.operativos_programados.pop(int(event_id), None)
        limpiar_cache_evento(event_id)
        invalidar_cache_contexto_justificaciones_db()
    except Exception as e:
        logger.warning(f"⚠️ [Asistencia] No se pudo borrar evento cancelado de BD: {e}")
    logger.info(f"🚫 [Asistencia] Evento {event_id} cancelado")


def get_sesion(event_id: int) -> Optional[dict]:
    """Devuelve la sesión activa para un event_id (para debug o consulta)."""
    return _sesiones_asistencia.get(event_id)


def get_sesiones_activas() -> list[int]:
    """Devuelve los event_ids de los OPs actualmente en tracking."""
    return [
        eid for eid, s in _sesiones_asistencia.items()
        if s.get("inicio") and not s.get("fin")
    ]
