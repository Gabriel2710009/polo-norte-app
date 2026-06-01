import logging
import re
from datetime import datetime
from typing import Optional

import discord

logger = logging.getLogger("ArmamentBot")


def _parsear_cantidad(texto: str) -> int:
    """
    Convierte strings de cantidad que pueden tener separadores de miles.
    Ej: '50.891' → 50891, '50,891' → 50891, '50' → 50
    """
    try:
        # Eliminar puntos y comas que sean separadores de miles
        # Detectar si el punto/coma es separador de miles o decimal
        # En formato español: 50.891 = cincuenta mil ochocientos noventa y uno
        limpio = texto.strip().replace(".", "").replace(",", "")
        return int(limpio)
    except (ValueError, AttributeError):
        return 1


def parsear_mensaje_texto_libre(content: str) -> Optional[dict]:
    """
    Parsea mensajes de texto libre con el formato:
    **Nombre IC** (<@discord_id>) ha sacado/metido x(cantidad) Nombre (`objeto`) del/al almacén 'Almacén'
    La cantidad puede tener separadores de miles: x50.891
    """
    try:
        if re.search(r"\bha\s+sacado\b", content, re.IGNORECASE):
            tipo = "RETIRO"
        elif re.search(r"\bha\s+metido\b", content, re.IGNORECASE):
            tipo = "DEPOSITO"
        else:
            return None

        match_nombre  = re.match(r"^\*{0,2}(.+?)\*{0,2}\s*\(", content.strip())
        nombre        = match_nombre.group(1).strip().strip("*") if match_nombre else None

        match_discord = re.search(r"<@!?(\d{17,20})>", content)
        discord_id    = match_discord.group(1) if match_discord else None

        if not discord_id:
            return None

        # Cantidad: acepta dígitos con puntos/comas como separadores de miles
        match_cantidad = re.search(r"\bx([\d.,]+)\b", content, re.IGNORECASE)
        cantidad       = _parsear_cantidad(match_cantidad.group(1)) if match_cantidad else 1

        match_objeto = re.search(r"`([^`]+)`", content)
        objeto       = match_objeto.group(1).strip() if match_objeto else None

        if not objeto:
            return None

        match_almacen = re.search(
            r"['\u2018\u2019]([^''\u2018\u2019]+)['\u2018\u2019]", content
        )
        almacen = match_almacen.group(1).strip() if match_almacen else None

        logger.info(
            f"📝 [TEXTO LIBRE] Tipo: {tipo} | Objeto: {objeto} x{cantidad} | "
            f"Usuario: {nombre} ({discord_id}) | Almacén: {almacen}"
        )
        return {
            "tipo":       tipo,
            "nombre":     nombre,
            "discord_id": discord_id,
            "objeto":     objeto,
            "cantidad":   cantidad,
            "almacen":    almacen,
        }

    except Exception as e:
        logger.error(f"❌ [TEXTO LIBRE] Error parseando: {e}", exc_info=True)
        return None


