"""
sheets_plantilla.py - Integracion del bot con Google Apps Script para la plantilla.
"""

import asyncio
import json
import logging
import os
import re
from typing import Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

import discord

from asistencia_plantilla import despedir_miembro
from config import PLANTILLA_AUTOMATICA_CHANNEL_ID

logger = logging.getLogger("ArmamentBot")

_APPS_SCRIPT_URL = os.getenv("APPS_SCRIPT_URL", "").strip()
CHECK_EMOJI = "<a:Check:1490022575634382989>"

_RANGOS_VALIDOS = (
    "Purple Ghost",
    "Purple Curse",
    "Purple Soul",
    "Purple Demon",
    "Purple Venom",
    "Baby Purple",
)

_RANGO_ALIASES = {
    "PURPLE GHOST": "Purple Ghost",
    "PURPLE CURSE": "Purple Curse",
    "PURPLE SOUL": "Purple Soul",
    "PURPLE DEMON": "Purple Demon",
    "PURPLE VENOM": "Purple Venom",
    "BABY PURPLE": "Baby Purple",
}

_AVISO_CANAL_PLANTILLA = (
    "Ese canal es solo para enviar plantillas de mafia.\n\n"
    "Usa el formato de plantilla con estos campos:\n"
    "Nombre IC:\n"
    "ID de Discord:\n"
    "Rango:\n"
    "Steam:"
)


def _normalizar_rango(rango: str) -> str:
    texto = " ".join((rango or "").split()).upper()
    return _RANGO_ALIASES.get(texto, texto.title())


def _limpiar_linea_plantilla(linea: str) -> str:
    return re.sub(r"^[\s\-–—•]+", "", (linea or "").strip())


def _parsear_bloque_plantilla(texto: str) -> dict:
    lines = [_limpiar_linea_plantilla(line) for line in (texto or "").splitlines()]
    modo_out = any(line.lower().startswith("out:") for line in lines)

    campos = {
        "steam": "",
        "nombre_ic": "",
        "discord_id": "",
        "discord_tag": "",
        "rango": "",
    }

    for line in lines:
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("out:"):
            continue
        if lower.startswith("link steam:") or lower.startswith("steam:"):
            campos["steam"] = line.split(":", 1)[1].strip()
            continue
        if lower.startswith("nombre ic:"):
            campos["nombre_ic"] = line.split(":", 1)[1].strip()
            continue
        if lower.startswith("discord id:") or lower.startswith("id de discord:"):
            raw = line.split(":", 1)[1].strip()
            match = re.match(r"^(?P<id>\d+)(?:\s*-\s*(?P<tag>.+))?$", raw)
            if not match:
                raise ValueError("Discord ID invalido")
            campos["discord_id"] = match.group("id")
            campos["discord_tag"] = (match.group("tag") or "").strip()
            continue
        if lower.startswith("rango:"):
            campos["rango"] = _normalizar_rango(line.split(":", 1)[1].strip())
            continue

    if not campos["discord_id"]:
        raise ValueError("Falta Discord ID")
    if not campos["nombre_ic"]:
        raise ValueError("Falta Nombre IC")
    if not campos["rango"]:
        raise ValueError("Falta Rango")

    if campos["rango"] not in set(_RANGO_ALIASES.values()):
        raise ValueError(
            "Rango no reconocido. Rangos validos: "
            + ", ".join(_RANGOS_VALIDOS)
        )

    return {"modo_out": modo_out, "campos": campos}


def _contiene_campos_minimos_plantilla(texto: str) -> bool:
    lower = (texto or "").lower()
    patrones = (
        r"\bnombre\s+ic\s*:",
        r"\b(?:id\s+de\s+discord|discord\s+id)\s*:",
        r"\brango\s*:",
        r"\b(?:steam|link\s+steam)\s*:",
    )
    return all(re.search(patron, lower) for patron in patrones)


