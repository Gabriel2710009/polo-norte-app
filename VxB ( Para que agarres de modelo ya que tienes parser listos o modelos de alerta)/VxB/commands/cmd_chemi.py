"""
commands/cmd_chemi.py - Comandos de gestion del armario chemi.
"""

import logging
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands

import state
from utils import es_armero_o_alto_cargo
from chemi import ChemiCreditosView 

logger = logging.getLogger("ArmamentBot")


def register(tree: app_commands.CommandTree):
    @tree.command(name="chemi_activar", description="Activar el sistema de armario chemi")
    async def chemi_activar(interaction: discord.Interaction):
        from chemi import activar_chemi, chemi_activo
        from database import guardar_config_chemi_db
        from log_actions import log_accion

        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        activar_chemi(interaction.user.name)
        guardar_config_chemi_db(True, interaction.user.name, datetime.now())

        embed = discord.Embed(
            title="🟢 Chemi activado",
            description="El sistema de armario chemi quedó activo otra vez.",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="Estado", value="✅ Activo", inline=True)
        embed.add_field(name="Activado por", value=interaction.user.mention, inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)
        await log_accion(
            interaction.user,
            "Activó chemi",
            f"Estado actual: {'activo' if chemi_activo() else 'inactivo'}",
            discord.Color.green(),
            "🟢",
        )

    @tree.command(name="chemi_desactivar", description="Desactivar el sistema de armario chemi")
    async def chemi_desactivar(interaction: discord.Interaction):
        from chemi import desactivar_chemi, chemi_activo
        from database import guardar_config_chemi_db
        from log_actions import log_accion

        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        desactivar_chemi(interaction.user.name)
        guardar_config_chemi_db(False, interaction.user.name, datetime.now())

        embed = discord.Embed(
            title="🔴 Chemi desactivado",
            description="El sistema de armario chemi quedó apagado. Los retiros y depósitos chemi se ignorarán hasta reactivarlo.",
            color=discord.Color.red(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="Estado", value="❌ Desactivado", inline=True)
        embed.add_field(name="Desactivado por", value=interaction.user.mention, inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)
        await log_accion(
            interaction.user,
            "Desactivó chemi",
            f"Estado actual: {'activo' if chemi_activo() else 'inactivo'}",
            discord.Color.red(),
            "🔴",
        )

    @tree.command(name="chemi_estado", description="Ver estado general del sistema de armario chemi")
    async def chemi_estado(interaction: discord.Interaction):
        from chemi import (
            CHEMI_ALMACEN_NOMBRE,
            CHEMI_AVISO_CHANNEL_ID,
            ALTOS_CARGOS_CHANNEL_ID,
            DEUDA_CHEMI_ROLE_ID,
            LIMITE_PISTOLAS_DIA,
            _stats_creditos_pendientes,
            chemi_activo,
        )
        from database import get_db_connection

        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE contador > 0) AS activos,
                    COUNT(*) FILTER (WHERE contador >= %s) AS bloqueados,
                    COALESCE(SUM(contador), 0) AS total_contador
                FROM chemi_contadores
                """,
                (LIMITE_PISTOLAS_DIA,),
            )
            row_stats = cursor.fetchone() or {}
            cursor.execute(
                """
                SELECT COUNT(*) AS total, COALESCE(SUM(cantidad), 0) AS pistolas
                FROM registros_armas
                WHERE tipo = 'RETIRO' AND almacen = %s AND timestamp >= %s
                """,
                (CHEMI_ALMACEN_NOMBRE, datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)),
            )
            row_hoy = cursor.fetchone()
            cursor.close()
            conn.close()
            creditos_pendientes, pipas_credito = _stats_creditos_pendientes()

            embed = discord.Embed(
                title="🧪 Estado - Sistema Armario Chemi",
                color=discord.Color.teal(),
                timestamp=datetime.now(),
            )
            embed.add_field(name="Estado", value="✅ Activo" if chemi_activo() else "❌ Desactivado", inline=True)
            embed.add_field(name="🏠 Almacén", value=f"`{CHEMI_ALMACEN_NOMBRE}`", inline=False)
            embed.add_field(name="🔴 Rol deuda", value=f"<@&{DEUDA_CHEMI_ROLE_ID}>" if DEUDA_CHEMI_ROLE_ID else "N/D", inline=True)
            embed.add_field(name="🔫 Límite/día", value=f"{LIMITE_PISTOLAS_DIA} pistolas", inline=True)
            embed.add_field(name="📣 Canal avisos", value=f"<#{CHEMI_AVISO_CHANNEL_ID}>", inline=True)
            embed.add_field(name="🚨 Canal altos cargos", value=f"<#{ALTOS_CARGOS_CHANNEL_ID}>", inline=True)
            embed.add_field(name="👥 Contadores activos", value=str(int(row_stats.get("activos") or 0)), inline=True)
            embed.add_field(name="🔒 Bloqueados", value=str(int(row_stats.get("bloqueados") or 0)), inline=True)
            embed.add_field(name="🎟️ Créditos pendientes", value=f"{creditos_pendientes} créditos | {pipas_credito} pipas", inline=True)
            if row_hoy:
                embed.add_field(
                    name="📊 Retiros hoy",
                    value=f"{row_hoy['total']} retiros | {row_hoy['pistolas']} pistolas",
                    inline=False,
                )
            embed.set_footer(text=f"Consultado por {interaction.user.name}")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"❌ Error en /chemi_estado: {e}", exc_info=True)
            await interaction.followup.send("❌ Error consultando estado.", ephemeral=True)

    @tree.command(name="chemi_deuda_ver", description="Ver deudas activas del armario chemi")
    @app_commands.describe(usuario="Usuario específico (opcional, si no se muestra todos)")
    async def chemi_deuda_ver(
        interaction: discord.Interaction,
        usuario: Optional[discord.Member] = None,
    ):
        from database import get_db_connection

        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            if usuario:
                cursor.execute(
                    """
                    SELECT discord_id, nombre, contador, updated_at
                    FROM chemi_contadores
                    WHERE discord_id = %s AND contador >= 3
                    """,
                    (str(usuario.id),),
                )
            else:
                cursor.execute(
                    """
                    SELECT discord_id, nombre, contador, updated_at
                    FROM chemi_contadores
                    WHERE contador >= 3
                    ORDER BY contador DESC, updated_at DESC
                    LIMIT 20
                    """
                )
            deudas = cursor.fetchall() or []
            cursor.close()
            conn.close()

            if not deudas:
                await interaction.followup.send(
                    "✅ No hay deudas activas." if not usuario else f"✅ {usuario.mention} no tiene deudas activas.",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title=f"🔴 Deudas activas - Armario Chemi ({len(deudas)})",
                color=discord.Color.red(),
                timestamp=datetime.now(),
            )

            for deuda in deudas:
                updated = deuda.get("updated_at")
                updated_txt = updated.strftime("%d/%m/%Y %H:%M") if updated else "N/A"
                embed.add_field(
                    name=f"👤 {deuda['nombre']} (<@{deuda['discord_id']}>)",
                    value=(
                        f"📊 Contador: **{deuda['contador']}/3**\n"
                        f"🕐 Actualizado: **{updated_txt}**\n"
                        f"🔴 Rol esperado: asignado"
                    ),
                    inline=False,
                )

            embed.set_footer(text=f"Consultado por {interaction.user.name}")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"❌ Error en /chemi_deuda_ver: {e}", exc_info=True)
            await interaction.followup.send("❌ Error consultando deudas.", ephemeral=True)

    @tree.command(name="chemi_deuda_saldar", description="Saldar manualmente la deuda chemi de un usuario")
    @app_commands.describe(
        usuario="Usuario a quien saldar la deuda",
        motivo="Motivo del saldo manual (opcional)",
    )
    async def chemi_deuda_saldar(
        interaction: discord.Interaction,
        usuario: discord.Member,
        motivo: Optional[str] = None,
    ):
        from chemi import _cancelar_deuda_db, _quitar_rol_deuda, _set_contador_chemi
        from log_actions import log_accion

        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        _set_contador_chemi(str(usuario.id), usuario.display_name, 0)
        _cancelar_deuda_db(str(usuario.id))
        await _quitar_rol_deuda(interaction.guild, usuario.id)

        embed = discord.Embed(
            title="✅ Deuda saldada manualmente - Chemi",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="👤 Usuario", value=usuario.mention, inline=True)
        embed.add_field(name="✅ Por", value=interaction.user.mention, inline=True)
        if motivo:
            embed.add_field(name="📝 Motivo", value=motivo, inline=False)
        embed.set_footer(text=f"Saldado por {interaction.user.name}")

        await interaction.followup.send(embed=embed, ephemeral=False)
        await log_accion(
            interaction.user,
            "Saldó deuda chemi manualmente",
            f"{usuario.mention} ({usuario.id})" + (f" | Motivo: {motivo}" if motivo else ""),
            discord.Color.green(),
            "✅",
        )

    @tree.command(name="chemi_limite_ver", description="Ver el uso del límite diario de pistolas chemi de un usuario")
    @app_commands.describe(usuario="Usuario a consultar (opcional, por defecto tú mismo)")
    async def chemi_limite_ver(
        interaction: discord.Interaction,
        usuario: Optional[discord.Member] = None,
    ):
        from chemi import LIMITE_PISTOLAS_DIA, _listar_creditos_db, _pistolas_retiradas_hoy, tiene_deuda_chemi

        if usuario and usuario.id != interaction.user.id and not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        target = usuario or interaction.user
        await interaction.response.defer(ephemeral=True)

        pistolas_hoy = _pistolas_retiradas_hoy(str(target.id))
        creditos = _listar_creditos_db(str(target.id))
        total_creditos = sum(int(c.get("cantidad_restante") or 0) for c in creditos)

        barra = "🟩" * min(pistolas_hoy, LIMITE_PISTOLAS_DIA) + "⬛" * max(0, LIMITE_PISTOLAS_DIA - pistolas_hoy)
        estado_limite = "✅ Disponible" if pistolas_hoy < LIMITE_PISTOLAS_DIA else "🔴 Límite alcanzado"
        tiene_rol = hasattr(target, "roles") and tiene_deuda_chemi(target)

        embed = discord.Embed(
            title=f"🔫 Límite diario chemi - {getattr(target, 'display_name', str(target))}",
            color=discord.Color.teal(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="📊 Uso hoy", value=f"{barra} `{pistolas_hoy}/{LIMITE_PISTOLAS_DIA}`", inline=False)
        embed.add_field(name="🔒 Estado límite", value=estado_limite, inline=True)
        embed.add_field(name="🔴 Rol deuda", value="Sí" if tiene_rol else "No", inline=True)
        embed.add_field(name="🎟️ Créditos pendientes", value=f"{total_creditos} pipa(s)", inline=True)
        embed.set_footer(text=f"Consultado por {interaction.user.name}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @tree.command(name="chemi_limite_reset", description="Resetear el límite diario de pistolas chemi de un usuario")
    @app_commands.describe(
        usuario="Usuario al que resetear el límite",
        motivo="Motivo del reset (opcional)",
    )
    async def chemi_limite_reset(
        interaction: discord.Interaction,
        usuario: discord.Member,
        motivo: Optional[str] = None,
    ):
        from chemi import _cancelar_deuda_db, _quitar_rol_deuda, _set_contador_chemi
        from database import get_db_connection
        from log_actions import log_accion

        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO chemi_limite_resets (discord_id, reseteado_por, motivo, reset_at)
                VALUES (%s, %s, %s, NOW())
                """,
                (str(usuario.id), interaction.user.name, motivo or ""),
            )
            conn.commit()
            cursor.close()
            conn.close()
            _set_contador_chemi(str(usuario.id), usuario.display_name, 0)
            _cancelar_deuda_db(str(usuario.id))
            await _quitar_rol_deuda(interaction.guild, usuario.id)

            embed = discord.Embed(
                title="🔄 Límite diario reseteado - Chemi",
                color=discord.Color.green(),
                timestamp=datetime.now(),
            )
            embed.add_field(name="👤 Usuario", value=usuario.mention, inline=True)
            embed.add_field(name="🔄 Por", value=interaction.user.mention, inline=True)
            if motivo:
                embed.add_field(name="📝 Motivo", value=motivo, inline=False)
            embed.set_footer(text=f"Reseteado por {interaction.user.name}")
            await interaction.followup.send(embed=embed, ephemeral=False)

            await log_accion(
                interaction.user,
                "Reseteó límite diario chemi",
                f"{usuario.mention} ({usuario.id})" + (f" | Motivo: {motivo}" if motivo else ""),
                discord.Color.blue(),
                "🔄",
            )
        except Exception as e:
            logger.error(f"❌ Error en /chemi_limite_reset: {e}", exc_info=True)
            await interaction.followup.send("❌ Error reseteando límite.", ephemeral=True)

    @tree.command(name="chemi_creditos", description="Ver tus créditos del armario Chemi y usarlos")
    @app_commands.describe(usuario="Usuario a consultar (opcional, solo armeros/altos cargos)")
    async def chemi_creditos(
        interaction: discord.Interaction,
        usuario: Optional[discord.Member] = None,
    ):
        from chemi import _listar_creditos_db, _get_contador_chemi, ChemiCreditosView

        if usuario and usuario.id != interaction.user.id and not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message(
                "⛔ Sin permiso para ver créditos de otro usuario.", ephemeral=True
            )
            return

        target = usuario or interaction.user
        await interaction.response.defer(ephemeral=True)

        creditos = _listar_creditos_db(str(target.id))
        contador = _get_contador_chemi(str(target.id))
        total_pipas = sum(int(c.get("cantidad_restante") or 0) for c in creditos)

        embed = discord.Embed(
            title=f"🎟️ Créditos Chemi — {target.display_name}",
            color=discord.Color.blurple(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="📊 Contador actual", value=f"`{contador}/3`", inline=True)
        embed.add_field(
            name="💰 Total disponible",
            value=f"**{total_pipas}** pipa(s) en {len(creditos)} crédito(s)" if creditos else "Sin créditos",
            inline=True,
        )

        if not creditos:
            embed.description = "No tenés créditos pendientes en el armario Chemi."
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        lineas = []
        for c in creditos[:8]:
            created = c.get("created_at")
            fecha = created.strftime("%d/%m %H:%M") if created else "N/A"
            lineas.append(f"**#{c['id']}** · `{c['cantidad_restante']}/{c['cantidad_total']}` pipas · {fecha}")
        embed.add_field(name="📋 Detalle", value="\n".join(lineas), inline=False)
        embed.set_footer(text="Usá los botones para aplicar el crédito")

        primer_credito = creditos[0]
        # Solo mostrar botones si es el propio usuario
        if target.id == interaction.user.id:
            view = ChemiCreditosView(
                credito_id=int(primer_credito["id"]),
                owner_id=int(target.id),
                restante=int(primer_credito.get("cantidad_restante") or 0),
            )
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    @tree.command(name="chemi_panel_setup", description="Crear o restaurar el panel fijado de límite Chemi")
    async def chemi_panel_setup(interaction: discord.Interaction):
        from chemi import CHEMI_AVISO_CHANNEL_ID, ChemiLimitPanelView
        from database import get_chemi_panel_config, set_chemi_panel_config
        from log_actions import log_accion

        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        channel = interaction.client.get_channel(CHEMI_AVISO_CHANNEL_ID)
        if not channel:
            await interaction.followup.send("❌ Canal de avisos Chemi no encontrado.", ephemeral=True)
            return

        old_config = get_chemi_panel_config()
        if old_config and old_config.get("panel_message_id"):
            try:
                old_msg = await channel.fetch_message(int(old_config["panel_message_id"]))
                await old_msg.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        embed = discord.Embed(
            title="🧪 Panel Chemi",
            description="Usá el botón para ver tu límite y créditos pendientes.",
            color=discord.Color.teal(),
            timestamp=datetime.now(),
        )
        embed.set_footer(text="Sistema Chemi - ArmamentBot")
        msg = await channel.send(embed=embed, view=ChemiLimitPanelView())
        try:
            await msg.pin(reason=f"Panel Chemi creado por {interaction.user}")
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"⚠️ No se pudo fijar panel Chemi: {e}")

        set_chemi_panel_config(channel.id, msg.id)
        await interaction.followup.send(f"✅ Panel Chemi creado en {channel.mention}.", ephemeral=True)
        await log_accion(
            interaction.user,
            "Creó panel Chemi",
            f"Canal: {channel.mention} | Mensaje: `{msg.id}`",
            discord.Color.teal(),
            "🧪",
        )
    
    @tree.command(name="chemi_credito_dar", description="[DEV] Generar crédito Chemi manual a un usuario")
    @app_commands.describe(
        usuario="Usuario al que dar el crédito",
        cantidad="Cantidad de pipas a acreditar",
    )
    async def chemi_credito_dar(
        interaction: discord.Interaction,
        usuario: discord.Member,
        cantidad: int,
    ):
        from chemi import _enviar_credito_deposito
        from config import DEVELOPER_USER_IDS

        if interaction.user.id not in DEVELOPER_USER_IDS and not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        if cantidad <= 0:
            await interaction.response.send_message("❌ La cantidad debe ser mayor a 0.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        await _enviar_credito_deposito(usuario.id, usuario.display_name, cantidad)

        embed = discord.Embed(
            title="🎟️ Crédito Chemi generado manualmente",
            color=discord.Color.blurple(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="👤 Usuario", value=usuario.mention, inline=True)
        embed.add_field(name="🔫 Pipas", value=str(cantidad), inline=True)
        embed.add_field(name="✅ Por", value=interaction.user.mention, inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

def _register_chemi_creditos(tree):
    @tree.command(
        name="chemi_creditos",
        description="Muestra tus créditos del armario Chemi y te permite usarlos.",
    )
    async def chemi_creditos(interaction: discord.Interaction):
        from chemi import _listar_creditos_db, ChemiCreditosView, _get_contador_chemi
 
        discord_id = str(interaction.user.id)
        creditos   = _listar_creditos_db(discord_id)
        contador   = _get_contador_chemi(discord_id)
 
        if not creditos:
            await interaction.response.send_message(
                "ℹ️ No tenés créditos pendientes en el armario Chemi.",
                ephemeral=True,
            )
            return
 
        # Construir embed resumen
        embed = discord.Embed(
            title="🎟️ Tus créditos — Armario Chemi",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="📊 Tu contador actual",
            value=f"`{contador}/{3}`",   # LIMITE_PISTOLAS_DIA = 3
            inline=False,
        )
 
        total_pipas = sum(int(c.get("cantidad_restante") or 0) for c in creditos)
        embed.add_field(
            name="💰 Total disponible",
            value=f"**{total_pipas}** pipa(s) en {len(creditos)} crédito(s)",
            inline=False,
        )
 
        lineas = []
        for c in creditos[:8]:
            created = c.get("created_at")
            fecha   = created.strftime("%d/%m %H:%M") if created else "N/A"
            lineas.append(
                f"**#{c['id']}** · `{c['cantidad_restante']}/{c['cantidad_total']}` pipas · {fecha}"
            )
        embed.add_field(name="📋 Detalle", value="\n".join(lineas), inline=False)
        embed.set_footer(text="Usá los botones para descontar del contador")
 
        # Usar el primer crédito con saldo (el más reciente)
        primer_credito = creditos[0]
        view = ChemiCreditosView(
            credito_id=int(primer_credito["id"]),
            owner_id=int(interaction.user.id),
            restante=int(primer_credito.get("cantidad_restante") or 0),
        )
 
        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True,
        )

    