def parsear_embed_arma(embed: discord.Embed) -> Optional[dict]:
    """
    Parsea un embed de retiro/depósito de armas.
    Soporta múltiples formatos incluyendo backticks con emojis.
    La cantidad puede tener separadores de miles.
    """
    try:
        datos = {"timestamp": datetime.now().isoformat()}
        parseo_formato_nuevo = False

        descripcion = embed.description or ""
        if not descripcion:
            return None

        descripcion_lineal = re.sub(r"\n+", " ", descripcion).strip()

        # ── TIPO ──────────────────────────────────────────────
        if "RETIRO" in descripcion.upper() or re.search(r"\bha\s+sacado\b", descripcion, re.IGNORECASE):
            datos["tipo"] = "RETIRO"
        elif (
            "DEPOSITO" in descripcion.upper()
            or "DEPÓSITO" in descripcion.upper()
            or re.search(r"\bha\s+metido\b", descripcion, re.IGNORECASE)
        ):
            datos["tipo"] = "DEPOSITO"
        else:
            return None

        # ── FORMATO NARRATIVO OX_INVENTORY ────────────────────
        match_mov = re.search(
            r"^(?P<nombre>.+?)\s+\((?:<@!?(?P<discord_id>\d{17,20})>|@\*{0,2}(?P<discord_user>[^)]+?)\*{0,2})\)\s+ha\s+"
            r"(?:metido|sacado)\s+x(?P<cantidad>[\d.,]+)\s+"
            r"(?P<objeto_nombre>.+?)\s+\((?:`)?(?P<objeto_codigo>[^)`]+)(?:`)?\)\s+"
            r"(?:al|del)\s+almac[^\s']*\s+'(?P<almacen>[^']+)'",
            descripcion_lineal,
            re.IGNORECASE,
        )
        if match_mov:
            parseo_formato_nuevo = True
            datos["nombre"]    = re.sub(r"^\*+|\*+$", "", match_mov.group("nombre").strip())
            if match_mov.group("discord_id"):
                datos["discord_id"] = match_mov.group("discord_id").strip()
            if match_mov.group("discord_user"):
                datos["discord"] = f"@{match_mov.group('discord_user').strip().lstrip('@')}"
            datos["cantidad"] = _parsear_cantidad(match_mov.group("cantidad"))
            datos["objeto"]   = match_mov.group("objeto_codigo").strip().strip("`")
            datos["almacen"]  = match_mov.group("almacen").strip()

        # ── CANTIDAD ──────────────────────────────────────────
        if "cantidad" not in datos:
            patrones_cantidad = [
                r"`[^`]*`\s*\*\*CANTIDAD\*\*\s*:\s*([\d.,]+)",
                r"`[^`]*`\s*CANTIDAD\s*:\s*([\d.,]+)",
                r"\*\*CANTIDAD\*\*\s*:?\s*([\d.,]+)",
                r"CANTIDAD\s*:\s*([\d.,]+)",
                r"CANTIDAD[^\d]*([\d.,]+)",
            ]
            for patron in patrones_cantidad:
                m = re.search(patron, descripcion, re.IGNORECASE)
                if m:
                    datos["cantidad"] = _parsear_cantidad(m.group(1))
                    break

        # ── ID PERSONAJE ──────────────────────────────────────
        if "id_personaje" not in datos:
            patrones_id = [
                r"`[^`]*`\s*\*\*ID\*\*\s*:\s*(\d+)",
                r"`[^`]*`\s*ID\s*:\s*(\d+)",
                r"\*\*ID\*\*\s*:\s*(\d+)(?!\w)",
                r"(?<![A-Z])ID\s*:\s*(\d+)",
            ]
            for patron in patrones_id:
                m = re.search(patron, descripcion, re.IGNORECASE)
                if m:
                    datos["id_personaje"] = m.group(1).strip()
                    break

        # ── NOMBRE IC ─────────────────────────────────────────
        if "nombre" not in datos:
            patrones_nombre = [
                r"`[^`]*`\s*\*\*NOMBRE IC\*\*\s*:\s*(.+?)(?:\s+`|$)",
                r"`[^`]*`\s*NOMBRE IC\s*:\s*(.+?)(?:\s+`|$)",
                r"\*\*NOMBRE IC\*\*:\s*(.+?)(?:\n|$)",
                r"NOMBRE IC[:\s]+(.+?)(?:\n|$)",
            ]
            for patron in patrones_nombre:
                m = re.search(patron, descripcion, re.IGNORECASE)
                if m:
                    datos["nombre"] = m.group(1).strip()
                    break

        # ── STEAMID ───────────────────────────────────────────
        if "steamid" not in datos:
            patrones_steam = [
                r"`[^`]*`\s*\*\*STEAMID\*\*\s+(.+?)(?:\s+`|$)",
                r"`[^`]*`\s*STEAMID\s+(.+?)(?:\s+`|$)",
                r"\*\*STEAMID\*\*\s+(.+?)(?:\n|$)",
                r"STEAMID[:\s]+(.+?)(?:\n|$)",
            ]
            for patron in patrones_steam:
                m = re.search(patron, descripcion, re.IGNORECASE)
                if m:
                    datos["steamid"] = m.group(1).strip()
                    break

        # ── DISCORD ID ────────────────────────────────────────
        if "discord_id" not in datos:
            m = re.search(r"\((\d{17,20})\)", descripcion)
            if m:
                datos["discord_id"] = m.group(1)
            else:
                m = re.search(r"<@(\d{17,20})>", descripcion)
                if m:
                    datos["discord_id"] = m.group(1)

        # ── DISCORD texto ─────────────────────────────────────
        if "discord" not in datos:
            patrones_discord = [
                r"`[^`]*`\s*\*\*DISCORD\*\*\s+(.+?)(?:\s+`|$)",
                r"`[^`]*`\s*DISCORD\s+(.+?)(?:\s+`|$)",
                r"\*\*DISCORD\*\*\s+(.+?)(?:\n|$)",
            ]
            for patron in patrones_discord:
                m = re.search(patron, descripcion, re.IGNORECASE)
                if m:
                    datos["discord"] = m.group(1).strip()
                    break

        # ── OBJETO ────────────────────────────────────────────
        if "objeto" not in datos:
            patrones_objeto = [
                r"`[^`]*`\s*\*\*OBJETO\*\*\s*:\s*(.+?)(?:\s+`|$)",
                r"`[^`]*`\s*OBJETO\s*:\s*(.+?)(?:\s+`|$)",
                r"\*\*OBJETO\*\*:\s*(.+?)(?:\n|$)",
                r"OBJETO[:\s]+(.+?)(?:\n|$)",
            ]
            for patron in patrones_objeto:
                m = re.search(patron, descripcion, re.IGNORECASE)
                if m:
                    datos["objeto"] = m.group(1).strip()
                    break

        # ── ALMACÉN ───────────────────────────────────────────
        if "almacen" not in datos:
            patrones_almacen = [
                r"`[^`]*`\s*\*\*ALMAC[EÉ]N\*\*\s*:\s*(.+?)(?:\n|$)",
                r"`[^`]*`\s*ALMAC[EÉ]N\s*:\s*(.+?)(?:\n|$)",
                r"\*\*ALMAC[EÉ]N\*\*:\s*(.+?)(?:\n|$)",
                r"ALMAC[EÉ]N[:\s]+(.+?)(?:\n|$)",
            ]
            for patron in patrones_almacen:
                m = re.search(patron, descripcion, re.IGNORECASE)
                if m:
                    datos["almacen"] = m.group(1).strip()
                    break

        # ── FALLBACK: FIELDS ──────────────────────────────────
        if embed.fields:
            for field in embed.fields:
                fname = field.name.upper()
                fval  = field.value
                if "CANTIDAD" in fname and "cantidad" not in datos:
                    m = re.search(r"([\d.,]+)", fval)
                    if m:
                        datos["cantidad"] = _parsear_cantidad(m.group(1))
                elif "OBJETO" in fname and "objeto" not in datos:
                    datos["objeto"] = fval.strip()
                elif "NOMBRE" in fname and "nombre" not in datos:
                    datos["nombre"] = fval.strip()
                elif fname == "ID" and "id_personaje" not in datos:
                    m = re.search(r"(\d+)", fval)
                    if m:
                        datos["id_personaje"] = m.group(1)
                elif "ALMAC" in fname and "almacen" not in datos:
                    datos["almacen"] = fval.strip()

        # ── VALIDACIÓN FINAL ──────────────────────────────────
        if "tipo" in datos and "objeto" in datos and "discord_id" in datos:
            if "cantidad" not in datos:
                datos["cantidad"] = 1
            logger.info(
                f"{'📋 LOG nuevo' if parseo_formato_nuevo else '📋 Embed'} parseado | "
                f"Tipo: {datos['tipo']} | Objeto: {datos['objeto']} | "
                f"Cantidad: {datos['cantidad']} | Usuario: {datos.get('nombre', 'N/A')}"
            )
            return datos

        logger.warning(
            f"⚠️ Embed incompleto | tipo: {'tipo' in datos} | "
            f"objeto: {'objeto' in datos} | discord_id: {'discord_id' in datos}"
        )
        return None

    except Exception as e:
        logger.error(f"❌ Error parseando embed: {e}", exc_info=True)
        return None