async def _avisar_plantilla_por_dm(message: discord.Message, detalle: str = "") -> None:
    aviso = _AVISO_CANAL_PLANTILLA
    if detalle:
        aviso += f"\n\nDetalle: {detalle}"
    try:
        await message.author.send(aviso)
    except discord.Forbidden:
        try:
            await message.reply(
                "Te intenté mandar por DM cómo usar este canal, pero tenés los DMs cerrados.",
                mention_author=False,
                delete_after=10,
            )
        except Exception:
            pass


async def _limpiar_mensaje_plantilla_invalido(message: discord.Message) -> None:
    try:
        await message.delete()
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass


def _get_script_url() -> str:
    return os.getenv("APPS_SCRIPT_URL", "").strip()


def _diagnosticar_script_url(url: str) -> Optional[str]:
    if not url:
        return "APPS_SCRIPT_URL no configurada."
    if "/exec" not in url:
        return "APPS_SCRIPT_URL parece no ser la URL de despliegue /exec."
    return None


async def _reordenar_sheets_y_doc() -> None:
    """
    Compacta la hoja de plantilla y luego re-sincroniza el Doc.
    Esto corrige huecos y hace que la columna # vuelva a ser correlativa.
    """
    try:
        from sheets import sincronizar_plantilla

        ok_sheets = await sincronizar_plantilla()
        logger.info("ℹ️ [PlantillaDoc] Resync Sheets tras cambio | ok=%s", ok_sheets)
    except Exception as e:
        logger.warning(f"⚠️ [PlantillaDoc] No se pudo re-sincronizar Sheets: {e}")
        return

    try:
        resultado_doc = await sincronizar_doc_desde_sheets()
        logger.info("ℹ️ [PlantillaDoc] Resync Doc tras cambio | ok=%s", resultado_doc)
    except Exception as e:
        logger.warning(f"⚠️ [PlantillaDoc] No se pudo re-sincronizar Doc tras la baja local: {e}")


async def _llamar_script(action: str, data: dict = None) -> Optional[dict]:
    url = _get_script_url()
    diagnostico = _diagnosticar_script_url(url)
    if diagnostico:
        logger.warning(f"⚠️ [PlantillaDoc] {diagnostico} Llamada omitida")
        return None

    payload = json.dumps({"action": action, "data": data or {}}).encode("utf-8")

    def _do_request():
        req = urllib_request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        return await asyncio.to_thread(_do_request)
    except urllib_error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            pass
        logger.error(f"❌ [PlantillaDoc] HTTP {e.code}: {body[:200]}")
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = {}
        mensaje = parsed.get("mensaje") or parsed.get("error") or ""
        if not mensaje:
            if e.code == 401:
                mensaje = (
                    "La Web App de Google Apps Script no está pública o la URL configurada "
                    "no es la de /exec."
                )
            else:
                mensaje = f"Error HTTP {e.code} al hablar con el Apps Script."
        return {"ok": False, "mensaje": mensaje, "error": mensaje}
    except Exception as e:
        logger.error(f"❌ [PlantillaDoc] Error llamando al script: {e}", exc_info=True)
        return {"ok": False, "mensaje": f"Error conectando al script: {e}", "error": str(e)}


async def sincronizar_doc_desde_sheets() -> bool:
    resultado = await _llamar_script("sync_desde_sheets")
    if not resultado:
        return False
    if resultado.get("ok"):
        logger.info(f"✅ [PlantillaDoc] Doc sincronizado: {resultado.get('resultado', '')}")
        return True
    logger.warning(f"⚠️ [PlantillaDoc] Error sincronizando doc: {resultado.get('error', '')}")
    return False


