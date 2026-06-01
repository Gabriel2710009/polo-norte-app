"""
commands/cmd_asistencia.py — Comandos de asistencia y gestión de plantilla

Comandos:
    /vincular_operativo  → vincula un Guild Scheduled Event al próximo OP
    /asistencia          → muestra resumen de asistencia del último OP
    /asistencia_semanal_activar   → activa el contador semanal persistente
    /asistencia_semanal_desactivar → desactiva el contador semanal persistente
    /contratar           → agrega un miembro a la plantilla
    /despedir            → marca un miembro como inactivo
    /ver_plantilla       → muestra la plantilla activa paginada
    /perfil_asistencia   → historial de asistencia de un miembro

Integración con main.py:
    En on_ready() agregar:
        from asistencia import set_bot as asistencia_set_bot
        asistencia_set_bot(bot)

    En on_message() / guardar_registro(), tras detectar un RETIRO en operativo:
        from asistencia import on_weapon_withdraw, get_sesiones_activas
        discord_id = int(datos.get("discord_id", 0))
        if discord_id:
            on_weapon_withdraw(discord_id)  # registra en todas las sesiones activas
"""

import logging
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands

from utils import es_armero_o_alto_cargo
from asistencia_plantilla import (
    PLANTILLA,
    agregar_miembro,
    despedir_miembro,
    get_info_miembro,
    refresh_plantilla,
    get_plantilla_activa,
)

logger = logging.getLogger("ArmamentBot")
_ultimo_resultado: Optional[dict] = None
_debug_interesados_tests: dict[int, dict] = {}

# Último resultado de asistencia en memoria (para /asistencia rápido)
_ultimo_resultado: Optional[dict] = None


def set_ultimo_resultado(resultado: dict):
    global _ultimo_resultado
    _ultimo_resultado = resultado


async def _snapshot_evento_interesados(event_id: int) -> Optional[dict]:
    from asistencia import fetch_event_by_id, fetch_event_users

    event = await fetch_event_by_id(event_id)
    if not event:
        return None

    usuarios = await fetch_event_users(event)
    usuarios_ordenados = sorted(int(uid) for uid in usuarios)
    return {
        "event_id": int(event_id),
        "nombre": getattr(event, "name", f"Evento {event_id}"),
        "inicio": getattr(event, "start_time", None),
        "user_count": getattr(event, "user_count", None),
        "conteo_fetch": len(usuarios_ordenados),
        "usuarios": usuarios_ordenados,
    }


