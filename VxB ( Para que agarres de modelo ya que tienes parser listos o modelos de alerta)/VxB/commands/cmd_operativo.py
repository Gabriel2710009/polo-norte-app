import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands

import state
from config import CATEGORIAS, ONLY_ARMEROS_CHANNEL_ID, ARMERO_ROLE_ID
from utils import es_armero_o_alto_cargo, traducir_objeto
from operativo import iniciar_operativo, finalizar_operativo, _obtener_canal_seguro, _enviar_embed_a_canal
from database import get_db_connection

logger = logging.getLogger("ArmamentBot")

CANAL_BALAS    = 1015394887581061120
BALLAS_ROLE_ID = 1212120053936427049


# ─── MODAL UMBRALES ──────────────────────────────────────────

class ConfigurarUmbralesModal(discord.ui.Modal, title="Configurar umbrales de cantidad"):
    """
    Configura múltiples umbrales a la vez.
    Formato: objeto=cantidad (uno por línea)
    Ejemplo:
        money=500
        ammo-9=100
        WEAPON_PISTOL=1
    Poner cantidad 0 elimina el umbral del objeto.
    """
    umbrales = discord.ui.TextInput(
        label="Umbrales (objeto=cantidad, uno por línea)",
        placeholder=(
            "money=500\n"
            "ammo-9=100\n"
            "WEAPON_PISTOL=1\n\n"
            "Cantidad 0 = eliminar umbral"
        ),
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=800,
    )

    async def on_submit(self, interaction: discord.Interaction):
        from utils import traducir_objeto as _trad
        from log_actions import log_accion

        await interaction.response.defer(ephemeral=True)

        lineas  = str(self.umbrales.value).strip().splitlines()
        errores = []
        cambios = []

        for linea in lineas:
            linea = linea.strip()
            if not linea or linea.startswith("#"):
                continue
            if "=" not in linea:
                errores.append(f"• `{linea}` — formato inválido (usar `objeto=cantidad`)")
                continue
            partes = linea.split("=", 1)
            objeto = partes[0].strip()
            try:
                cantidad = int(partes[1].strip())
            except ValueError:
                errores.append(f"• `{linea}` — cantidad no es un número")
                continue
            if cantidad < 0:
                errores.append(f"• `{linea}` — cantidad no puede ser negativa")
                continue

            nombre_obj = _trad(objeto)
            if cantidad == 0:
                state.UMBRALES_CANTIDAD.pop(objeto, None)
                cambios.append(f"• **{nombre_obj}** (`{objeto}`) → ❌ umbral eliminado")
            else:
                state.UMBRALES_CANTIDAD[objeto] = cantidad
                cambios.append(f"• **{nombre_obj}** (`{objeto}`) → alerta si retira ≥ **{cantidad}**")

        embed = discord.Embed(
            title="⚙️ Umbrales actualizados",
            color=discord.Color.green() if cambios else discord.Color.orange(),
            timestamp=datetime.now(),
        )
        if cambios:
            embed.add_field(name="✅ Cambios aplicados", value="\n".join(cambios), inline=False)
        if errores:
            embed.add_field(name="❌ Errores", value="\n".join(errores), inline=False)

        if state.UMBRALES_CANTIDAD:
            activos_txt = "\n".join(f"• **{_trad(k)}** (`{k}`): ≥ {v}" for k, v in sorted(state.UMBRALES_CANTIDAD.items()))
            embed.add_field(name="📋 Umbrales activos ahora", value=activos_txt, inline=False)
        else:
            embed.add_field(name="📋 Umbrales activos ahora", value="*Ninguno — todos los retiros generan alerta*", inline=False)

        embed.set_footer(text=f"Configurado por {interaction.user.name}")
        await interaction.followup.send(embed=embed, ephemeral=True)

        if cambios:
            await log_accion(interaction.user, "Configuró umbrales de cantidad", "\n".join(cambios)[:500], discord.Color.blue(), "⚙️")


