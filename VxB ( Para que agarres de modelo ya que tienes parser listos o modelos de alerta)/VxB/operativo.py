import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

import discord

import state
from config import ALERTAS_CHANNEL_ID, ONLY_ARMEROS_CHANNEL_ID
from database import guardar_estado_operativo_db
from utils import traducir_objeto, es_armero_o_alto_cargo

logger = logging.getLogger("ArmamentBot")

_bot = None
_control_update_pending: bool = False
_verificacion_task: Optional[asyncio.Task] = None
_guardado_task: Optional[asyncio.Task] = None

# Constantes locales para evitar import circular con cmd_operativo
_CANAL_BALAS    = 1015394887581061120
_BALLAS_ROLE_ID = 1212120053936427049

def set_bot(bot_instance):
    global _bot
    _bot = bot_instance


def _cancelar_tareas_operativo(*, preservar_tarea_actual: bool = False):
    global _verificacion_task, _guardado_task
    tarea_actual = asyncio.current_task()

    if _verificacion_task and not _verificacion_task.done():
        if not preservar_tarea_actual or _verificacion_task is not tarea_actual:
            _verificacion_task.cancel()
    _verificacion_task = None

    if _guardado_task and not _guardado_task.done():
        _guardado_task.cancel()
    _guardado_task = None


async def _obtener_canal_seguro(canal_id: int):
    channel = _bot.get_channel(canal_id)
    if channel is not None:
        return channel
    try:
        return await _bot.fetch_channel(canal_id)
    except Exception:
        return None


async def _enviar_embed_a_canal(canal_id: int, *, embed: discord.Embed, content: str | None = None, contexto: str = "") -> bool:
    canal = await _obtener_canal_seguro(canal_id)
    if canal is None:
        logger.warning(f"⚠️ [Operativo] {contexto}: no se pudo resolver el canal {canal_id}")
        return False
    try:
        await canal.send(content=content, embed=embed)
        logger.info(
            f"✅ [Operativo] {contexto}: embed enviado | canal_id={canal_id} | "
            f"canal={getattr(canal, 'name', 'N/A')}"
        )
        return True
    except Exception as e:
        logger.error(f"❌ [Operativo] {contexto}: error enviando embed al canal {canal_id}: {e}", exc_info=True)
        return False


# ─── CONTROL DEL MENSAJE DE OPERATIVO ────────────────────────

def _build_control_embed(retiros: dict, depositos: dict) -> discord.Embed:
    from config import CATEGORIAS
    PISTOLAS = set(CATEGORIAS["pistolas"])

    def _filtrar(dic):
        return {k: v for k, v in dic.items() if k in PISTOLAS}

    def _resumen(dic):
        if not dic:
            return "*Sin movimientos*"
        return "\n".join(f"• {traducir_objeto(obj)}: {cant}" for obj, cant in sorted(dic.items(), key=lambda x: x[1], reverse=True))[:1024]

    ret_pistolas = _filtrar(retiros)
    dep_pistolas = _filtrar(depositos)
    total_ret    = sum(ret_pistolas.values())
    total_dep    = sum(dep_pistolas.values())
    bal_total    = total_dep - total_ret
    b_emoji      = "📈" if bal_total > 0 else ("📉" if bal_total < 0 else "📊")

    balance_por_arma = {obj: dep_pistolas.get(obj, 0) - ret_pistolas.get(obj, 0) for obj in set(ret_pistolas) | set(dep_pistolas)}
    balance_texto    = "\n".join(f"• {traducir_objeto(obj)}: {bal:+d}" for obj, bal in sorted(balance_por_arma.items()))[:1024] or "*Sin movimientos*"

    embed = discord.Embed(
        title="🧩 CONTROL DE ARMARIO — OPERATIVO",
        description=f"{b_emoji} Balance total pistolas: **{bal_total:+d}**",
        color=discord.Color.orange(),
        timestamp=datetime.now(),
    )
    embed.add_field(name=f"📤 Retiros ({total_ret})",     value=_resumen(ret_pistolas), inline=True)
    embed.add_field(name=f"📥 Depósitos ({total_dep})",   value=_resumen(dep_pistolas), inline=True)
    embed.add_field(name="⚖️ Balance por arma",            value=balance_texto,          inline=False)
    embed.set_footer(text=f"Actualizado: {datetime.now().strftime('%H:%M:%S')}")
    return embed