def register(tree: app_commands.CommandTree):

    # ── /vincular_operativo ───────────────────────────────────
    @tree.command(
        name="vincular_operativo",
        description="Vincula un evento de Discord al próximo operativo para tracking de asistencia",
    )
    @app_commands.describe(
        event_id="ID del Guild Scheduled Event de Discord (clic derecho → Copiar ID)"
    )
    async def vincular_operativo(interaction: discord.Interaction, event_id: str):
        from asistencia import registrar_evento_operativo

        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        try:
            eid = int(event_id.strip())
        except ValueError:
            await interaction.response.send_message("❌ El Event ID debe ser un número.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        ok = await registrar_evento_operativo(eid, tipo="operativo")
        if ok:
            embed = discord.Embed(
                title="📅 Evento vinculado al operativo",
                description=(
                    f"✅ El evento `{eid}` fue vinculado correctamente.\n\n"
                    "**¿Qué pasa ahora?**\n"
                    "• 5 min antes del inicio → snapshot de confirmados\n"
                    "• Al iniciar el OP → snapshot final + inicio de tracking\n"
                    "• Durante el OP → se registra quién retira arma\n"
                    "• Al terminar con `/terminar_operativo` → resultado en Sheets"
                ),
                color=discord.Color.green(),
                timestamp=datetime.now(),
            )
            embed.set_footer(text=f"Vinculado por {interaction.user.name}")
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(
                "❌ No se pudo vincular el evento. Verificá que el ID sea correcto "
                "y que el evento exista en este servidor.",
                ephemeral=True,
            )

    # ── /asistencia_semanal_activar ──────────────────────────
    @tree.command(
        name="debug_interesados",
        description="Debug para verificar si el bot cuenta los usuarios que marcan 'Me interesa'",
    )
    @app_commands.describe(
        event_id="ID del Guild Scheduled Event de Discord",
        accion="Elegí si querés iniciar, ver o limpiar el test",
    )
    @app_commands.choices(
        accion=[
            app_commands.Choice(name="Iniciar test", value="iniciar"),
            app_commands.Choice(name="Ver estado", value="ver"),
            app_commands.Choice(name="Limpiar test", value="limpiar"),
        ]
    )
    async def debug_interesados(
        interaction: discord.Interaction,
        event_id: str,
        accion: app_commands.Choice[str],
    ):
        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        try:
            eid = int(event_id.strip())
        except ValueError:
            await interaction.response.send_message("❌ El Event ID debe ser un número.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        snapshot = await _snapshot_evento_interesados(eid)
        if not snapshot:
            await interaction.followup.send(
                "❌ No pude leer ese evento. Verificá que exista y que el bot tenga acceso.",
                ephemeral=True,
            )
            return

        accion_value = str(accion.value)
        if accion_value == "limpiar":
            _debug_interesados_tests.pop(eid, None)
            embed = discord.Embed(
                title="🧪 Debug interesados limpiado",
                description=f"Se eliminó el test guardado para el evento `{eid}`.",
                color=discord.Color.red(),
                timestamp=datetime.now(),
            )
            embed.add_field(name="Evento", value=f"{snapshot['nombre']} (`{eid}`)", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        base = _debug_interesados_tests.get(eid)
        if accion_value == "iniciar" or not base:
            _debug_interesados_tests[eid] = {
                "count": snapshot["conteo_fetch"],
                "user_count": snapshot["user_count"],
                "nombre": snapshot["nombre"],
                "started_at": datetime.now(),
            }

        base = _debug_interesados_tests.get(eid)
        delta = snapshot["conteo_fetch"] - int(base.get("count") or 0)
        user_count_txt = str(snapshot["user_count"]) if snapshot["user_count"] is not None else "N/D"

        embed = discord.Embed(
            title="🧪 Debug de 'Me interesa'",
            description=(
                "Este test guarda una línea base y luego compara cuántos usuarios devuelve el evento.\n"
                "Si sube el conteo después de que alguien marque 'Me interesa', el bot lo está leyendo."
            ),
            color=discord.Color.orange() if delta == 0 else discord.Color.green(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="Evento", value=f"{snapshot['nombre']} (`{eid}`)", inline=False)
        embed.add_field(name="Línea base", value=str(base.get("count", 0)), inline=True)
        embed.add_field(name="Conteo actual", value=str(snapshot["conteo_fetch"]), inline=True)
        embed.add_field(name="Delta", value=f"{delta:+d}", inline=True)
        embed.add_field(name="user_count API", value=user_count_txt, inline=True)
        embed.add_field(
            name="Test guardado",
            value=base.get("started_at").strftime("%d/%m/%Y %H:%M:%S") if base.get("started_at") else "N/D",
            inline=True,
        )
        sample = snapshot["usuarios"][:20]
        embed.add_field(
            name="IDs detectados",
            value=", ".join(f"`{uid}`" for uid in sample) if sample else "Sin usuarios",
            inline=False,
        )
        embed.set_footer(text="Usá /debug_interesados con la misma ID para volver a comparar.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @tree.command(
        name="asistencia_semanal_activar",
        description="Activa el contador semanal de asistencia",
    )
    async def asistencia_semanal_activar(interaction: discord.Interaction):
        from asistencia import activar_asistencia_semanal
        from sheets import sincronizar_asistencia_semanal

        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        activar_asistencia_semanal(interaction.user.name)
        try:
            interaction.client.loop.create_task(sincronizar_asistencia_semanal())
        except Exception:
            pass

        embed = discord.Embed(
            title="📊 Asistencia semanal activada",
            description=(
                "El contador semanal quedó **activo** y empezó a contar desde ahora.\n"
                "La semana se sigue cerrando los domingos a las 23:00 hora España."
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="Estado", value="✅ Activa", inline=True)
        embed.add_field(name="Activada por", value=interaction.user.mention, inline=True)
        embed.set_footer(text=f"Activado por {interaction.user.name}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /asistencia_semanal_desactivar ───────────────────────
    @tree.command(
        name="asistencia_semanal_desactivar",
        description="Desactiva el contador semanal de asistencia",
    )
    async def asistencia_semanal_desactivar(interaction: discord.Interaction):
        from asistencia import desactivar_asistencia_semanal
        from sheets import sincronizar_asistencia_semanal

        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        desactivar_asistencia_semanal(interaction.user.name)
        try:
            interaction.client.loop.create_task(sincronizar_asistencia_semanal())
        except Exception:
            pass

        embed = discord.Embed(
            title="📴 Asistencia semanal desactivada",
            description="El contador semanal quedó apagado. No se seguirán sumando operativos hasta reactivarlo.",
            color=discord.Color.red(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="Estado", value="❌ Desactivada", inline=True)
        embed.add_field(name="Desactivada por", value=interaction.user.mention, inline=True)
        embed.set_footer(text=f"Desactivado por {interaction.user.name}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /asistencia_semanal_estado ───────────────────────────
    @tree.command(
        name="asistencia_semanal_estado",
        description="Ver el estado del contador semanal de asistencia",
    )
    async def asistencia_semanal_estado(interaction: discord.Interaction):
        import state as _state

        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        cfg = dict(_state.ASISTENCIA_SEMANAL_CONFIG)
        activo = bool(cfg.get("activo", False))
        activado_por = cfg.get("activado_por") or "—"
        activado_at = cfg.get("activado_at")
        activado_at_txt = activado_at.strftime("%d/%m/%Y %H:%M") if activado_at else "—"

        embed = discord.Embed(
            title="📊 Estado de asistencia semanal",
            color=discord.Color.green() if activo else discord.Color.red(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="Estado", value="✅ Activa" if activo else "❌ Desactivada", inline=True)
        embed.add_field(name="Activada por", value=activado_por, inline=True)
        embed.add_field(name="Activada en", value=activado_at_txt, inline=True)
        embed.add_field(
            name="Objetivo",
            value=f"{4} operativos por semana" if activo else "No está contando ahora mismo",
            inline=False,
        )
        embed.set_footer(text="La semana cuenta de lunes 00:00 a domingo 23:59 hora España")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /asistencia ───────────────────────────────────────────
    @tree.command(
        name="asistencia",
        description="Ver el resumen de asistencia del último operativo",
    )
    async def ver_asistencia(interaction: discord.Interaction):
        from asistencia import EstadoAsistencia
        from asistencia_plantilla import get_info_miembro as _info

        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        if not _ultimo_resultado:
            await interaction.response.send_message(
                "ℹ️ No hay resultados de asistencia todavía. "
                "Los resultados se generan automáticamente al terminar un OP vinculado a un evento.",
                ephemeral=True,
            )
            return

        resumen = _ultimo_resultado.get("resumen", {})
        miembros = _ultimo_resultado.get("miembros", {})
        evento   = _ultimo_resultado.get("evento", "Operativo")
        inicio   = _ultimo_resultado.get("inicio")
        fecha    = inicio.strftime("%d/%m/%Y %H:%M") if inicio else "—"

        embed = discord.Embed(
            title=f"📊 Asistencia — {evento}",
            description=f"📅 {fecha}",
            color=discord.Color.blurple(),
            timestamp=datetime.now(),
        )

        # Resumen numérico
        total = len(miembros)
        embed.add_field(
            name="📈 Resumen",
            value=(
                f"✅ Asistieron: **{resumen.get('asistio', 0)}**\n"
                f"❌ Faltaron: **{resumen.get('falto', 0)}**\n"
                f"⚠️ No confirmados: **{resumen.get('no_confirmado', 0)}**\n"
                f"Justificados: **{resumen.get('justificado', 0)}**\n"
                f"⬛ Ausentes: **{resumen.get('ausente', 0)}**\n"
                f"👥 Total: **{total}**"
            ),
            inline=False,
        )

        # Listar faltaron
        faltaron = [
            _info(did).get("nombre_ic", f"ID {did}")
            for did, estado in miembros.items()
            if estado == EstadoAsistencia.FALTO
        ]
        if faltaron:
            texto_faltaron = "\n".join(f"• {n}" for n in faltaron[:20])
            if len(faltaron) > 20:
                texto_faltaron += f"\n… y {len(faltaron) - 20} más"
            embed.add_field(name="❌ Faltaron (marcaron pero no vinieron)", value=texto_faltaron, inline=False)

        # Listar no confirmados
        no_conf = [
            _info(did).get("nombre_ic", f"ID {did}")
            for did, estado in miembros.items()
            if estado == EstadoAsistencia.NO_CONFIRMADO
        ]
        if no_conf:
            texto_nc = "\n".join(f"- {n}" for n in no_conf[:10])
            embed.add_field(name="Asistieron sin marcar", value=texto_nc, inline=False)

        justificados = [
            _info(did).get("nombre_ic", f"ID {did}")
            for did, estado in miembros.items()
            if estado == EstadoAsistencia.JUSTIFICADO
        ]
        if justificados:
            texto_j = "\n".join(f"- {n}" for n in justificados[:10])
            embed.add_field(name="Justificados", value=texto_j, inline=False)

        embed.set_footer(text="Guardado en Google Sheets | Ver /perfil_asistencia para detalles")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /contratar ────────────────────────────────────────────
    @tree.command(
        name="contratar",
        description="Agregar un nuevo miembro a la plantilla Vlone X Ballas",
    )
    @app_commands.describe(
        usuario="Usuario de Discord",
        nombre_ic="Nombre del personaje en el servidor RP",
        rango="Rango inicial",
        steam="Link de Steam (opcional)",
    )
    @app_commands.choices(rango=[
        app_commands.Choice(name="Purple Ghost", value="Purple Ghost"),
        app_commands.Choice(name="Purple Curse", value="Purple Curse"),
        app_commands.Choice(name="Purple Soul", value="Purple Soul"),
        app_commands.Choice(name="Purple Demon", value="Purple Demon"),
        app_commands.Choice(name="Purple Venom", value="Purple Venom"),
        app_commands.Choice(name="Baby Purple", value="Baby Purple"),
    ])
    async def contratar(
        interaction: discord.Interaction,
        usuario: discord.Member,
        nombre_ic: str,
        rango: app_commands.Choice[str],
        steam: Optional[str] = None,
    ):
        from log_actions import log_accion
        from sheets_plantilla import agregar_miembro_a_doc

        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Verificar si ya existe
        plantilla_actual = refresh_plantilla(force=True)
        info_existente = plantilla_actual.get(usuario.id)
        if info_existente and info_existente.get("activo", True):
            await interaction.followup.send(
                f"⚠️ {usuario.mention} ya está en la plantilla como **{PLANTILLA[usuario.id].get('nombre_ic')}**.",
                ephemeral=True,
            )
            return

        tag = f"{usuario.name}"
        ok  = agregar_miembro(
            discord_id=usuario.id,
            nombre_ic=nombre_ic.strip(),
            discord_tag=tag,
            rango=rango.value,
            steam=steam or "",
        )

        if not ok:
            # Si ya existía pero inactivo, reactivar
            PLANTILLA[usuario.id]["activo"]    = True
            PLANTILLA[usuario.id]["nombre_ic"] = nombre_ic.strip()
            PLANTILLA[usuario.id]["rango"]     = rango.value

        # Sincronizar Sheets en background
        interaction.client.loop.create_task(sincronizar_plantilla())
        interaction.client.loop.create_task(sincronizar_doc_desde_sheets())

        embed = discord.Embed(
            title="🟢 Nuevo miembro contratado",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="👤 Discord",    value=usuario.mention,   inline=True)
        embed.add_field(name="🎭 Nombre IC",  value=nombre_ic,         inline=True)
        embed.add_field(name="🏷️ Rango",      value=rango.value,       inline=True)
        embed.set_footer(text=f"Contratado por {interaction.user.name}")

        await interaction.followup.send(embed=embed, ephemeral=False)
        await log_accion(
            interaction.user, "Contrató miembro",
            f"{usuario.mention} ({usuario.id}) | IC: {nombre_ic} | Rango: {rango.value}",
            discord.Color.green(), "🟢",
        )

    # ── /despedir ─────────────────────────────────────────────
    @tree.command(
        name="despedir",
        description="Dar de baja a un miembro de la plantilla",
    )
    @app_commands.describe(
        usuario="Usuario de Discord a despedir",
        motivo="Motivo del despido (opcional)",
    )
    async def despedir(
        interaction: discord.Interaction,
        usuario: discord.Member,
        motivo: Optional[str] = None,
    ):
        from log_actions import log_accion
        from sheets import sincronizar_plantilla
        from sheets_plantilla import sincronizar_doc_desde_sheets

        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        refresh_plantilla(force=True)
        info = get_info_miembro(usuario.id)
        if not PLANTILLA.get(usuario.id):
            await interaction.followup.send(
                f"⚠️ {usuario.mention} no está en la plantilla.", ephemeral=True
            )
            return

        if not PLANTILLA[usuario.id].get("activo", True):
            await interaction.followup.send(
                f"⚠️ {usuario.mention} ya está marcado como despedido.", ephemeral=True
            )
            return

        despedir_miembro(usuario.id)

        # Sincronizar Sheets en background
        interaction.client.loop.create_task(sincronizar_plantilla())
        interaction.client.loop.create_task(sincronizar_doc_desde_sheets())

        embed = discord.Embed(
            title="🔴 Miembro despedido",
            color=discord.Color.red(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="👤 Discord",    value=usuario.mention,          inline=True)
        embed.add_field(name="🎭 Nombre IC",  value=info.get("nombre_ic", "—"), inline=True)
        embed.add_field(name="🏷️ Rango",      value=info.get("rango", "—"),    inline=True)
        if motivo:
            embed.add_field(name="📝 Motivo", value=motivo, inline=False)
        embed.set_footer(text=f"Despedido por {interaction.user.name}")

        await interaction.followup.send(embed=embed, ephemeral=False)
        await log_accion(
            interaction.user, "Despidió miembro",
            f"{usuario.mention} ({usuario.id}) | IC: {info.get('nombre_ic')} | Motivo: {motivo or 'N/A'}",
            discord.Color.red(), "🔴",
        )

    # ── /ver_plantilla ────────────────────────────────────────
    @tree.command(
        name="ver_plantilla",
        description="Ver la plantilla activa de Vlone X Ballas",
    )
    async def ver_plantilla(interaction: discord.Interaction):
        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        activos = get_plantilla_activa()
        orden_rangos = {
            "Purple Ghost": 0,
            "Purple Curse": 1,
            "Purple Soul": 2,
            "Purple Demon": 3,
            "Purple Venom": 4,
            "Baby Purple": 5,
        }
        ordenados = sorted(
            activos.items(),
            key=lambda x: (orden_rangos.get(x[1].get("rango", "Baby Purple"), 99), x[1].get("nombre_ic", ""))
        )

        embed = discord.Embed(
            title="🟣 Plantilla — Vlone X Ballas",
            description=f"**{len(activos)}** miembros activos",
            color=discord.Color.purple(),
            timestamp=datetime.now(),
        )

        # Agrupar por rango
        grupos: dict[str, list[str]] = {}
        for discord_id, info in ordenados:
            rango = info.get("rango", "Baby Purple")
            linea = f"• {info.get('nombre_ic', '—')} (<@{discord_id}>)"
            grupos.setdefault(rango, []).append(linea)

        for rango, lineas in grupos.items():
            texto = "\n".join(lineas[:15])
            if len(lineas) > 15:
                texto += f"\n… y {len(lineas) - 15} más"
            embed.add_field(name=f"🏷️ {rango} ({len(lineas)})", value=texto, inline=False)

        embed.set_footer(text=f"Consultado por {interaction.user.name}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /perfil_asistencia ────────────────────────────────────
    @tree.command(
        name="perfil_asistencia",
        description="Ver el historial de asistencia de un miembro",
    )
    @app_commands.describe(usuario="Miembro a consultar (opcional, por defecto vos mismo)")
    async def perfil_asistencia(
        interaction: discord.Interaction,
        usuario: Optional[discord.Member] = None,
    ):
        from sheets import get_historial_miembro

        target = usuario or interaction.user
        await interaction.response.defer(ephemeral=True)

        info     = get_info_miembro(target.id)
        historial = await get_historial_miembro(target.id, ultimos_n=10)

        embed = discord.Embed(
            title=f"📋 Perfil — {info.get('nombre_ic', target.display_name)}",
            description=(
                f"**Discord:** {target.mention}\n"
                f"**Rango:** {info.get('rango', '—')}\n"
                f"**Últimos {len(historial)} operativos:**"
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.now(),
        )

        if not historial:
            embed.add_field(name="ℹ️", value="Sin registros de asistencia todavía.", inline=False)
        else:
            lineas = []
            for reg in historial:
                estado = reg.get("Estado", "—")
                fecha  = reg.get("Fecha", "—")
                evento = reg.get("Evento", "—")
                lineas.append(f"`{fecha}` **{evento}** → {estado}")
            embed.add_field(name="Historial", value="\n".join(lineas), inline=False)

            # Estadísticas rápidas
            total    = len(historial)
            asistio  = sum(1 for r in historial if "ASISTIÓ" in r.get("Estado", ""))
            embed.add_field(
                name="📊 Stats (últimos registros)",
                value=f"Asistencia: **{asistio}/{total}** ({asistio/total*100:.0f}%)" if total else "—",
                inline=False,
            )

        embed.set_footer(text=f"Consultado por {interaction.user.name}")
        await interaction.followup.send(embed=embed, ephemeral=True)