async def agregar_miembro_a_doc(
    discord_id: int,
    nombre_ic: str,
    rango: str,
    discord_tag: str = "",
    steam: str = "",
) -> dict:
    resultado = await _llamar_script(
        "agregar_miembro",
        {
            "discord_id": str(discord_id),
            "nombre_ic": nombre_ic,
            "rango": rango,
            "discord_tag": discord_tag,
            "steam": steam,
        },
    )
    if not resultado:
        return {"ok": False, "mensaje": "Error conectando al script", "ya_existe": False}
    salida = {
        "ok": bool(resultado.get("ok")),
        "mensaje": resultado.get("error") or resultado.get("mensaje") or "",
        "ya_existe": bool(resultado.get("ya_existe")),
    }
    if salida["ok"]:
        try:
            from asistencia_plantilla import refresh_plantilla

            refresh_plantilla(force=True)
        except Exception as e:
            logger.warning(f"⚠️ [PlantillaDoc] No se pudo refrescar cache local tras alta: {e}")
        await _reordenar_sheets_y_doc()
    return salida


async def despedir_miembro_en_doc(discord_id: int) -> dict:
    """
    Da de baja a un miembro:
      1. Llama al Apps Script → limpia el bloque en el Doc y borra de su Sheets interno.
      2. Si el script responde ok → actualiza también el dict Python (asistencia_plantilla)
         y sincroniza el Sheets de Python (sheets.py) para que ambas fuentes queden alineadas.
      3. Si el script falla → intenta fallback local.
    """
    resultado = await _llamar_script("despedir_miembro", {"discord_id": str(discord_id)})

    if not resultado:
        return await _despedir_miembro_local(discord_id, "Error conectando al script")

    if not resultado.get("ok"):
        error = str(resultado.get("error") or resultado.get("mensaje") or "")
        if "indexOf is not a function" in error:
            logger.warning(
                "⚠️ [PlantillaDoc] Apps Script devolvio error de tipo al dar de baja; usando fallback local"
            )
            return await _despedir_miembro_local(discord_id, error)
        # Cualquier otro error del script → fallback local también
        return await _despedir_miembro_local(discord_id, error)

    # ── Apps Script OK ────────────────────────────────────────────────────────
    # El Apps Script ya actualizó su Sheets interno y limpió el Doc.
    # Ahora sincronizamos el dict Python y el Sheets de Python para que
    # ambas fuentes de datos estén alineadas.
    try:
        despedir_miembro(discord_id)  # marca activo=False en asistencia_plantilla.PLANTILLA
        from asistencia_plantilla import refresh_plantilla

        refresh_plantilla(force=True)
        logger.info(f"✅ [PlantillaDoc] Miembro {discord_id} marcado inactivo en PLANTILLA Python")
    except Exception as e:
        logger.warning(f"⚠️ [PlantillaDoc] No se pudo actualizar PLANTILLA Python para {discord_id}: {e}")

    try:
        from sheets import sincronizar_plantilla
        await sincronizar_plantilla()
        logger.info(f"✅ [PlantillaDoc] Sheets Python sincronizado tras despido de {discord_id}")
    except Exception as e:
        logger.warning(f"⚠️ [PlantillaDoc] No se pudo sincronizar Sheets Python tras despido de {discord_id}: {e}")

    return {
        "ok": True,
        "mensaje": resultado.get("mensaje") or "",
        "out": resultado.get("out") or "",
    }


async def _despedir_miembro_local(discord_id: int, mensaje_error: str = "") -> dict:
    """
    Fallback cuando el Apps Script no responde o falla.
    Actualiza solo el dict Python y sincroniza el Sheets de Python.
    El Doc queda sin actualizar (se avisa en el log).
    """
    from sheets import sincronizar_plantilla

    if not despedir_miembro(discord_id):
        return {
            "ok": False,
            "mensaje": mensaje_error or "No se pudo dar de baja.",
            "out": "",
        }

    logger.warning(
        f"⚠️ [PlantillaDoc] Baja local para {discord_id} "
        f"(Apps Script no disponible: {mensaje_error or 'sin detalle'}). "
        "El Doc puede necesitar actualización manual."
    )

    try:
        await sincronizar_plantilla()
    except Exception as e:
        logger.warning(f"⚠️ [PlantillaDoc] No se pudo sincronizar Sheets tras la baja local: {e}")

    try:
        await sincronizar_doc_desde_sheets()
    except Exception as e:
        logger.warning(f"⚠️ [PlantillaDoc] No se pudo sincronizar Doc tras la baja local: {e}")

    return {
        "ok": True,
        "mensaje": "Baja aplicada localmente y sincronizada con Sheets (Doc puede necesitar revisión).",
        "out": "",
    }


