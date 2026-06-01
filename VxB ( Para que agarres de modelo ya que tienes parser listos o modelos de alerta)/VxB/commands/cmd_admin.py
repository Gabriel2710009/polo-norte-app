import logging
import re
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands

import state
from config import CLIPS_ADMIN_PANEL_CHANNEL_ID, CLIPS_PANEL_CHANNEL_ID, DEVELOPER_ROLE_ID, DEVELOPER_USER_IDS
from database import (
    get_db_connection,
    get_clip_panel_config,
    get_clip_admin_panel_config,
    set_clip_panel_config,
    set_clip_admin_panel_config,
)
from utils import es_armero_o_alto_cargo, es_armero

logger = logging.getLogger("ArmamentBot")


def register(tree: app_commands.CommandTree):

    # ── /retiros_pendientes ───────────────────────────────────
    @tree.command(name="retiros_pendientes", description="Ver retiros pendientes de validación")
    async def retiros_pendientes(interaction: discord.Interaction):
        from views.retiros_view import SeleccionarRetiroView
        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            conn   = get_db_connection()
            cursor = conn.cursor()

            if state.OBJETOS_ALERTAR:
                cursor.execute("""
                    SELECT id, discord_id, nombre, objeto, cantidad, almacen, timestamp
                    FROM registros_armas
                    WHERE tipo           = 'RETIRO'
                      AND validado       = FALSE
                      AND COALESCE(no_validado, FALSE) = FALSE
                      AND COALESCE(devuelto, FALSE)    = FALSE
                      AND objeto         = ANY(%s)
                    ORDER BY timestamp DESC
                    LIMIT 100
                """, (list(state.OBJETOS_ALERTAR),))
            else:
                cursor.execute("""
                    SELECT id, discord_id, nombre, objeto, cantidad, almacen, timestamp
                    FROM registros_armas
                    WHERE tipo           = 'RETIRO'
                      AND validado       = FALSE
                      AND COALESCE(no_validado, FALSE) = FALSE
                      AND COALESCE(devuelto, FALSE)    = FALSE
                    ORDER BY timestamp DESC
                    LIMIT 100
                """)
            retiros = cursor.fetchall()
            cursor.close()
            conn.close()

            if not retiros:
                await interaction.followup.send("✅ No hay retiros pendientes.", ephemeral=True)
                return

            config_info = (
                f"\nℹ️ *Mostrando solo: {len(state.OBJETOS_ALERTAR)} objetos configurados*"
                if state.OBJETOS_ALERTAR
                else "\nℹ️ *Mostrando todos los objetos*"
            )
            await interaction.followup.send(
                content=f"📤 **Retiros pendientes ({len(retiros)})**{config_info}",
                view=SeleccionarRetiroView(retiros),
                ephemeral=True,
            )
        except Exception as e:
            logger.error(f"❌ Error en /retiros_pendientes: {e}", exc_info=True)
            await interaction.followup.send("❌ Error consultando.", ephemeral=True)

    # ── /sync ─────────────────────────────────────────────────
    @tree.command(name="sync", description="Sincronizar comandos slash con Discord")
    async def sync(interaction: discord.Interaction):
        role_ids = {r.id for r in interaction.user.roles}
        if DEVELOPER_ROLE_ID not in role_ids and interaction.user.id not in DEVELOPER_USER_IDS:
            await interaction.response.send_message("⛔ Solo developers.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            synced = await interaction.client.tree.sync()
            await interaction.followup.send(f"✅ Sincronizados {len(synced)} comandos.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    # ── /sincronizar_historial_texto ──────────────────────────
    @tree.command(name="sincronizar_historial_texto", description="Sincronizar alertas desde historial")
    @app_commands.describe(
        limite="Cantidad de alertas a revisar (max 200)",
        message_id="Sincronizar por message_id específico",
        registro_id="Sincronizar por registro_id específico",
    )
    async def sincronizar_historial_texto(
        interaction: discord.Interaction,
        limite: Optional[int] = 50,
        message_id: Optional[str] = None,
        registro_id: Optional[int] = None,
    ):
        from alertas import (
            sincronizar_alertas_limite,
            sincronizar_por_message_id,
            sincronizar_por_registro_id,
        )
        if not es_armero(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        if message_id:
            resultado = await sincronizar_por_message_id(message_id.strip())
        elif registro_id:
            resultado = await sincronizar_por_registro_id(int(registro_id))
        else:
            limite = max(1, min(200, int(limite or 50)))
            resultado = await sincronizar_alertas_limite(limite)

        await interaction.followup.send(resultado, ephemeral=True)

    # ── /setup_clips ──────────────────────────────────────────
    @tree.command(name="setup_clips", description="Crear o restaurar panel de clips")
    async def setup_clips(interaction: discord.Interaction):
        from views.clips_view import ClipChannelView
        role_ids = {r.id for r in interaction.user.roles}
        if DEVELOPER_ROLE_ID not in role_ids and interaction.user.id not in DEVELOPER_USER_IDS:
            await interaction.response.send_message("⛔ Solo developers.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        channel = interaction.guild.get_channel(CLIPS_PANEL_CHANNEL_ID) if interaction.guild else None
        if not channel:
            await interaction.followup.send("❌ Canal de panel no encontrado.", ephemeral=True)
            return

        old_config = get_clip_panel_config()
        if old_config:
            try:
                old_msg = await channel.fetch_message(int(old_config["panel_message_id"]))
                await old_msg.delete()
            except Exception:
                pass

        embed = discord.Embed(
            title="🎬 Panel de Clips",
            description=(
                "Aquí podés gestionar tu canal de clips.\n\n"
                "Paso 1: tocá **Crear mi canal de clips**.\n"
                "Paso 2: si querés, escribí un emoji opcional.\n"
                "Paso 3: el bot crea tu canal en la categoría correcta con los permisos listos."
            ),
            color=discord.Color.blue(),
        )
        msg = await channel.send(embed=embed, view=ClipChannelView())
        set_clip_panel_config(channel.id, msg.id)
        await interaction.followup.send(f"✅ Panel creado en {channel.mention}.", ephemeral=True)

    # ── /setup_clips_admin ────────────────────────────────────
    @tree.command(name="setup_clips_admin", description="Crear o restaurar panel admin de clips")
    async def setup_clips_admin(interaction: discord.Interaction):
        from views.clips_view import ClipAdminPanelView
        role_ids = {r.id for r in interaction.user.roles}
        if DEVELOPER_ROLE_ID not in role_ids and interaction.user.id not in DEVELOPER_USER_IDS:
            await interaction.response.send_message("⛔ Solo developers.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        channel = interaction.guild.get_channel(CLIPS_ADMIN_PANEL_CHANNEL_ID) if interaction.guild else None
        if not channel:
            await interaction.followup.send("❌ Canal de panel admin no encontrado.", ephemeral=True)
            return

        old_config = get_clip_admin_panel_config()
        if old_config:
            try:
                old_msg = await channel.fetch_message(int(old_config["panel_message_id"]))
                await old_msg.delete()
            except Exception:
                pass

        embed = discord.Embed(
            title="🛠️ Panel Admin de Clips",
            description="Usá los botones para gestionar los canales de clips.",
            color=discord.Color.red(),
        )
        msg = await channel.send(embed=embed, view=ClipAdminPanelView())
        set_clip_admin_panel_config(channel.id, msg.id)
        await interaction.followup.send(f"✅ Panel admin creado en {channel.mention}.", ephemeral=True)

    # ── /setup_voice_admin ────────────────────────────────────
    @tree.command(name="setup_voice_admin", description="Crear o restaurar panel admin de canales de voz")
    async def setup_voice_admin(interaction: discord.Interaction):
        from views.voice_view import VoiceAdminPanelView
        from database import get_voice_admin_panel_config, set_voice_admin_panel_config
        from config import VOICE_ADMIN_PANEL_CHANNEL_ID
        role_ids = {r.id for r in interaction.user.roles}
        if DEVELOPER_ROLE_ID not in role_ids and interaction.user.id not in DEVELOPER_USER_IDS:
            await interaction.response.send_message("⛔ Solo developers.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        channel = interaction.guild.get_channel(VOICE_ADMIN_PANEL_CHANNEL_ID) if interaction.guild else None
        if not channel:
            await interaction.followup.send("❌ Canal del panel de voz no encontrado.", ephemeral=True)
            return

        old_config = get_voice_admin_panel_config()
        if old_config:
            try:
                old_msg = await channel.fetch_message(int(old_config["panel_message_id"]))
                await old_msg.delete()
            except Exception:
                pass

        embed = discord.Embed(
            title="🎙️ Panel Admin — Canales de Voz",
            description=(
                "Gestioná los canales de voz privados del servidor.\n\n"
                "**Para admins:** crear, borrar, ocultar/desocultar y renombrar canales.\n"
                "**Para usuarios con canal:** usá **Gestionar mi acceso** para invitar o expulsar personas."
            ),
            color=discord.Color.purple(),
        )
        embed.add_field(
            name="ℹ️ ¿Cómo funciona?",
            value=(
                "• Cada canal de voz tiene un **rol exclusivo**.\n"
                "• Al crear el canal, el dueño recibe el rol automáticamente.\n"
                "• El dueño puede dar/quitar el rol a otros usando **Gestionar mi acceso**.\n"
                "• Solo quienes tienen el rol pueden ver y entrar al canal."
            ),
            inline=False,
        )
        msg = await channel.send(embed=embed, view=VoiceAdminPanelView())
        set_voice_admin_panel_config(channel.id, msg.id)
        await interaction.followup.send(f"✅ Panel de voz creado en {channel.mention}.", ephemeral=True)