def register(tree: app_commands.CommandTree):

    # ── /inicio_operativo ─────────────────────────────────────
    @tree.command(name="inicio_operativo", description="Iniciar un operativo")
    async def inicio_operativo(interaction: discord.Interaction):
        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        if state.operativo_activo["activo"]:
            await interaction.response.send_message("⚠️ Ya hay un operativo activo.", ephemeral=True)
            return

        iniciar_operativo(interaction.user.id)

        cfg = state.VERIFICACION_OPERATIVO_CONFIG
        verificacion_txt = (
            f"\n\n⏰ Verificación periódica cada **{cfg.get('intervalo_minutos', 60)} min** "
            f"(timeout: {cfg.get('timeout_minutos', 10)} min)"
            if cfg.get("activo", True) else "\n\n⏰ Verificación periódica **desactivada**"
        )

        embed = discord.Embed(
            title="🧩 OPERATIVO INICIADO",
            description=f"Iniciado por {interaction.user.mention}{verificacion_txt}",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        embed.set_footer(text=f"Iniciado a las {datetime.now().strftime('%H:%M:%S')}")
        await interaction.response.send_message(embed=embed)
        logger.info(f"✅ Operativo iniciado por {interaction.user}")

# ── /terminar_operativo ───────────────────────────────────
    @tree.command(name="terminar_operativo", description="Terminar el operativo activo")
    async def terminar_operativo(interaction: discord.Interaction):
        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        if not state.operativo_activo["activo"]:
            await interaction.response.send_message("⚠️ No hay operativo activo.", ephemeral=True)
            return

        await interaction.response.defer()

        inicio_op = state.operativo_activo["inicio"]
        snapshot  = finalizar_operativo()
        inicio    = snapshot["inicio"]

        duracion = ""
        if inicio:
            delta   = datetime.now() - inicio
            minutos = int(delta.total_seconds() // 60)
            duracion = f"{minutos // 60}h {minutos % 60}m"

        # ── Consultar BD para tener TODOS los ítems (no solo pistolas) ──
        retiros_bd   = {}
        depositos_bd = {}
        if inicio_op:
            try:
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
                for r in cursor.fetchall():
                    obj = r["objeto"]
                    if int(r["retiros"]   or 0) > 0:
                        retiros_bd[obj]   = int(r["retiros"])
                    if int(r["depositos"] or 0) > 0:
                        depositos_bd[obj] = int(r["depositos"])
                cursor.close()
                conn.close()
            except Exception as e:
                logger.error(f"❌ Error consultando BD para embed cierre: {e}", exc_info=True)

        def _resumen(dic):
            if not dic:
                return "*Sin movimientos*"
            return "\n".join(
                f"• {traducir_objeto(o)}: {c}"
                for o, c in sorted(dic.items(), key=lambda x: x[1], reverse=True)
            )[:1024]

        balance_por_arma = {
            obj: depositos_bd.get(obj, 0) - retiros_bd.get(obj, 0)
            for obj in set(retiros_bd) | set(depositos_bd)
        }
        balance_texto = (
            "\n".join(
                f"• {traducir_objeto(obj)}: {bal:+d}"
                for obj, bal in sorted(balance_por_arma.items())
            )[:1024]
            or "*Sin movimientos*"
        )
        total_ret = sum(retiros_bd.values())
        total_dep = sum(depositos_bd.values())
        bal_total = total_dep - total_ret
        b_emoji   = "📈" if bal_total > 0 else ("📉" if bal_total < 0 else "📊")

        embed = discord.Embed(
            title="🏁 OPERATIVO TERMINADO",
            description=(
                f"Terminado por {interaction.user.mention}\n"
                f"⏱️ Duración: **{duracion}**\n"
                f"📉 Balance total: **{bal_total:+d}**"
            ) if duracion else f"Terminado por {interaction.user.mention}",
            color=discord.Color.red(),
            timestamp=datetime.now(),
        )
        embed.add_field(name=f"📤 Retiros ({total_ret})", value=_resumen(retiros_bd), inline=False)
        embed.add_field(name=f"📥 Depósitos ({total_dep})", value=_resumen(depositos_bd), inline=False)
        embed.add_field(name="⚖️ Balance por arma", value=balance_texto, inline=False)
        embed.set_footer(text=f"Terminado a las {datetime.now().strftime('%H:%M:%S')}")
        await interaction.followup.send(embed=embed)
        logger.info(f"✅ Operativo terminado por {interaction.user}")

        if not inicio_op:
            return

        # ── Resumen General → canal balas ──────────────────────────────
        try:
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

            if rows_general:
                total_ret_g = sum(int(r["retiros"]   or 0) for r in rows_general)
                total_dep_g = sum(int(r["depositos"] or 0) for r in rows_general)
                bal_total_g = total_dep_g - total_ret_g
                b_emoji_g   = "📈" if bal_total_g > 0 else ("📉" if bal_total_g < 0 else "📊")

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
                await _enviar_embed_a_canal(
                    CANAL_BALAS,
                    embed=embed_general,
                    content=f"<@&{BALLAS_ROLE_ID}>",
                    contexto="cierre manual / resumen general",
                )
        except Exception as e:
            logger.error(f"❌ Error enviando resumen general: {e}", exc_info=True)

        # ── Resumen pistolas por usuario → canal armeros ───────────────
        try:
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

            if rows_pist:
                personas: dict = {}
                for r in rows_pist:
                    key = (r["nombre"], r["discord_id"])
                    if key not in personas:
                        personas[key] = {"nombre": r["nombre"], "discord_id": r["discord_id"], "objetos": []}
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
                embed_pist.set_footer(text=f"Duración: {duracion}")

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
                await _enviar_embed_a_canal(
                    ONLY_ARMEROS_CHANNEL_ID,
                    embed=embed_pist,
                    content=f"<@&{ARMERO_ROLE_ID}>",
                    contexto="cierre manual / pistolas por usuario",
                )
        except Exception as e:
            logger.error(f"❌ Error enviando resumen pistolas por usuario: {e}", exc_info=True)

        from asistencia import get_sesiones_activas, handle_op_end
        from commands.cmd_asistencia import set_ultimo_resultado
        from sheets import registrar_asistencia_op
 
        sesiones_activas = get_sesiones_activas()
        for event_id in sesiones_activas:
            try:
                resultado = await handle_op_end(event_id)
                if resultado:
                    resultado["event_id"] = event_id
                    set_ultimo_resultado(resultado)
                    # Guardar en Google Sheets en background
                    interaction.client.loop.create_task(registrar_asistencia_op(resultado))
                    # Enviar resumen de asistencia al canal de armeros
                    from asistencia import EstadoAsistencia
                    from asistencia_plantilla import get_info_miembro
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
                    await _enviar_embed_a_canal(
                        ONLY_ARMEROS_CHANNEL_ID,
                        embed=embed_asist,
                        contexto=f"resumen asistencia evento {event_id}",
                    )
            except Exception as e:
                logger.error(f"❌ Errór finalizando asistencia evento {event_id}: {e}", exc_info=True)

    # ── /config_verificacion ──────────────────────────────────
    @tree.command(name="config_verificacion", description="Configurar verificación periódica del operativo")
    @app_commands.describe(
        activo="Activar o desactivar la verificación periódica",
        intervalo_minutos="Cada cuántos minutos se pide verificación (mínimo 1, default: 60)",
        timeout_minutos="Minutos para responder antes de terminar el operativo (mínimo 1, default: 10)",
    )
    async def config_verificacion(
        interaction: discord.Interaction,
        activo: Optional[bool] = None,
        intervalo_minutos: Optional[int] = None,
        timeout_minutos: Optional[int] = None,
    ):
        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        cfg     = state.VERIFICACION_OPERATIVO_CONFIG
        cambios = []

        if activo is not None:
            cfg["activo"] = activo
            cambios.append(f"• Estado: {'✅ Activa' if activo else '❌ Desactivada'}")

        if intervalo_minutos is not None:
            if intervalo_minutos < 1:
                await interaction.response.send_message("⚠️ El intervalo mínimo es 1 minuto.", ephemeral=True)
                return
            cfg["intervalo_minutos"] = intervalo_minutos
            cambios.append(f"• Intervalo: **{intervalo_minutos} min**")

        if timeout_minutos is not None:
            if timeout_minutos < 1:
                await interaction.response.send_message("⚠️ El timeout mínimo es 1 minuto.", ephemeral=True)
                return
            cfg["timeout_minutos"] = timeout_minutos
            cambios.append(f"• Timeout: **{timeout_minutos} min**")

        embed = discord.Embed(
            title="⏰ Configuración de verificación de operativo",
            color=discord.Color.green() if cambios else discord.Color.blue(),
            timestamp=datetime.now(),
        )
        if cambios:
            embed.add_field(name="✅ Cambios aplicados", value="\n".join(cambios), inline=False)
        embed.add_field(
            name="📋 Configuración actual",
            value=(
                f"• Estado: {'✅ Activa' if cfg.get('activo', True) else '❌ Desactivada'}\n"
                f"• Intervalo: **{cfg.get('intervalo_minutos', 60)} min**\n"
                f"• Timeout: **{cfg.get('timeout_minutos', 10)} min**"
            ),
            inline=False,
        )
        embed.set_footer(text=f"Por {interaction.user.name}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

        if cambios:
            from log_actions import log_accion
            await log_accion(interaction.user, "Configuró verificación de operativo", "\n".join(cambios), discord.Color.blue(), "⏰")

    # ── /configurar_umbrales ──────────────────────────────────
    @tree.command(name="configurar_umbrales", description="Configurar cantidad mínima por ítem para alertas de retiro")
    async def configurar_umbrales(interaction: discord.Interaction):
        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        await interaction.response.send_modal(ConfigurarUmbralesModal())

    # ── /ver_umbrales ─────────────────────────────────────────
    @tree.command(name="ver_umbrales", description="Ver todos los umbrales de cantidad configurados para alertas")
    async def ver_umbrales(interaction: discord.Interaction):
        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        embed = discord.Embed(title="📋 Umbrales de cantidad para alertas", color=discord.Color.blue(), timestamp=datetime.now())
        if state.UMBRALES_CANTIDAD:
            umbrales_txt = "\n".join(f"• **{traducir_objeto(k)}** (`{k}`): alerta si retira ≥ **{v}**" for k, v in sorted(state.UMBRALES_CANTIDAD.items()))
            embed.description = umbrales_txt
        else:
            embed.description = ("*No hay umbrales configurados.*\nTodos los retiros generan alerta sin importar la cantidad.\n\nUsá `/configurar_umbrales` para configurarlos.")
        embed.set_footer(text=f"Consultado por {interaction.user.name}")
        await interaction.response.send_message(embed=embed, ephemeral=True)
