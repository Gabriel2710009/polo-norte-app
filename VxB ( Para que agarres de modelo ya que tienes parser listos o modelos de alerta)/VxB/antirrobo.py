import logging
import math
from datetime import datetime, timedelta
from typing import Optional

import discord

import state
from config import ARMERO_ROLE_ID, LOGS_CHANNEL_ID
from database import (
    get_db_connection,
    cargar_config_antirrobo_db,
    guardar_config_antirrobo_db,
    usuario_en_whitelist_antirrobo,
)
from utils import traducir_objeto, normalizar_objeto, es_armero

logger = logging.getLogger("ArmamentBot")

_bot = None
GUILD_ID = 968286555150110790

def set_bot(bot_instance):
    global _bot
    _bot = bot_instance


# ─── CARGA / GUARDADO ─────────────────────────────────────────

def cargar_config_antirrobo():
    row = cargar_config_antirrobo_db()
    if row:
        state.ANTIRROBO_CONFIG.update({
            "activo":                          bool(row["activo"]),
            "canal_alerta_id":                 int(row["canal_alerta_id"]),
            "ventana_minutos":                 int(row["ventana_minutos"]),
            "umbral_retiros_masivos":          int(row["umbral_retiros_masivos"]),
            "umbral_desbalance_retiros":       int(row["umbral_desbalance_retiros"]),
            "umbral_desbalance_depositos_max": int(row["umbral_desbalance_depositos_max"]),
            "umbral_ratio_retiros":            int(row["umbral_ratio_retiros"]),
            "umbral_ratio_factor":             float(row["umbral_ratio_factor"]),
            "operativo_relajacion_factor":     float(row["operativo_relajacion_factor"]),
            "objetos_monitoreados":            set(row.get("objetos_monitoreados") or []),
            "updated_by":                      row.get("updated_by"),
        })


def guardar_config_antirrobo(usuario: Optional[str] = None) -> bool:
    ok = guardar_config_antirrobo_db(state.ANTIRROBO_CONFIG, usuario)
    if ok:
        state.ANTIRROBO_CONFIG["updated_by"] = usuario
    return ok


# ─── HELPERS ──────────────────────────────────────────────────

def generar_preview_antirrobo() -> str:
    estado        = "✅ ACTIVO" if state.ANTIRROBO_CONFIG["activo"] else "❌ INACTIVO"
    monitoreados  = state.ANTIRROBO_CONFIG.get("objetos_monitoreados", set()) or set()
    items_linea   = f"{len(monitoreados)} seleccionados" if monitoreados else "Todos (sin filtro)"
    return (
        f"**Estado:** {estado}\n"
        f"**Canal alerta:** `{state.ANTIRROBO_CONFIG['canal_alerta_id']}`\n"
        f"**Ventana:** {state.ANTIRROBO_CONFIG['ventana_minutos']} min\n"
        f"**Retiros masivos:** {state.ANTIRROBO_CONFIG['umbral_retiros_masivos']}\n"
        f"**Desbalance fuerte:** retiros >= {state.ANTIRROBO_CONFIG['umbral_desbalance_retiros']} "
        f"y depósitos <= {state.ANTIRROBO_CONFIG['umbral_desbalance_depositos_max']}\n"
        f"**Desbalance ratio:** retiros >= {state.ANTIRROBO_CONFIG['umbral_ratio_retiros']} "
        f"y ratio >= {state.ANTIRROBO_CONFIG['umbral_ratio_factor']}\n"
        f"**Relajación en operativo:** x{state.ANTIRROBO_CONFIG['operativo_relajacion_factor']}\n"
        f"**Items monitoreados:** {items_linea}"
    )


def obtener_items_antirrobo_disponibles() -> list:
    from config import TRADUCCIONES, CATEGORIAS
    items = set(TRADUCCIONES.keys())
    for lista in CATEGORIAS.values():
        items.update(lista)
    return sorted(items)


def buscar_items_antirrobo(termino: str, limite: int = 25) -> list:
    termino_n = normalizar_objeto(termino or "")
    todos     = obtener_items_antirrobo_disponibles()
    if not termino_n:
        return todos[:limite]
    encontrados = []
    for item in todos:
        item_n  = normalizar_objeto(item)
        nombre  = normalizar_objeto(traducir_objeto(item))
        if termino_n in item_n or termino_n in nombre:
            encontrados.append(item)
    return encontrados[:limite]


def _antirrobo_umbral(base: float, en_operativo: bool) -> float:
    if not en_operativo:
        return base
    return base * float(state.ANTIRROBO_CONFIG["operativo_relajacion_factor"])