# ─── RECUPERACIÓN DE HISTORIAL AL ARRANQUE ────────────────────

def procesar_historial_mensajes(mensajes: list, operativo_activo: dict) -> int:
    """
    Corre en thread (asyncio.to_thread).
    Parsea los últimos mensajes del canal (embeds + texto libre) y guarda
    en BD los que todavía no existen, evitando duplicados por timestamp exacto.
    """
    from database import guardar_registro, get_db_connection

    contador = 0
    for message in mensajes:
        datos = None

        # 1. Intentar embed
        if message.embeds:
            for embed in message.embeds:
                datos = parsear_embed_arma(embed)
                if datos:
                    break

        # 2. Fallback texto libre (formato híbrido)
        if not datos and message.content:
            datos = parsear_mensaje_texto_libre(message.content)

        if not datos:
            continue

        datos["timestamp"] = message.created_at.replace(tzinfo=None).isoformat()

        try:
            conn   = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) AS count
                FROM registros_armas
                WHERE discord_id = %s
                  AND objeto     = %s
                  AND tipo       = %s
                  AND ABS(EXTRACT(EPOCH FROM (timestamp - %s::timestamp))) < 2
            """, (
                datos.get("discord_id"),
                datos.get("objeto"),
                datos.get("tipo"),
                datos.get("timestamp"),
            ))
            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if result and result["count"] == 0:
                rid = guardar_registro(datos, operativo_activo)
                if rid:
                    contador += 1
        except Exception as e:
            logger.error(f"❌ Error verificando registro histórico: {e}", exc_info=True)

    logger.info(f"📥 Historial procesado | Registros nuevos: {contador}")
    return contador


async def cargar_historial_canal(bot, logs_channel_id: int, operativo_activo: dict):
    """Lee los últimos 100 mensajes del canal de logs y recupera los no guardados."""
    import asyncio
    try:
        channel = bot.get_channel(logs_channel_id)
        if not channel:
            logger.warning(f"⚠️ Canal de historial no encontrado: {logs_channel_id}")
            return
        logger.info(f"📖 Cargando historial del canal de logs...")
        mensajes = [msg async for msg in channel.history(limit=100)]
        logger.info(f"📖 Historial: {len(mensajes)} mensajes encontrados, procesando...")
        await asyncio.to_thread(procesar_historial_mensajes, mensajes, operativo_activo)
    except Exception as e:
        logger.error(f"❌ Error cargando historial del canal: {e}", exc_info=True)