async def _enviar_nuevo_control(channel: discord.TextChannel, embed: discord.Embed):
    msg_id = state.operativo_activo.get("control_msg_id")
    if msg_id:
        try:
            old = await channel.fetch_message(int(msg_id))
            await old.delete()
        except (discord.NotFound, discord.HTTPException):
            pass
    state.operativo_activo["control_msg_id"]     = None
    state.operativo_activo["control_channel_id"] = None
    try:
        msg = await channel.send(embed=embed)
        state.operativo_activo["control_msg_id"]     = msg.id
        state.operativo_activo["control_channel_id"] = channel.id
        logger.info(f"✅ Nuevo mensaje de control enviado | ID: {msg.id}")
    except (discord.Forbidden, discord.HTTPException) as e:
        logger.error(f"❌ Error enviando control de armario: {e}", exc_info=True)


async def actualizar_control_operativo():
    global _control_update_pending
    if not state.operativo_activo["activo"]:
        return
    if _control_update_pending:
        return
    _control_update_pending = True
    await asyncio.sleep(3)
    _control_update_pending = False
    if not state.operativo_activo["activo"]:
        return
    channel = _bot.get_channel(ALERTAS_CHANNEL_ID)
    if not channel:
        return
    retiros   = dict(state.operativo_activo.get("pistolas_retiros")   or {})
    depositos = dict(state.operativo_activo.get("pistolas_depositos")  or {})
    embed     = _build_control_embed(retiros, depositos)
    msg_id     = state.operativo_activo.get("control_msg_id")
    channel_id = state.operativo_activo.get("control_channel_id")
    if msg_id and channel_id == channel.id:
        try:
            msg = await channel.fetch_message(int(msg_id))
            await msg.edit(embed=embed)
            return
        except discord.NotFound:
            state.operativo_activo["control_msg_id"]     = None
            state.operativo_activo["control_channel_id"] = None
        except discord.HTTPException as e:
            if getattr(e, "code", 0) == 30046:
                await _enviar_nuevo_control(channel, embed)
                return
            state.operativo_activo["control_msg_id"]     = None
            state.operativo_activo["control_channel_id"] = None
        except discord.Forbidden:
            state.operativo_activo["control_msg_id"]     = None
            state.operativo_activo["control_channel_id"] = None
    await _enviar_nuevo_control(channel, embed)


# ─── VIEW VERIFICACIÓN (aparece en ALERTAS_CHANNEL_ID) ───────

class VerificacionOperativoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ El operativo sigue activo", style=discord.ButtonStyle.success, custom_id="verificar_operativo_activo")
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        if not state.operativo_activo["activo"]:
            await interaction.response.defer()
            try:
                await interaction.message.delete()
            except Exception:
                pass
            await interaction.followup.send("ℹ️ El operativo ya no está activo.", ephemeral=True)
            return

        # Limpiar verify_msg_id → señal de verificación exitosa
        state.VERIFICACION_OPERATIVO_CONFIG["verify_msg_id"]     = None
        state.VERIFICACION_OPERATIVO_CONFIG["verify_channel_id"] = None
        state.operativo_activo["verify_msg_id"] = None
        state.operativo_activo["verify_channel_id"] = None
        state.operativo_activo["verify_sent_at"] = None
        await asyncio.to_thread(
            guardar_estado_operativo_db,
            True,
            state.operativo_activo.get("inicio"),
            state.operativo_activo.get("iniciado_por"),
            state.operativo_activo,
        )

        # Borrar el mensaje de verificación
        await interaction.response.defer()
        try:
            await interaction.message.delete()
        except Exception:
            pass

        from log_actions import log_accion
        await log_accion(interaction.user, "Verificó operativo activo", "Verificación respondida a tiempo.", discord.Color.green(), "✅")
        await interaction.followup.send("✅ Operativo marcado como activo.", ephemeral=True)


# ─── TAREA VERIFICACIÓN PERIÓDICA ────────────────────────────