def _antirrobo_alerta_en_cache(discord_id: str, tipo_alerta: str) -> bool:
    key      = f"{discord_id}:{tipo_alerta}"
    now      = datetime.now()
    anterior = state.ANTIRROBO_ALERT_CACHE.get(key)
    from config import ANTIRROBO_CACHE_MINUTOS
    if anterior and (now - anterior) < timedelta(minutes=ANTIRROBO_CACHE_MINUTOS):
        return True
    state.ANTIRROBO_ALERT_CACHE[key] = now
    return False


# ─── BUSCAR LOGS RECIENTES DEL USUARIO ───────────────────────

async def _buscar_logs_recientes(discord_id: str, ventana_minutos: int) -> list[str]:
    """
    Busca en LOGS_CHANNEL_ID los mensajes recientes del usuario (retiros)
    dentro de la ventana de tiempo. Devuelve lista de links con todos los encontrados.
    """
    links = []
    try:
        channel = _bot.get_channel(LOGS_CHANNEL_ID)
        if not channel:
            return links

        desde = datetime.utcnow() - timedelta(minutes=ventana_minutos)

        async for msg in channel.history(limit=300, after=desde, oldest_first=False):
            content = msg.content or ""
            # Verificar que sea un retiro del usuario
            if str(discord_id) not in content and not any(
                str(discord_id) in (e.description or "") for e in msg.embeds
            ):
                continue
            if "sacado" in content.lower() or "retiro" in content.upper():
                link = f"https://discord.com/channels/{GUILD_ID}/{LOGS_CHANNEL_ID}/{msg.id}"
                links.append(link)
    except Exception as e:
        logger.warning(f"⚠️ No se pudo buscar logs antirrobo: {e}")
    return links


# ─── ENVIAR ALERTA ANTIRROBO ──────────────────────────────────

async def enviar_alerta_antirrobo(
    datos: dict, retiros: int, depositos: int, motivos: list, en_operativo: bool
):
    try:
        channel = _bot.get_channel(int(state.ANTIRROBO_CONFIG["canal_alerta_id"]))
        if not channel:
            return

        discord_id    = str(datos.get("discord_id"))
        member_name   = datos.get("nombre") or "N/A"
        estado        = "🟡 EN OPERATIVO (sensibilidad reducida)" if en_operativo else "🔴 FUERA DE OPERATIVO"
        objeto        = datos.get("objeto")
        objeto_txt    = traducir_objeto(objeto) if objeto else "N/A"
        motivos_texto = "\n".join(f"• {m}" for m in motivos)
        usuario_mention = f"<@{discord_id}>" if discord_id else "N/A"
        hora          = datetime.now().strftime("%H:%M:%S")

        # Buscar todos los logs recientes del usuario dentro de la ventana
        logs_links = await _buscar_logs_recientes(
            discord_id,
            int(state.ANTIRROBO_CONFIG["ventana_minutos"])
        )

        embed = discord.Embed(
            title="🚨 ALERTA ANTIRROBO — ARMARIO",
            color=discord.Color.from_rgb(220, 50, 50),
            timestamp=datetime.now(),
        )
        embed.add_field(name="👤 Usuario",        value=usuario_mention,  inline=True)
        embed.add_field(name="🏷️ Nombre IC",      value=member_name,      inline=True)
        embed.add_field(name="🕐 Hora",           value=hora,             inline=True)
        embed.add_field(name="📊 Estado",         value=estado,           inline=True)
        embed.add_field(name="📦 Objeto",         value=objeto_txt,       inline=True)
        embed.add_field(name="⏱️ Ventana",        value=f"{state.ANTIRROBO_CONFIG['ventana_minutos']} min", inline=True)
        embed.add_field(name="📤 Retiros",        value=str(retiros),     inline=True)
        embed.add_field(name="📥 Depósitos",      value=str(depositos),   inline=True)
        embed.add_field(name="⚖️ Ratio",          value=f"{retiros / max(depositos, 1):.2f}x", inline=True)
        embed.add_field(name="⚠️ Motivos",        value=motivos_texto,    inline=False)

        if logs_links:
            # Armar el texto con todos los links; si supera 1024 chars, cortar con nota
            hora_actual = datetime.now().strftime("%H:%M")
            lineas_logs = [
                f"[Log {i+1} — {hora_actual}]({link})"
                for i, link in enumerate(logs_links)
            ]
            logs_txt = "\n".join(lineas_logs)
            if len(logs_txt) > 1024:
                # Truncar manteniendo los primeros que entren
                truncado = []
                largo = 0
                for linea in lineas_logs:
                    if largo + len(linea) + 1 > 990:
                        truncado.append(f"… y {len(logs_links) - len(truncado)} más.")
                        break
                    truncado.append(linea)
                    largo += len(linea) + 1
                logs_txt = "\n".join(truncado)
            embed.add_field(name=f"🔗 Logs recientes del usuario ({len(logs_links)})", value=logs_txt, inline=False)
        else:
            embed.add_field(
                name="🔗 Logs",
                value=f"[Ver canal de logs](https://discord.com/channels/{GUILD_ID}/{LOGS_CHANNEL_ID})",
                inline=False,
            )

        embed.set_footer(text=f"ID: {discord_id}")

        await channel.send(content=f"<@&{ARMERO_ROLE_ID}>", embed=embed)
        logger.info(
            f"🚨 ANTIRROBO | Usuario={datos.get('nombre', 'N/A')} ({discord_id}) | "
            f"Retiros: {retiros} | Depósitos: {depositos} | "
            f"Motivos: {len(motivos)} | Operativo: {en_operativo}"
        )
    except Exception as e:
        logger.error(f"❌ Error enviando alerta antirrobo: {e}", exc_info=True)