async def procesar_texto_plantilla(texto: str) -> dict:
    resultado = await _llamar_script("sync_miembro_desde_mensaje", {"texto": texto})
    if not resultado:
        return {"ok": False, "mensaje": "Error conectando al script", "ya_existe": False}
    if resultado.get("ok") or resultado.get("ya_existe"):
        try:
            from asistencia_plantilla import refresh_plantilla

            refresh_plantilla(force=True)
        except Exception as e:
            logger.warning(f"⚠️ [PlantillaDoc] No se pudo refrescar cache local tras sync: {e}")
    return {
        "ok": bool(resultado.get("ok")),
        "mensaje": resultado.get("error") or resultado.get("mensaje") or "",
        "miembro": resultado.get("miembro") or {},
        "ya_existe": bool(resultado.get("ya_existe")),
    }


async def obtener_plantilla_doc() -> list:
    resultado = await _llamar_script("get_plantilla")
    if not resultado or not resultado.get("ok"):
        return []
    return resultado.get("miembros") or []


async def revisar_cambios_doc() -> dict:
    resultado = await _llamar_script("revisar_cambios_doc")
    if not resultado:
        return {"ok": False, "changed": False, "mensaje": "Error conectando al script"}
    return {
        "ok": bool(resultado.get("ok")),
        "changed": bool(resultado.get("changed")),
        "mensaje": resultado.get("mensaje") or "",
        "source": resultado.get("source") or "",
        "hash": resultado.get("hash") or "",
        "last_hash": resultado.get("last_hash") or "",
    }


async def manejar_mensaje_plantilla_automatica(message: discord.Message) -> bool:
    if not PLANTILLA_AUTOMATICA_CHANNEL_ID:
        return False
    if message.channel.id != PLANTILLA_AUTOMATICA_CHANNEL_ID:
        return False
    if message.author.bot or message.webhook_id:
        return False
    if not message.content or not message.content.strip():
        return False

    texto = message.content.strip()

    if not _contiene_campos_minimos_plantilla(texto):
        await _avisar_plantilla_por_dm(message)
        await _limpiar_mensaje_plantilla_invalido(message)
        return True

    try:
        parsed = _parsear_bloque_plantilla(texto)
    except Exception as e:
        await _avisar_plantilla_por_dm(message, str(e))
        await _limpiar_mensaje_plantilla_invalido(message)
        return True

    campos = parsed["campos"]
    modo_out = parsed["modo_out"]

    if modo_out:
        resultado = await despedir_miembro_en_doc(int(campos["discord_id"]))
        if resultado.get("ok"):
            try:
                await message.add_reaction(CHECK_EMOJI)
            except Exception:
                pass
            return True

        error = resultado.get("mensaje") or resultado.get("out") or "No se pudo dar de baja."
        await _avisar_plantilla_por_dm(message, error)
        return True

    resultado = await procesar_texto_plantilla(texto)
    if resultado.get("ok"):
        try:
            await message.add_reaction(CHECK_EMOJI)
        except Exception:
            pass

        if resultado.get("ya_existe"):
            try:
                await message.reply("ℹ️ Ya está en plantilla.", mention_author=False)
            except Exception:
                pass
        return True

    error = resultado.get("mensaje") or "No se pudo procesar la plantilla."
    await _avisar_plantilla_por_dm(message, error)
    return True