async def _tarea_verificacion_operativo():
    """
    Cada VERIFICACION_OPERATIVO_CONFIG['intervalo_minutos'] minutos manda
    un mensaje en ALERTAS_CHANNEL_ID pidiendo confirmación.
    Si nadie responde en timeout_minutos, termina el operativo.
    """
    global _verificacion_task

    while state.operativo_activo["activo"]:
        cfg = state.VERIFICACION_OPERATIVO_CONFIG
        if not cfg.get("activo", True):
            await asyncio.sleep(60)
            continue

        timeout_min = int(cfg.get("timeout_minutos", 10))
        intervalo_min = int(cfg.get("intervalo_minutos", 60))
        if cfg.get("verify_msg_id") is not None:
            sent_at = state.operativo_activo.get("verify_sent_at") or datetime.now()
            state.operativo_activo["verify_sent_at"] = sent_at
            restante = max(0.0, timeout_min * 60 - (datetime.now() - sent_at).total_seconds())
            await asyncio.sleep(restante)

            if not state.operativo_activo["activo"]:
                break

            if cfg.get("verify_msg_id") is None:
                logger.info("✅ Operativo verificado por armero, continúa")
                continue

            if _hubo_movimiento_operativo_desde(sent_at):
                logger.info("✅ Operativo auto-verificado por movimiento reciente en armario")
                try:
                    ch_v = _bot.get_channel(int(cfg["verify_channel_id"]))
                    if ch_v:
                        msg_v = await ch_v.fetch_message(int(cfg["verify_msg_id"]))
                        await msg_v.delete()
                except Exception as e:
                    logger.warning(f"⚠️ No se pudo borrar mensaje de verificación auto-confirmado: {e}")

                cfg["verify_msg_id"]     = None
                cfg["verify_channel_id"] = None
                state.VERIFICACION_OPERATIVO_CONFIG["verify_msg_id"]     = None
                state.VERIFICACION_OPERATIVO_CONFIG["verify_channel_id"] = None
                state.operativo_activo["verify_msg_id"] = None
                state.operativo_activo["verify_channel_id"] = None
                state.operativo_activo["verify_sent_at"] = None
                await asyncio.to_thread(
                    guardar_estado_operativo_db,
                    True,
                    state.operativo_activo.get("inicio"),
                    state.operativo_activo.get("iniciado_por"),
                    state.operativo_activo,
                )
                continue

            logger.info("⏰ Timeout de verificación expirado — terminando operativo automáticamente")
            try:
                ch_v = _bot.get_channel(int(cfg["verify_channel_id"]))
                if ch_v:
                    msg_v = await ch_v.fetch_message(int(cfg["verify_msg_id"]))
                    await msg_v.delete()
            except Exception as e:
                logger.warning(f"⚠️ No se pudo borrar mensaje de verificación expirado: {e}")

            cfg["verify_msg_id"]     = None
            cfg["verify_channel_id"] = None
            state.VERIFICACION_OPERATIVO_CONFIG["verify_msg_id"]     = None
            state.VERIFICACION_OPERATIVO_CONFIG["verify_channel_id"] = None
            state.operativo_activo["verify_msg_id"] = None
            state.operativo_activo["verify_channel_id"] = None
            state.operativo_activo["verify_sent_at"] = None
            await asyncio.to_thread(
                guardar_estado_operativo_db,
                True,
                state.operativo_activo.get("inicio"),
                state.operativo_activo.get("iniciado_por"),
                state.operativo_activo,
            )
            await _terminar_operativo_automatico()
            break

        await asyncio.sleep(intervalo_min * 60)

        if not state.operativo_activo["activo"]:
            break

        if cfg.get("verify_msg_id") is not None:
            continue

        canal = _bot.get_channel(ALERTAS_CHANNEL_ID)
        if not canal:
            logger.warning(f"⚠️ ALERTAS_CHANNEL_ID no encontrado: {ALERTAS_CHANNEL_ID}")
            continue

        from config import ARMERO_ROLE_ID
        inicio_str  = ""
        if state.operativo_activo.get("inicio"):
            delta      = datetime.now() - state.operativo_activo["inicio"]
            minutos    = int(delta.total_seconds() // 60)
            inicio_str = f"{minutos // 60}h {minutos % 60}m"

        embed = discord.Embed(
            title="⏰ Verificación de operativo",
            description=(
                f"El operativo lleva activo **{inicio_str}**.\n\n"
                f"Si el operativo **sigue activo**, presioná el botón "
                f"en los próximos **{timeout_min} minutos**.\n\n"
                f"⚠️ Si nadie confirma, el bot **terminará el operativo automáticamente** y mandará el resumen."
            ),
            color=discord.Color.yellow(),
            timestamp=datetime.now(),
        )
        embed.set_footer(text=f"Timeout: {timeout_min} min | Intervalo configurado: {cfg.get('intervalo_minutos', 60)} min")

        view = VerificacionOperativoView()
        try:
            msg = await canal.send(content=f"<@&{ARMERO_ROLE_ID}>", embed=embed, view=view)
            cfg["verify_msg_id"]     = msg.id
            cfg["verify_channel_id"] = canal.id
            state.VERIFICACION_OPERATIVO_CONFIG["verify_msg_id"]     = msg.id
            state.VERIFICACION_OPERATIVO_CONFIG["verify_channel_id"] = canal.id
            state.operativo_activo["verify_msg_id"] = msg.id
            state.operativo_activo["verify_channel_id"] = canal.id
            state.operativo_activo["verify_sent_at"] = datetime.now()
            await asyncio.to_thread(
                guardar_estado_operativo_db,
                True,
                state.operativo_activo.get("inicio"),
                state.operativo_activo.get("iniciado_por"),
                state.operativo_activo,
            )
        except Exception as e:
            logger.error(f"❌ Error mandando verificación operativo: {e}", exc_info=True)
            continue

    _verificacion_task = None


async def _terminar_operativo_automatico():
    if not state.operativo_activo["activo"]:
        logger.info("ℹ️ [Operativo auto] Se pidió cierre automático pero ya no hay operativo activo")
        return

    logger.info("🚀 [Operativo auto] Inicio de cierre automático")
    inicio_op = state.operativo_activo.get("inicio")
    # No cancelar la tarea actual acá: esta función corre dentro de la
    # tarea de verificación y necesitamos que siga viva hasta terminar
    # de mandar los embeds de cierre.
    logger.info(
        "🧩 [Operativo auto] Llamando a finalizar_operativo() | "
        f"inicio_op={inicio_op} | activo={state.operativo_activo['activo']}"
    )
    snapshot  = finalizar_operativo(preservar_tarea_actual=True)
    logger.info(
        "✅ [Operativo auto] finalizar_operativo() completado | "
        f"retiros={sum(snapshot['retiros'].values())} | "
        f"depositos={sum(snapshot['depositos'].values())} | "
        f"inicio={snapshot['inicio']} | iniciado_por={snapshot['iniciado_por']}"
    )
    retiros   = snapshot["retiros"]
    depositos = snapshot["depositos"]
    inicio    = snapshot["inicio"]

    duracion = ""
    if inicio:
        delta   = datetime.now() - inicio
        minutos = int(delta.total_seconds() // 60)
        duracion = f"{minutos // 60}h {minutos % 60}m"

    # ── Embed "OPERATIVO TERMINADO" → canal donde se inició (ALERTAS_CHANNEL_ID) ──
    def _resumen(dic):
        if not dic:
            return "*Sin movimientos*"
        return "\n".join(
            f"• {traducir_objeto(o)}: {c}"
            for o, c in sorted(dic.items(), key=lambda x: x[1], reverse=True)
        )[:1024]

    balance_por_arma = {
        obj: depositos.get(obj, 0) - retiros.get(obj, 0)
        for obj in set(retiros) | set(depositos)
    }
    balance_texto = (
        "\n".join(
            f"• {traducir_objeto(obj)}: {bal:+d}"
            for obj, bal in sorted(balance_por_arma.items())
        )[:1024]
        or "*Sin movimientos*"
    )
    total_ret = sum(retiros.values())
    total_dep = sum(depositos.values())
    bal_total = total_dep - total_ret
    b_emoji   = "📈" if bal_total > 0 else ("📉" if bal_total < 0 else "📊")

    embed_aviso = discord.Embed(
        title="🏁 OPERATIVO TERMINADO",
        description=(
            f"Terminado automáticamente por inactividad\n"
            f"⏱️ Duración: **{duracion}**\n"
            f"📉 Balance total: **{bal_total:+d}**"
        ) if duracion else "Terminado automáticamente por inactividad",
        color=discord.Color.red(),
        timestamp=datetime.now(),
    )
    logger.info("📝 [Operativo auto] Embed principal armado")
    embed_aviso.add_field(name=f"📤 Retiros ({total_ret})", value=_resumen(retiros), inline=False)
    embed_aviso.add_field(name=f"📥 Depósitos ({total_dep})", value=_resumen(depositos), inline=False)
    embed_aviso.add_field(name="⚖️ Balance por arma", value=balance_texto, inline=False)
    embed_aviso.set_footer(text=f"Terminado a las {datetime.now().strftime('%H:%M:%S')}")
    logger.info(
        "📤 [Operativo auto] Enviando embed principal a ALERTAS | "
        f"canal={ALERTAS_CHANNEL_ID} | ret={total_ret} | dep={total_dep} | balance={bal_total:+d}"
    )
    await _enviar_embed_a_canal(
        ALERTAS_CHANNEL_ID,
        embed=embed_aviso,
        contexto="cierre automático / aviso principal",
    )
    logger.info("✅ [Operativo auto] Embed principal enviado")

    if not inicio_op:
        logger.warning("⚠️ [Operativo auto] inicio_op es None, no se pueden generar resúmenes BD")
        logger.info("➡️ [Operativo auto] Saltando resumen general y de pistolas por falta de inicio_op")
        logger.info("➡️ [Operativo auto] Pasando al resumen de asistencia")
        await _enviar_resumen_asistencia_cierre()
        logger.info("🏁 [Operativo auto] Cierre automático finalizado")
        return

    # ── Resumen General → canal balas ──────────────────────────────
    try:
        from database import get_db_connection

        logger.info("🔎 [Operativo auto] Consultando BD para resumen general")
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT objeto,
                   SUM(CASE WHEN tipo='RETIRO'   THEN cantidad ELSE 0 END) AS retiros,
                   SUM(CASE WHEN tipo='DEPOSITO' THEN cantidad ELSE 0 END) AS depositos
            FROM registros_armas
            WHERE en_operativo = TRUE AND timestamp >= %s
            GROUP BY objeto ORDER BY retiros DESC
        """, (inicio_op,))
        rows_general = cursor.fetchall()
        cursor.close()
        conn.close()
        logger.info(f"✅ [Operativo auto] Consulta resumen general OK | filas={len(rows_general)}")

        if rows_general:
            # Calcular totales para el embed
            total_ret_g   = sum(int(r["retiros"]   or 0) for r in rows_general)
            total_dep_g   = sum(int(r["depositos"] or 0) for r in rows_general)
            bal_total_g   = total_dep_g - total_ret_g
            b_emoji_g     = "📈" if bal_total_g > 0 else ("📉" if bal_total_g < 0 else "📊")

            lineas = [
                f"• {traducir_objeto(r['objeto'])}: "
                f"Ret: {r['retiros']} | Dep: {r['depositos']} | Bal: {r['depositos'] - r['retiros']:+d}"
                for r in rows_general
            ]

            embed_general = discord.Embed(
                title="🏁 OPERATIVO TERMINADO",
                description=(
                    "Terminado automáticamente por inactividad\n"
                    f"⏱️ Duración: {duracion}\n"
                    f"📉 Balance total: {bal_total_g:+d}"
                ),
                color=discord.Color.blurple(),
                timestamp=datetime.now(),
            )
            logger.info("📝 [Operativo auto] Embed de resumen general armado")

            CHUNK = 1024
            partes, actual, largo_actual = [], [], 0
            for linea in lineas:
                if largo_actual + len(linea) + 1 > CHUNK:
                    partes.append("\n".join(actual))
                    actual, largo_actual = [linea], len(linea)
                else:
                    actual.append(linea)
                    largo_actual += len(linea) + 1
            if actual:
                partes.append("\n".join(actual))

            for i, parte in enumerate(partes):
                embed_general.add_field(
                    name=f"📤 Retiros ({total_ret_g})" if i == 0 else f"📤 Retiros ({total_ret_g}) (cont. {i+1})",
                    value=parte,
                    inline=False,
                )

            logger.info(
                "📤 [Operativo auto] Enviando resumen general a canal balas | "
                f"canal={_CANAL_BALAS} | partes={len(partes)}"
            )
            await _enviar_embed_a_canal(
                _CANAL_BALAS,
                embed=embed_general,
                content=f"<@&{_BALLAS_ROLE_ID}>",
                contexto="cierre automático / resumen general",
            )
            logger.info("✅ [Operativo auto] Resumen general enviado")
        else:
            logger.info("ℹ️ [Operativo auto] No hay rows en BD para resumen general (quizás sin movimientos)")

    except Exception as e:
        logger.error(f"❌ Error mandando resumen automático: {e}", exc_info=True)

    # ── Resumen pistolas por usuario → canal armeros ──────────────
    try:
        from config import CATEGORIAS
        from database import get_db_connection

        logger.info("🔎 [Operativo auto] Consultando BD para resumen de pistolas por usuario")
        PISTOLAS = set(CATEGORIAS["pistolas"])
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT nombre, discord_id, objeto,
                   SUM(CASE WHEN tipo='RETIRO'   THEN cantidad ELSE 0 END) AS retiros,
                   SUM(CASE WHEN tipo='DEPOSITO' THEN cantidad ELSE 0 END) AS depositos
            FROM registros_armas
            WHERE en_operativo = TRUE AND timestamp >= %s AND objeto = ANY(%s)
            GROUP BY nombre, discord_id, objeto ORDER BY nombre, retiros DESC
        """, (inicio_op, list(PISTOLAS)))
        rows_pist = cursor.fetchall()
        cursor.close()
        conn.close()
        logger.info(f"✅ [Operativo auto] Consulta pistolas por usuario OK | filas={len(rows_pist)}")

        if rows_pist:
            personas: dict = {}
            for r in rows_pist:
                key = (r["nombre"], r["discord_id"])
                if key not in personas:
                    personas[key] = {
                        "nombre":     r["nombre"],
                        "discord_id": r["discord_id"],
                        "objetos":    [],
                    }
                personas[key]["objetos"].append({
                    "objeto":    r["objeto"],
                    "retiros":   int(r["retiros"]   or 0),
                    "depositos": int(r["depositos"] or 0),
                })

            lineas = []
            for (nombre, discord_id), data in personas.items():
                usuario   = f"<@{discord_id}>" if discord_id else (nombre or "N/A")
                tot_ret   = sum(o["retiros"]   for o in data["objetos"])
                tot_dep   = sum(o["depositos"] for o in data["objetos"])
                bal_total = tot_dep - tot_ret
                emoji     = "📈" if bal_total > 0 else ("📉" if bal_total < 0 else "📊")
                detalle_items = [
                    f"   {'📈' if o['depositos'] - o['retiros'] > 0 else ('📉' if o['depositos'] - o['retiros'] < 0 else '📊')} "
                    f"{traducir_objeto(o['objeto'])}: R:{o['retiros']} D:{o['depositos']} B:{o['depositos'] - o['retiros']:+d}"
                    for o in sorted(data["objetos"], key=lambda x: x["retiros"], reverse=True)
                ]
                lineas.append(
                    f"{emoji} **{nombre or 'N/A'}** ({usuario})\n"
                    f"   Total — R:{tot_ret} D:{tot_dep} B:{bal_total:+d}\n"
                    + "\n".join(detalle_items)
                )

            embed_pist = discord.Embed(
                title="🔫 Pistolas por Usuario — Operativo",
                color=discord.Color.blue(),
                timestamp=datetime.now(),
            )
            embed_pist.set_footer(text=f"Duración: {duracion} | Terminado automáticamente")
            logger.info("📝 [Operativo auto] Embed de pistolas por usuario armado")

            CHUNK = 1024
            partes, actual, largo_actual = [], [], 0
            for linea in lineas:
                if largo_actual + len(linea) + 2 > CHUNK:
                    partes.append("\n\n".join(actual))
                    actual, largo_actual = [linea], len(linea)
                else:
                    actual.append(linea)
                    largo_actual += len(linea) + 2
            if actual:
                partes.append("\n\n".join(actual))

            for i, parte in enumerate(partes):
                embed_pist.add_field(
                    name="👥 Usuarios" if i == 0 else f"👥 Usuarios (cont. {i+1})",
                    value=parte[:1024],
                    inline=False,
                )

            from config import ARMERO_ROLE_ID
            logger.info(
                "📤 [Operativo auto] Enviando resumen de pistolas por usuario | "
                f"canal={ONLY_ARMEROS_CHANNEL_ID} | partes={len(partes)}"
            )
            await _enviar_embed_a_canal(
                ONLY_ARMEROS_CHANNEL_ID,
                embed=embed_pist,
                content=f"<@&{ARMERO_ROLE_ID}>",
                contexto="cierre automático / pistolas por usuario",
            )
            logger.info("✅ [Operativo auto] Resumen de pistolas por usuario enviado")
        else:
            logger.info("ℹ️ [Operativo auto] No hay pistolas registradas en el operativo")

    except Exception as e:
        logger.error(f"❌ Error enviando resumen pistolas (auto): {e}", exc_info=True)

    logger.info("➡️ [Operativo auto] Pasando al resumen de asistencia")
    await _enviar_resumen_asistencia_cierre()
    logger.info("🏁 [Operativo auto] Cierre automático finalizado")

async def _enviar_resumen_asistencia_cierre():

    # ── Resumen de asistencia de eventos ──────────────────────
    try:
        from asistencia import get_sesiones_activas, handle_op_end, EstadoAsistencia
        from asistencia_plantilla import get_info_miembro
        from commands.cmd_asistencia import set_ultimo_resultado
        from sheets import registrar_asistencia_op

        logger.info("🔎 [Operativo auto] Entrando al bloque de asistencia de cierre")
        sesiones_activas = get_sesiones_activas()
        logger.info(f"📋 [Operativo auto] Sesiones activas encontradas: {sesiones_activas}")
        if not sesiones_activas:
            logger.info("ℹ️ [Operativo auto] No hay eventos vinculados al operativo, sin resumen de asistencia")
            return

        for event_id in sesiones_activas:
            try:
                logger.info(f"➡️ [Operativo auto] Cerrando asistencia para event_id={event_id}")
                resultado = await handle_op_end(event_id)
                if not resultado:
                    logger.info(f"ℹ️ [Operativo auto] handle_op_end devolvió None para event_id={event_id}")
                    continue

                resultado["event_id"] = event_id
                logger.info(f"🧠 [Operativo auto] Resultado asistencia obtenido para event_id={event_id}")
                set_ultimo_resultado(resultado)
                logger.info(f"💾 [Operativo auto] Guardando resultado en task de Sheets | event_id={event_id}")
                _bot.loop.create_task(registrar_asistencia_op(resultado))

                resumen = resultado.get("resumen", {})
                embed_asist = discord.Embed(
                    title="📊 Asistencia del Operativo",
                    description=f"Evento: **{resultado.get('evento', '—')}**",
                    color=discord.Color.purple(),
                    timestamp=datetime.now(),
                )
                embed_asist.add_field(
                    name="📈 Resumen",
                    value=(
                        f"✅ Asistieron: {resumen.get('asistio', 0)}\n"
                        f"❌ Faltaron: {resumen.get('falto', 0)}\n"
                        f"⚠️ No confirmados: {resumen.get('no_confirmado', 0)}\n"
                        f"Justificados: {resumen.get('justificado', 0)}\n"
                        f"⬛ Ausentes: {resumen.get('ausente', 0)}"
                    ),
                    inline=False,
                )
                logger.info(f"📝 [Operativo auto] Embed de asistencia armado para event_id={event_id}")
                faltaron = [
                    get_info_miembro(did).get("nombre_ic", f"ID {did}")
                    for did, est in resultado.get("miembros", {}).items()
                    if est == EstadoAsistencia.FALTO
                ]
                if faltaron:
                    embed_asist.add_field(
                        name="❌ Faltaron",
                        value="\n".join(f"• {n}" for n in faltaron[:15]),
                        inline=False,
                    )
                no_confirmados = [
                    get_info_miembro(did).get("nombre_ic", f"ID {did}")
                    for did, est in resultado.get("miembros", {}).items()
                    if est == EstadoAsistencia.NO_CONFIRMADO
                ]
                if no_confirmados:
                    embed_asist.add_field(
                        name="Asistieron sin marcar",
                        value="\n".join(f"- {n}" for n in no_confirmados[:15]),
                        inline=False,
                    )
                justificados = [
                    get_info_miembro(did).get("nombre_ic", f"ID {did}")
                    for did, est in resultado.get("miembros", {}).items()
                    if est == EstadoAsistencia.JUSTIFICADO
                ]
                if justificados:
                    embed_asist.add_field(
                        name="Justificados",
                        value="\n".join(f"- {n}" for n in justificados[:15]),
                        inline=False,
                    )
                embed_asist.set_footer(text="Guardado en Google Sheets → /asistencia para ver detalles")
                logger.info(
                    "📤 [Operativo auto] Enviando resumen de asistencia | "
                    f"event_id={event_id} | canal={ONLY_ARMEROS_CHANNEL_ID}"
                )
                await _enviar_embed_a_canal(
                    ONLY_ARMEROS_CHANNEL_ID,
                    embed=embed_asist,
                    contexto=f"resumen asistencia evento {event_id}",
                )
                logger.info(f"✅ [Operativo auto] Resumen de asistencia enviado | event_id={event_id}")
            except Exception as e:
                logger.error(f"❌ Error finalizando asistencia evento {event_id}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"❌ Error en bloque de asistencia al cerrar operativo automático: {e}", exc_info=True)


async def _tarea_guardar_operativo():
    while state.operativo_activo["activo"]:
        await asyncio.sleep(300)
        if not state.operativo_activo["activo"]:
            break
        try:
            await asyncio.to_thread(guardar_estado_operativo_db, True, state.operativo_activo.get("inicio"), state.operativo_activo.get("iniciado_por"), state.operativo_activo)
            logger.info("💾 Guardado periódico del operativo OK")
        except Exception as e:
            logger.error(f"❌ Error en guardado periódico del operativo: {e}", exc_info=True)


def _hubo_movimiento_operativo_desde(sent_at: Optional[datetime]) -> bool:
    if sent_at is None:
        return False
    try:
        from database import get_db_connection

        if getattr(sent_at, "tzinfo", None) is not None:
            sent_at = sent_at.replace(tzinfo=None)

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 1
            FROM registros_armas
            WHERE en_operativo = TRUE
              AND timestamp >= %s
              AND tipo IN ('RETIRO', 'DEPOSITO')
            LIMIT 1
            """,
            (sent_at,),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return row is not None
    except Exception as e:
        logger.warning(f"⚠️ No se pudo verificar movimiento reciente del operativo: {e}")
        return False


# ─── INICIO ───────────────────────────────────────────────────

def iniciar_operativo(iniciado_por_id: int):
    global _verificacion_task, _guardado_task
    _cancelar_tareas_operativo()
    state.operativo_activo.update({
        "activo":             True,
        "inicio":             datetime.now(),
        "iniciado_por":       iniciado_por_id,
        "registros":          [],
        "pistolas_depositos": defaultdict(int),
        "pistolas_retiros":   defaultdict(int),
        "control_msg_id":     None,
        "control_channel_id": None,
        "verify_msg_id":      None,
        "verify_channel_id":  None,
        "verify_sent_at":     None,
    })
    state.operativo_recuperado = True
    state.VERIFICACION_OPERATIVO_CONFIG["verify_msg_id"]     = None
    state.VERIFICACION_OPERATIVO_CONFIG["verify_channel_id"] = None
    guardar_estado_operativo_db(True, state.operativo_activo["inicio"], iniciado_por_id, state.operativo_activo)
    _guardado_task = _bot.loop.create_task(_tarea_guardar_operativo())
    if state.VERIFICACION_OPERATIVO_CONFIG.get("activo", True):
        _verificacion_task = _bot.loop.create_task(_tarea_verificacion_operativo())


# ─── FIN ──────────────────────────────────────────────────────

def finalizar_operativo(*, preservar_tarea_actual: bool = False):
    retiros   = dict(state.operativo_activo.get("pistolas_retiros")   or {})
    depositos = dict(state.operativo_activo.get("pistolas_depositos")  or {})
    snapshot  = {
        "retiros":      retiros,
        "depositos":    depositos,
        "inicio":       state.operativo_activo["inicio"],
        "iniciado_por": state.operativo_activo["iniciado_por"],
    }
    state.operativo_activo.update({
        "activo":             False,
        "inicio":             None,
        "iniciado_por":       None,
        "registros":          [],
        "pistolas_depositos": defaultdict(int),
        "pistolas_retiros":   defaultdict(int),
        "control_msg_id":     None,
        "control_channel_id": None,
        "verify_msg_id":      None,
        "verify_channel_id":  None,
        "verify_sent_at":     None,
    })
    state.operativo_recuperado = False
    state.VERIFICACION_OPERATIVO_CONFIG["verify_msg_id"]     = None
    state.VERIFICACION_OPERATIVO_CONFIG["verify_channel_id"] = None
    _cancelar_tareas_operativo(preservar_tarea_actual=preservar_tarea_actual)
    guardar_estado_operativo_db(False, None, None, state.operativo_activo)
    return snapshot


# ─── RESTAURAR DESDE BD ───────────────────────────────────────

async def restaurar_operativo_desde_db(row: dict):
    global _verificacion_task, _guardado_task
    from database import get_db_connection

    _cancelar_tareas_operativo()
    state.operativo_activo.update({
        "activo":             True,
        "inicio":             row.get("inicio") or datetime.now(),
        "iniciado_por":       int(row["iniciado_por"]) if row.get("iniciado_por") else None,
        "registros":          [],
        "control_msg_id":     row.get("control_msg_id"),
        "control_channel_id": row.get("control_channel_id"),
        "verify_msg_id":      row.get("verify_msg_id"),
        "verify_channel_id":  row.get("verify_channel_id"),
        "verify_sent_at":     row.get("verify_sent_at"),
    })
    state.VERIFICACION_OPERATIVO_CONFIG["verify_msg_id"]     = row.get("verify_msg_id")
    state.VERIFICACION_OPERATIVO_CONFIG["verify_channel_id"] = row.get("verify_channel_id")
    state.operativo_recuperado = True
    inicio = state.operativo_activo["inicio"]

    retiros   = defaultdict(int)
    depositos = defaultdict(int)
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT tipo, objeto, cantidad FROM registros_armas WHERE en_operativo = TRUE AND timestamp >= %s", (inicio,))
        registros = cursor.fetchall()
        cursor.close()
        conn.close()
        for r in registros:
            obj  = r["objeto"]
            cant = int(r["cantidad"] or 0)
            if r["tipo"] == "RETIRO":
                retiros[obj]   += cant
            else:
                depositos[obj] += cant
        logger.info(f"✅ Contadores restaurados | Retiros: {sum(retiros.values())} | Depósitos: {sum(depositos.values())}")
    except Exception as e:
        logger.error(f"❌ Error reconstruyendo contadores del operativo: {e}", exc_info=True)

    state.operativo_activo["pistolas_retiros"]   = retiros
    state.operativo_activo["pistolas_depositos"] = depositos
    await _forzar_nuevo_control()
    _guardado_task = _bot.loop.create_task(_tarea_guardar_operativo())
    if state.VERIFICACION_OPERATIVO_CONFIG.get("activo", True):
        _verificacion_task = _bot.loop.create_task(_tarea_verificacion_operativo())


async def _forzar_nuevo_control():
    channel = _bot.get_channel(ALERTAS_CHANNEL_ID)
    if not channel:
        logger.warning(f"⚠️ Canal de control no encontrado: {ALERTAS_CHANNEL_ID}")
        return
    retiros   = dict(state.operativo_activo.get("pistolas_retiros")   or {})
    depositos = dict(state.operativo_activo.get("pistolas_depositos")  or {})
    embed     = _build_control_embed(retiros, depositos)
    old_msg_id = state.operativo_activo.get("control_msg_id")
    if old_msg_id:
        try:
            old = await channel.fetch_message(int(old_msg_id))
            await old.delete()
        except (discord.NotFound, discord.HTTPException):
            pass
        state.operativo_activo["control_msg_id"]     = None
        state.operativo_activo["control_channel_id"] = None
    try:
        msg = await channel.send(embed=embed)
        state.operativo_activo["control_msg_id"]     = msg.id
        state.operativo_activo["control_channel_id"] = channel.id
        logger.info(f"✅ Mensaje de control enviado | ID: {msg.id}")
    except (discord.Forbidden, discord.HTTPException) as e:
        logger.error(f"❌ Error enviando mensaje de control: {e}", exc_info=True)