# ─── EVALUAR ANTIRROBO ────────────────────────────────────────

async def evaluar_antirrobo(datos: dict):
    try:
        if datos.get("tipo") != "RETIRO":
            return
        if not state.ANTIRROBO_CONFIG["activo"]:
            return

        discord_id   = str(datos.get("discord_id") or "")
        if not discord_id:
            return

        objeto        = str(datos.get("objeto") or "").strip()
        monitoreados  = state.ANTIRROBO_CONFIG.get("objetos_monitoreados", set()) or set()
        if monitoreados and objeto not in monitoreados:
            return

        en_operativo = bool(state.operativo_activo["activo"])
        if usuario_en_whitelist_antirrobo(discord_id):
            return

        conn   = get_db_connection()
        cursor = conn.cursor()
        if monitoreados:
            cursor.execute("""
                SELECT
                    COALESCE(SUM(CASE WHEN tipo='RETIRO'   THEN cantidad ELSE 0 END), 0) AS retiros,
                    COALESCE(SUM(CASE WHEN tipo='DEPOSITO' THEN cantidad ELSE 0 END), 0) AS depositos
                FROM registros_armas
                WHERE discord_id = %s
                  AND objeto     = ANY(%s)
                  AND timestamp  >= (NOW() - (%s || ' minutes')::interval)
            """, (discord_id, list(monitoreados), int(state.ANTIRROBO_CONFIG["ventana_minutos"])))
        else:
            cursor.execute("""
                SELECT
                    COALESCE(SUM(CASE WHEN tipo='RETIRO'   THEN cantidad ELSE 0 END), 0) AS retiros,
                    COALESCE(SUM(CASE WHEN tipo='DEPOSITO' THEN cantidad ELSE 0 END), 0) AS depositos
                FROM registros_armas
                WHERE discord_id = %s
                  AND timestamp  >= (NOW() - (%s || ' minutes')::interval)
            """, (discord_id, int(state.ANTIRROBO_CONFIG["ventana_minutos"])))

        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return

        retiros   = int(row["retiros"]   or 0)
        depositos = int(row["depositos"] or 0)

        motivos = []

        if retiros >= math.ceil(
            _antirrobo_umbral(state.ANTIRROBO_CONFIG["umbral_retiros_masivos"], en_operativo)
        ):
            motivos.append(f"Retiros masivos: {retiros} en ventana corta.")

        umbral_ret_desb = math.ceil(
            _antirrobo_umbral(state.ANTIRROBO_CONFIG["umbral_desbalance_retiros"], en_operativo)
        )
        umbral_dep_max = math.ceil(
            _antirrobo_umbral(state.ANTIRROBO_CONFIG["umbral_desbalance_depositos_max"], en_operativo)
        )
        if retiros >= umbral_ret_desb and depositos <= umbral_dep_max:
            motivos.append(f"Desbalance fuerte: retira {retiros} y devuelve {depositos}.")

        umbral_ratio_ret = math.ceil(
            _antirrobo_umbral(state.ANTIRROBO_CONFIG["umbral_ratio_retiros"], en_operativo)
        )
        ratio = retiros / max(depositos, 1)
        if retiros >= umbral_ratio_ret and ratio >= float(state.ANTIRROBO_CONFIG["umbral_ratio_factor"]):
            motivos.append(f"Desbalance por ratio alto: {ratio:.2f}x más retiros que depósitos.")

        if not motivos:
            logger.debug(
                f"🛡️ Antirrobo OK | {discord_id} | retiros={retiros} depósitos={depositos}"
            )
            return

        alerta_tipo = "operativo" if en_operativo else "fuera"
        if _antirrobo_alerta_en_cache(discord_id, alerta_tipo):
            return

        await enviar_alerta_antirrobo(datos, retiros, depositos, motivos, en_operativo)

    except Exception as e:
        logger.error(f"❌ Error evaluando antirrobo: {e}", exc_info=True)