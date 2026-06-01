"""
Cache dinamica de la plantilla de miembros.

La fuente de verdad es el Apps Script / Google Sheets. Este modulo mantiene
una cache local mutable para compatibilidad con el resto del bot.
"""

import json
import logging
import os
import threading
import time
from urllib import error as urllib_error
from urllib import request as urllib_request

logger = logging.getLogger("ArmamentBot")

PLANTILLA: dict[int, dict] = {}

_PLANTILLA_TTL_SECONDS = 60
_last_refresh_at = 0.0
_refresh_lock = threading.Lock()


def _get_script_url() -> str:
    return os.getenv("APPS_SCRIPT_URL", "").strip()


def _normalizar_bool(valor) -> bool:
    if isinstance(valor, bool):
        return valor
    texto = str(valor or "").strip().lower()
    return texto in {"1", "true", "si", "sí", "activo", "✅ activo"}


def _normalizar_miembro(miembro: dict) -> tuple[int, dict] | None:
    try:
        discord_id = int(str(miembro.get("discord_id") or "").strip())
    except (TypeError, ValueError):
        return None

    if discord_id <= 0:
        return None

    estado = miembro.get("estado")
    activo = miembro.get("activo")
    if activo is None:
        activo = _normalizar_bool(estado) if estado is not None else True
    else:
        activo = _normalizar_bool(activo)

    return discord_id, {
        "nombre_ic": str(miembro.get("nombre_ic") or "").strip(),
        "discord_tag": str(miembro.get("discord_tag") or "").strip(),
        "rango": str(miembro.get("rango") or "").strip(),
        "steam": str(miembro.get("steam") or "").strip(),
        "activo": bool(activo),
    }


def _fetch_remote_plantilla() -> dict[int, dict] | None:
    url = _get_script_url()
    if not url or "/exec" not in url:
        return None

    payload = json.dumps({"action": "get_plantilla", "data": {}}).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib_request.urlopen(req, timeout=30) as resp:
            resultado = json.loads(resp.read().decode("utf-8"))
    except urllib_error.HTTPError as e:
        logger.warning("⚠️ [Plantilla] HTTP %s refrescando plantilla remota", e.code)
        return None
    except Exception as e:
        logger.warning("⚠️ [Plantilla] No se pudo refrescar plantilla remota: %s", e)
        return None

    if not isinstance(resultado, dict) or not resultado.get("ok"):
        return None

    miembros = resultado.get("miembros") or []
    remota: dict[int, dict] = {}
    for miembro in miembros:
        if not isinstance(miembro, dict):
            continue
        normalizado = _normalizar_miembro(miembro)
        if normalizado is None:
            continue
        discord_id, info = normalizado
        remota[discord_id] = info
    return remota


def refresh_plantilla(force: bool = False) -> dict[int, dict]:
    global _last_refresh_at

    now = time.time()
    if not force and PLANTILLA and (now - _last_refresh_at) < _PLANTILLA_TTL_SECONDS:
        return PLANTILLA

    with _refresh_lock:
        now = time.time()
        if not force and PLANTILLA and (now - _last_refresh_at) < _PLANTILLA_TTL_SECONDS:
            return PLANTILLA

        remota = _fetch_remote_plantilla()
        if remota is not None:
            PLANTILLA.clear()
            PLANTILLA.update(remota)
            _last_refresh_at = now
            logger.info("ℹ️ [Plantilla] Cache actualizada desde Apps Script | miembros=%s", len(PLANTILLA))

    return PLANTILLA


def get_plantilla_completa() -> dict[int, dict]:
    return dict(refresh_plantilla())


def get_info_miembro(discord_id: int) -> dict:
    """Devuelve info del miembro o un dict vacio si no esta en la plantilla."""
    refresh_plantilla()
    return PLANTILLA.get(discord_id, {
        "nombre_ic": f"Desconocido ({discord_id})",
        "discord_tag": "",
        "rango": "-",
        "steam": "",
        "activo": False,
    })


def agregar_miembro(discord_id: int, nombre_ic: str, discord_tag: str, rango: str, steam: str = "") -> bool:
    """
    Actualiza solo la cache local.
    La alta real debe hacerse via Apps Script.
    """
    refresh_plantilla()
    if discord_id in PLANTILLA and PLANTILLA[discord_id].get("activo", True):
        return False

    PLANTILLA[discord_id] = {
        "nombre_ic": nombre_ic,
        "discord_tag": discord_tag,
        "rango": rango,
        "steam": steam,
        "activo": True,
    }
    return True


def despedir_miembro(discord_id: int) -> bool:
    """
    Marca un miembro como inactivo solo en cache local.
    La baja real debe hacerse via Apps Script.
    """
    refresh_plantilla()
    if discord_id not in PLANTILLA:
        return False
    PLANTILLA[discord_id]["activo"] = False
    return True


def get_plantilla_activa() -> dict[int, dict]:
    """Devuelve solo los miembros activos."""
    refresh_plantilla()
    return {did: info for did, info in PLANTILLA.items() if info.get("activo", True)}


try:
    refresh_plantilla(force=True)
except Exception:
    pass
