"""
sheets.py — Integración con Google Sheets para registro de asistencia
Vlone X Ballas | ArmamentBot

DEPENDENCIAS:
    pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client

ESTRUCTURA DE HOJAS:
    📋 Plantilla    → Todos los miembros, rangos, estado activo/despedido
    📊 Asistencia   → Una fila por OP, columnas por miembro
    📝 Historial    → Log detallado de cada OP (una fila por miembro por OP)
    ⚙️ Config       → ID del sheet, event_ids vinculados, etc.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from database import get_db_connection

logger = logging.getLogger("ArmamentBot")

# ─── CONFIGURACIÓN ────────────────────────────────────────────
# Setear estas variables de entorno en Railway / .env:
#   SHEETS_CREDENTIALS_PATH → ruta al JSON de service account
#   SHEETS_SPREADSHEET_ID   → ID del Google Sheet (de la URL)
# Compatibilidad:
#   SPREADSHEET_ID          → alias antiguo del ID del Google Sheet

def _get_credentials_path() -> str:
    configured = os.getenv("SHEETS_CREDENTIALS_PATH")
    if configured:
        return configured
    # Compatibilidad con typo histórico en el repo:
    # existe un archivo "sevices_account.json" en lugar de "service_account.json".
    return "sevices_account.json" if os.path.exists("sevices_account.json") else "service_account.json"


def _get_spreadsheet_id() -> str:
    # Priorizamos el nombre nuevo, pero aceptamos el alias viejo para evitar fallos en despliegues existentes.
    return os.getenv("SHEETS_SPREADSHEET_ID", "").strip() or os.getenv("SPREADSHEET_ID", "").strip()


def _get_credentials_json() -> str:
    """
    Devuelve el contenido JSON de la service account desde variable de entorno.
    Útil para Railway donde no se pueden subir archivos fácilmente.
    Si no está seteada, devuelve string vacío y _get_service() usará el archivo local.

    En Railway, setear:
        GOOGLE_CREDENTIALS_JSON={"type":"service_account","project_id":"...","private_key":"..."}
    """
    return os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()


CREDENTIALS_PATH  = _get_credentials_path()
SPREADSHEET_ID    = _get_spreadsheet_id()

# Nombres de las hojas (pestañas)
HOJA_PLANTILLA   = "Plantilla"
HOJA_ASISTENCIA  = "Asistencia"
HOJA_HISTORIAL   = "Historial"
HOJA_ASISTENCIA_SEMANAL = "Asistencia Semanal"
HOJA_JUSTIFICACIONES = "Justificaciones"
HOJA_CONFIG      = "Config"

# ─── COLORES (RGB normalizado 0-1) ────────────────────────────
COLOR_VERDE      = {"red": 0.20, "green": 0.73, "blue": 0.33}   # ✅ Asistió
COLOR_AMARILLO   = {"red": 1.00, "green": 0.84, "blue": 0.00}   # ⚠️ No confirmado
COLOR_ROJO       = {"red": 0.90, "green": 0.22, "blue": 0.21}   # ❌ Faltó
COLOR_GRIS       = {"red": 0.60, "green": 0.60, "blue": 0.60}   # ⬛ Ausente
COLOR_CABECERA   = {"red": 0.29, "green": 0.00, "blue": 0.51}   # Morado Ballas
COLOR_BLANCO     = {"red": 1.00, "green": 1.00, "blue": 1.00}

# Timeout para httplib2 (segundos)
_HTTP_TIMEOUT = 30
# Reintentos ante timeout/error de red
_MAX_REINTENTOS = 3

_service = None  # Google Sheets service instance (lazy init)


# ─── INICIALIZACIÓN ───────────────────────────────────────────

def _get_service():
    """Inicializa y devuelve el servicio de Google Sheets (singleton)."""
    global _service
    global CREDENTIALS_PATH
    CREDENTIALS_PATH = _get_credentials_path()
    if _service is not None:
        return _service

    try:
        import httplib2
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
        credentials_json = _get_credentials_json()
        creds = None

        if credentials_json:
            try:
                creds = service_account.Credentials.from_service_account_info(
                    json.loads(credentials_json), scopes=SCOPES
                )
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(
                    f"⚠️ [Sheets] GOOGLE_CREDENTIALS_JSON inválido ({e}), "
                    f"intentando con archivo '{CREDENTIALS_PATH}'..."
                )

        if creds is None:
            creds = service_account.Credentials.from_service_account_file(
                CREDENTIALS_PATH, scopes=SCOPES
            )

        # Configurar timeout explícito: evita que llamadas a Sheets bloqueen indefinidamente.
        http = httplib2.Http(timeout=_HTTP_TIMEOUT)
        from google_auth_httplib2 import AuthorizedHttp
        authorized_http = AuthorizedHttp(creds, http=http)

        _service = build(
            "sheets", "v4",
            http=authorized_http,
            cache_discovery=False,
        )
        logger.info("✅ [Sheets] Servicio Google Sheets inicializado (timeout=%ds)", _HTTP_TIMEOUT)
        return _service
    except FileNotFoundError:
        logger.error(
            f"❌ [Sheets] No se encontró '{CREDENTIALS_PATH}'. "
            "Descargá el JSON de service account y colocalo en la raíz del bot."
        )
        return None
    except Exception as e:
        logger.error(f"❌ [Sheets] Error inicializando servicio: {e}", exc_info=True)
        return None


def _reset_service():
    """Resetea el singleton para forzar reconexión en el próximo uso."""
    global _service
    _service = None


# ─── HELPERS BÁSICOS ──────────────────────────────────────────

def _valores_a_rango(sheet_name: str, values: list[list], start: str = "A1") -> dict:
    return {
        "range":          f"'{sheet_name}'!{start}",
        "majorDimension": "ROWS",
        "values":         values,
    }


def _limpiar_texto(valor) -> str:
    texto = "" if valor is None else str(valor)
    return " ".join(texto.split()).strip()


def _ejecutar_sync(fn, *args, **kwargs):
    """Ejecuta una llamada a la API de Sheets con reintentos ante timeout."""
    import time

    ultimo_error = None
    for intento in range(1, _MAX_REINTENTOS + 1):
        try:
            return fn(*args, **kwargs).execute()
        except TimeoutError as e:
            ultimo_error = e
            logger.warning(
                f"⚠️ [Sheets] Timeout en llamada API (intento {intento}/{_MAX_REINTENTOS}): {e}"
            )
            _reset_service()
            if intento < _MAX_REINTENTOS:
                time.sleep(2 * intento)
        except Exception as e:
            err_str = str(e)
            if "Request-sent" in err_str or "timed out" in err_str.lower():
                ...
            else:
                logger.error(f"❌ [Sheets] Error en llamada API: {e}", exc_info=True)
                return None

        # DESPUÉS
        except Exception as e:
            err_str = str(e)
            _is_network = (
                "Request-sent" in err_str
                or "timed out" in err_str.lower()
                or "record layer failure" in err_str.lower()
                or "ssl" in err_str.lower()
                or isinstance(e, OSError)
            )
            if _is_network:
                ultimo_error = e
                logger.warning(
                    f"⚠️ [Sheets] Error de red (intento {intento}/{_MAX_REINTENTOS}): {e}"
                )
                _reset_service()
                if intento < _MAX_REINTENTOS:
                    time.sleep(2 * intento)
            else:
                logger.error(f"❌ [Sheets] Error en llamada API: {e}", exc_info=True)
                return None
    logger.error(f"❌ [Sheets] Todos los reintentos fallaron. Último error: {ultimo_error}")
    return None


async def _ejecutar_async(fn, *args, **kwargs):
    """Wrapper async para no bloquear el event loop."""
    return await asyncio.to_thread(_ejecutar_sync, fn, *args, **kwargs)


# ─── SETUP INICIAL DEL SPREADSHEET ───────────────────────────

async def setup_spreadsheet():
    """
    Crea las hojas con sus cabeceras si no existen.
    Llamar una sola vez desde /setup o on_ready del bot.
    """
    global SPREADSHEET_ID
    SPREADSHEET_ID = _get_spreadsheet_id()
    svc = _get_service()
    if not svc or not SPREADSHEET_ID:
        logger.error(
            "❌ [Sheets] setup_spreadsheet: servicio o SPREADSHEET_ID no configurado "
            f"(SHEETS_SPREADSHEET_ID={'set' if os.getenv('SHEETS_SPREADSHEET_ID') else 'missing'}, "
            f"SPREADSHEET_ID={'set' if os.getenv('SPREADSHEET_ID') else 'missing'}, "
            f"credenciales='{CREDENTIALS_PATH}')"
        )
        return False

    sheets_api = svc.spreadsheets()

    # Obtener hojas existentes
    meta = await _ejecutar_async(sheets_api.get, spreadsheetId=SPREADSHEET_ID)
    if not meta:
        return False

    hojas_existentes = {s["properties"]["title"] for s in meta["sheets"]}

    # Crear hojas que falten
    requests_batch = []
    for nombre_hoja in [HOJA_PLANTILLA, HOJA_ASISTENCIA, HOJA_HISTORIAL, HOJA_ASISTENCIA_SEMANAL, HOJA_JUSTIFICACIONES, HOJA_CONFIG]:
        if nombre_hoja not in hojas_existentes:
            requests_batch.append({
                "addSheet": {
                    "properties": {"title": nombre_hoja}
                }
            })

    if requests_batch:
        await _ejecutar_async(
            sheets_api.batchUpdate,
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": requests_batch},
        )
        logger.info(f"✅ [Sheets] Hojas creadas: {[r['addSheet']['properties']['title'] for r in requests_batch]}")

    # Escribir cabeceras
    await _escribir_cabecera_plantilla()
    await _escribir_cabecera_asistencia()
    await _escribir_cabecera_historial()
    await _escribir_cabecera_asistencia_semanal()
    await _escribir_cabecera_justificaciones()
    await _escribir_config_inicial()

    logger.info("✅ [Sheets] Spreadsheet configurado correctamente")
    return True


async def _escribir_cabecera_plantilla():
    svc = _get_service()
    if not svc:
        return
    sheets_api = svc.spreadsheets()
    valores = [
        ["#", "Nombre IC", "Discord Tag", "Discord ID", "Rango", "Steam", "Estado", "Fecha Ingreso", "Fecha Baja", "Notas"]
    ]
    await _ejecutar_async(
        sheets_api.values().update,
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{HOJA_PLANTILLA}'!A1",
        valueInputOption="RAW",
        body={"values": valores},
    )


async def _escribir_cabecera_asistencia():
    svc = _get_service()
    if not svc:
        return
    sheets_api = svc.spreadsheets()
    valores = [
        ["Fecha", "Evento", "Event ID", "Total Miembros",
         "? Asistieron", "? Faltaron", "?? No Confirmados", "?? Justificados", "? Ausentes",
         "% Asistencia", "Duraci?n", "Notas"]
    ]
    await _ejecutar_async(
        sheets_api.values().update,
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{HOJA_ASISTENCIA}'!A1",
        valueInputOption="RAW",
        body={"values": valores},
    )


async def _escribir_cabecera_historial():
    svc = _get_service()
    if not svc:
        return
    sheets_api = svc.spreadsheets()
    valores = [
        ["Fecha", "Evento", "Event ID", "Discord ID", "Nombre IC", "Discord Tag",
         "Rango", "Estado", "Confirmó Evento", "Participó (retiró arma)"]
    ]
    await _ejecutar_async(
        sheets_api.values().update,
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{HOJA_HISTORIAL}'!A1",
        valueInputOption="RAW",
        body={"values": valores},
    )


async def _escribir_cabecera_asistencia_semanal():
    svc = _get_service()
    if not svc:
        return
    sheets_api = svc.spreadsheets()
    valores = [
        [
            "Semana Inicio",
            "Discord ID",
            "Nombre IC",
            "Discord Tag",
            "Rango",
            "Operativos Realizados",
            "Objetivo",
            "Faltan",
            "Justificado",
            "Aviso Enviado",
            "Actualizado",
        ]
    ]
    await _ejecutar_async(
        sheets_api.values().update,
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{HOJA_ASISTENCIA_SEMANAL}'!A1",
        valueInputOption="RAW",
        body={"values": valores},
    )


async def _escribir_cabecera_justificaciones():
    svc = _get_service()
    if not svc:
        return
    sheets_api = svc.spreadsheets()
    valores = [
        [
            "Fecha",
            "Usuario",
            "Discord ID",
            "Tipo",
            "Subtipo",
            "Texto",
            "Mensaje Origen ID",
            "Canal Origen ID",
        ]
    ]
    await _ejecutar_async(
        sheets_api.values().update,
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{HOJA_JUSTIFICACIONES}'!A1",
        valueInputOption="RAW",
        body={"values": valores},
    )


async def _escribir_config_inicial():
    svc = _get_service()
    if not svc:
        return
    sheets_api = svc.spreadsheets()
    valores = [
        ["Clave",              "Valor"],
        ["spreadsheet_id",     SPREADSHEET_ID],
        ["organization",       "Vlone X Ballas"],
        ["guild_id",           "968286555150110790"],
        ["ultima_actualizacion", datetime.now(tz=timezone.utc).strftime("%d/%m/%Y %H:%M")],
    ]
    await _ejecutar_async(
        sheets_api.values().update,
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{HOJA_CONFIG}'!A1",
        valueInputOption="RAW",
        body={"values": valores},
    )


# ─── SINCRONIZAR PLANTILLA ────────────────────────────────────

async def sincronizar_plantilla():
    """
    Escribe/actualiza la hoja Plantilla con todos los miembros actuales.
    Llamar tras /contratar o /despedir, o periódicamente.
    """
    from asistencia_plantilla import PLANTILLA, refresh_plantilla

    global SPREADSHEET_ID
    SPREADSHEET_ID = _get_spreadsheet_id()
    svc = _get_service()
    if not svc or not SPREADSHEET_ID:
        return False

    sheets_api = svc.spreadsheets()

    refresh_plantilla(force=True)

    # Cabecera
    filas = [
        ["#", "Nombre IC", "Discord Tag", "Discord ID", "Rango", "Steam", "Estado", "Notas"]
    ]

    # Ordenar por rango (de mayor a menor) luego por nombre
    orden_rangos = {
        "Purple Ghost": 0,
        "Purple Curse": 1,
        "Purple Soul": 2,
        "Purple Demon": 3,
        "Purple Venom": 4,
        "Baby Purple": 5,
    }
    miembros_ordenados = sorted(
        PLANTILLA.items(),
        key=lambda x: (orden_rangos.get(x[1].get("rango", "Baby Purple"), 99), x[1].get("nombre_ic", ""))
    )

    for idx, (discord_id, info) in enumerate(miembros_ordenados, 1):
        estado = "✅ Activo" if info.get("activo", True) else "🚫 Despedido"
        filas.append([
            idx,
            _limpiar_texto(info.get("nombre_ic", "")),
            _limpiar_texto(info.get("discord_tag", "")),
            str(discord_id),
            _limpiar_texto(info.get("rango", "")),
            _limpiar_texto(info.get("steam", "")),
            estado,
            "",  # Notas (editable manualmente)
        ])

    await _ejecutar_async(
        sheets_api.values().update,
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{HOJA_PLANTILLA}'!A1",
        valueInputOption="RAW",
        body={"values": filas},
    )
    logger.info(f"✅ [Sheets] Plantilla sincronizada | {len(filas) - 1} miembros")
    return True


# ─── REGISTRAR RESULTADO DE ASISTENCIA ───────────────────────

async def registrar_asistencia_op(resultado: dict) -> bool:
    """
    Escribe el resultado de un OP en las hojas Asistencia e Historial.

    resultado: el dict devuelto por handle_op_end() en asistencia.py
    """
    from asistencia import EstadoAsistencia
    from asistencia_plantilla import get_info_miembro

    global SPREADSHEET_ID
    SPREADSHEET_ID = _get_spreadsheet_id()
    svc = _get_service()
    if not svc or not SPREADSHEET_ID:
        logger.error("❌ [Sheets] No configurado. Revisar SHEETS_SPREADSHEET_ID y credenciales.")
        return False

    sheets_api = svc.spreadsheets()

    nombre_evento = resultado.get("evento", "Operativo")
    event_id      = resultado.get("event_id", "")
    inicio        = resultado.get("inicio")
    fin           = resultado.get("fin")
    resumen       = resultado.get("resumen", {})
    miembros      = resultado.get("miembros", {})

    fecha_str = inicio.strftime("%d/%m/%Y %H:%M") if inicio else datetime.now().strftime("%d/%m/%Y %H:%M")
    duracion  = ""
    if inicio and fin:
        delta   = fin - inicio
        minutos = int(delta.total_seconds() // 60)
        duracion = f"{minutos // 60}h {minutos % 60}m"

    total      = len(miembros)
    asistieron = resumen.get("asistio", 0)
    pct        = f"{(asistieron / total * 100):.1f}%" if total > 0 else "0%"

    # ── Hoja Asistencia: fila resumen del OP ──────────────────
    fila_resumen = [[
        fecha_str,
        nombre_evento,
        str(event_id),
        total,
        asistieron,
        resumen.get("falto", 0),
        resumen.get("no_confirmado", 0),
        resumen.get("justificado", 0),
        resumen.get("ausente", 0),
        pct,
        duracion,
        "",  # Notas
    ]]

    await _ejecutar_async(
        sheets_api.values().append,
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{HOJA_ASISTENCIA}'!A:L",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": fila_resumen},
    )

    # ── Hoja Historial: una fila por miembro ──────────────────
    # Ordenar por estado para que sea más legible
    orden_estado = {
        EstadoAsistencia.ASISTIO:       0,
        EstadoAsistencia.NO_CONFIRMADO: 1,
        EstadoAsistencia.FALTO:         2,
        EstadoAsistencia.JUSTIFICADO:   3,
        EstadoAsistencia.AUSENTE:       4,
    }
    miembros_ordenados = sorted(
        miembros.items(),
        key=lambda x: (orden_estado.get(x[1], 9), get_info_miembro(x[0]).get("nombre_ic", ""))
    )

    filas_historial = []
    for discord_id, estado in miembros_ordenados:
        info = get_info_miembro(discord_id)
        confirmo   = "SÍ" if estado in (EstadoAsistencia.ASISTIO, EstadoAsistencia.FALTO) else "NO"
        participo  = "SÍ" if estado in (EstadoAsistencia.ASISTIO, EstadoAsistencia.NO_CONFIRMADO) else "NO"
        filas_historial.append([
            fecha_str,
            nombre_evento,
            str(event_id),
            str(discord_id),
            info.get("nombre_ic", f"ID {discord_id}"),
            info.get("discord_tag", ""),
            info.get("rango", ""),
            estado.value,       # "✅ ASISTIÓ", "❌ FALTÓ", etc.
            confirmo,
            participo,
        ])

    if filas_historial:
        await _ejecutar_async(
            sheets_api.values().append,
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{HOJA_HISTORIAL}'!A:J",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": filas_historial},
        )

    logger.info(
        f"✅ [Sheets] Asistencia registrada | OP: '{nombre_evento}' | "
        f"Filas historial: {len(filas_historial)}"
    )
    return True


async def sincronizar_asistencia_semanal() -> bool:
    """
    Refresca la hoja 'Asistencia Semanal' desde la tabla local de Postgres.
    """
    from asistencia_plantilla import get_plantilla_activa, get_info_miembro
    from asistencia import _inicio_semana_actual
    from config import ASISTENCIA_SEMANAL_OBJETIVO

    global SPREADSHEET_ID
    SPREADSHEET_ID = _get_spreadsheet_id()
    svc = _get_service()
    if not svc or not SPREADSHEET_ID:
        return False

    week_start = _inicio_semana_actual().date().isoformat()
    rows_db = {}
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
        for row in cursor.fetchall() or []:
            try:
                did = int(row["discord_id"])
            except (TypeError, ValueError):
                continue
            rows_db[did] = {
                "operativos_realizados": int(row.get("operativos_realizados") or 0),
                "justificado": bool(row.get("justificado")),
                "aviso_enviado": bool(row.get("aviso_enviado")),
            }
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ [Sheets] Error leyendo asistencia semanal: {e}", exc_info=True)
        return False

    plantilla = get_plantilla_activa()
    filas = [[
        "Semana Inicio",
        "Discord ID",
        "Nombre IC",
        "Discord Tag",
        "Rango",
        "Operativos Realizados",
        "Objetivo",
        "Faltan",
        "Justificado",
        "Aviso Enviado",
        "Actualizado",
    ]]

    for discord_id, info in sorted(plantilla.items(), key=lambda x: x[1].get("nombre_ic", "")):
        data = rows_db.get(int(discord_id), {"operativos_realizados": 0, "justificado": False, "aviso_enviado": False})
        realizados = int(data.get("operativos_realizados") or 0)
        faltan = max(0, ASISTENCIA_SEMANAL_OBJETIVO - realizados)
        justificado = bool(data.get("justificado", False))
        filas.append([
            week_start,
            str(discord_id),
            _limpiar_texto(info.get("nombre_ic", "")),
            _limpiar_texto(info.get("discord_tag", "")),
            _limpiar_texto(info.get("rango", "")),
            realizados,
            ASISTENCIA_SEMANAL_OBJETIVO,
            faltan,
            "SÍ" if justificado else "NO",
            "SÍ" if data.get("aviso_enviado") else "NO",
            datetime.now(tz=timezone.utc).strftime("%d/%m/%Y %H:%M"),
        ])

    sheets_api = svc.spreadsheets()
    try:
        await _ejecutar_async(
            sheets_api.values().clear,
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{HOJA_ASISTENCIA_SEMANAL}'",
        )
    except Exception:
        pass

    await _ejecutar_async(
        sheets_api.values().update,
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{HOJA_ASISTENCIA_SEMANAL}'!A1",
        valueInputOption="RAW",
        body={"values": filas},
    )
    logger.info(f"✅ [Sheets] Asistencia semanal sincronizada | semana={week_start} | filas={len(filas)-1}")
    return True


async def registrar_justificacion_texto(row: dict) -> bool:
    global SPREADSHEET_ID
    SPREADSHEET_ID = _get_spreadsheet_id()
    svc = _get_service()
    if not svc or not SPREADSHEET_ID:
        return False

    sheets_api = svc.spreadsheets()
    valores = [[
        row.get("created_at") or datetime.now(tz=timezone.utc).strftime("%d/%m/%Y %H:%M"),
        row.get("usuario", ""),
        str(row.get("discord_id", "")),
        row.get("tipo", ""),
        row.get("subtipo", ""),
        row.get("texto", ""),
        str(row.get("mensaje_origen_id", "")),
        str(row.get("canal_origen_id", "")),
    ]]

    await _ejecutar_async(
        sheets_api.values().append,
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{HOJA_JUSTIFICACIONES}'!A:H",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": valores},
    )
    return True


# ─── LEER ASISTENCIA DE UN MIEMBRO ───────────────────────────

async def get_historial_miembro(discord_id: int, ultimos_n: int = 10) -> list[dict]:
    """
    Devuelve los últimos N registros de asistencia de un miembro.
    Útil para /perfil o consultas rápidas.
    """
    global SPREADSHEET_ID
    SPREADSHEET_ID = _get_spreadsheet_id()
    svc = _get_service()
    if not svc or not SPREADSHEET_ID:
        return []

    sheets_api = svc.spreadsheets()
    result     = await _ejecutar_async(
        sheets_api.values().get,
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{HOJA_HISTORIAL}'!A:J",
    )
    if not result:
        return []

    rows   = result.get("values", [])
    header = rows[0] if rows else []
    target = str(discord_id)

    registros = [
        dict(zip(header, row))
        for row in rows[1:]
        if len(row) > 3 and row[3] == target
    ]
    return registros[-ultimos_n:]
