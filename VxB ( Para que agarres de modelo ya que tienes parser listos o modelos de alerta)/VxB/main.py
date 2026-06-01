import asyncio
import logging
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import logger_setup  # noqa: F401

import state
from config import (
    ALTO_CARGO_ROLE_ID,
    ARMERO_ROLE_ID,
    LOGS_CHANNEL_ID,
    BOT_LOGS_CHANNEL_ID,
    PLANTILLA_AUTOMATICA_CHANNEL_ID,
    RAZON_RETIRO_CHANNEL_ID,
    JUSTIFICACION_CHANNEL_ID,
)
from database import (
    inicializar_base_datos,
    cargar_config_alertas,
    cargar_config_chemi_db,
    cargar_estado_operativo_db,
    get_clip_panel_config,
    get_clip_admin_panel_config,
    get_voice_admin_panel_config,
    set_voice_admin_panel_config,
)
from antirrobo import cargar_config_antirrobo, evaluar_antirrobo
from alertas import enviar_alerta_retiro, reactivar_botones_alertas, set_bot as alertas_set_bot, DevolucionConfirmView, obtener_miembro_seguro
from antirrobo import set_bot as antirrobo_set_bot
from chemi import (
    set_bot as chemi_set_bot,
    restaurar_deudas_activas,
    evaluar_retiro_chemi,
    evaluar_deposito_chemi,
    es_retiro_chemi,
    es_deposito_chemi,
    CHEMI_ALMACEN_NOMBRE,
    ChemiCreditoView,
    ChemiLimitPanelView,
    ChemiCreditosView,
)
from justificaciones import set_bot as justificaciones_set_bot, manejar_mensaje_justificacion
from eventos_discord import manejar_mensaje_evento
from operativo import restaurar_operativo_desde_db, set_bot as operativo_set_bot, VerificacionOperativoView
from log_actions import set_bot as log_set_bot
from parser import parsear_embed_arma, parsear_mensaje_texto_libre, cargar_historial_canal
from licencia import verificar_licencia
from sheets_plantilla import manejar_mensaje_plantilla_automatica, revisar_cambios_doc

from asistencia import (
    set_bot as asistencia_set_bot,
    autoiniciar_operativo_por_retiro,
    on_weapon_withdraw,
    get_sesiones_activas,
    iniciar_programador_asistencia_semanal,
    cargar_config_asistencia_semanal,
    restaurar_operativos_programados_desde_db,
)
from commands.cmd_asistencia import set_ultimo_resultado
import commands.cmd_asistencia as cmd_asistencia

from views.validar_view import ValidarView, RazonRetiroView
from views.clips_view import ClipChannelView, ClipAdminPanelView
from views.voice_view import VoiceAdminPanelView

import commands.cmd_stats      as cmd_stats
import commands.cmd_operativo  as cmd_operativo
import commands.cmd_alertas    as cmd_alertas
import commands.cmd_admin      as cmd_admin
import commands.cmd_misc       as cmd_misc
import commands.cmd_chemi      as cmd_chemi

logger = logging.getLogger("ArmamentBot")

# ─── BOT ──────────────────────────────────────────────────────
intents                  = discord.Intents.default()
intents.message_content  = True
intents.members          = True
intents.guilds           = True

bot = commands.Bot(command_prefix="!", intents=intents)
state.BOT = bot

_commands_registered = False
_views_registered = False

alertas_set_bot(bot)
antirrobo_set_bot(bot)
chemi_set_bot(bot)
justificaciones_set_bot(bot)
operativo_set_bot(bot)
log_set_bot(bot)
asistencia_set_bot(bot)


def _register_all_commands():
    global _commands_registered
    if _commands_registered:
        return
    cmd_stats.register(bot.tree)
    cmd_operativo.register(bot.tree)
    cmd_alertas.register(bot.tree)
    cmd_admin.register(bot.tree)
    cmd_misc.register(bot.tree)
    cmd_asistencia.register(bot.tree)
    cmd_chemi.register(bot.tree)
    _commands_registered = True


def _register_persistent_views():
    global _views_registered
    if _views_registered:
        return
    bot.add_view(ValidarView(0))
    bot.add_view(RazonRetiroView(0, 0, 0))
    bot.add_view(VerificacionOperativoView())
    bot.add_view(ClipChannelView())
    bot.add_view(ClipAdminPanelView())
    bot.add_view(DevolucionConfirmView(0, None, None, 1))
    bot.add_view(VoiceAdminPanelView())
    bot.add_view(ChemiCreditoView())
    bot.add_view(ChemiLimitPanelView())
    bot.add_view(ChemiCreditosView(0, 0, 0))
    _views_registered = True


@tasks.loop(minutes=10)
async def vigilar_plantilla_manual():
    try:
        resultado = await revisar_cambios_doc()
        if not resultado.get("ok") or not resultado.get("changed"):
            return

        channel = bot.get_channel(BOT_LOGS_CHANNEL_ID)
        if channel:
            embed = discord.Embed(
                title="📝 Cambio manual detectado en plantilla",
                description=(
                    "El documento de plantilla cambió fuera del bot.\n"
                    "Revisá el Google Doc antes de volver a sincronizar."
                ),
                color=discord.Color.orange(),
                timestamp=datetime.now(),
            )
            if resultado.get("source"):
                embed.add_field(name="Origen", value=resultado["source"], inline=True)
            await channel.send(embed=embed)
            logger.warning("⚠️ Cambio manual detectado en la plantilla")
    except Exception as e:
        logger.debug(f"No se pudo revisar cambios manuales en plantilla: {e}")


@bot.event
async def on_ready():
    if not await verificar_licencia():
        logger.critical("⛔ Cerrando el bot por una falla en la verificación de licencia.")
        await bot.close()
        return
    logger.info(f"✅ Bot conectado como {bot.user} ({bot.user.id})")

    alertas_set_bot(bot)
    antirrobo_set_bot(bot)
    chemi_set_bot(bot)
    justificaciones_set_bot(bot)
    operativo_set_bot(bot)
    log_set_bot(bot)
    asistencia_set_bot(bot)
    cargar_config_asistencia_semanal()
    iniciar_programador_asistencia_semanal()

    import os
    sheet_id = (
        os.getenv("SHEETS_SPREADSHEET_ID")
        or os.getenv("SPREADSHEET_ID")
        or os.getenv("SHEET_ID")
        or ""
    ).strip()
    if sheet_id:
        from sheets import setup_spreadsheet
        bot.loop.create_task(setup_spreadsheet())

    if not vigilar_plantilla_manual.is_running():
        vigilar_plantilla_manual.start()

    logger_setup.discord_log_handler.bot_ready  = True
    logger_setup.discord_log_handler._loop       = bot.loop
    logger_setup.discord_log_handler._bot_ref    = bot

    _register_all_commands()
    _register_persistent_views()

    state.CHEMI_CONFIG.update(cargar_config_chemi_db())
    logger.info(f"🧪 Chemi: {'activo' if state.CHEMI_CONFIG.get('activo', True) else 'inactivo'}")

    activas, objetos = cargar_config_alertas()
    state.ALERTAS_ACTIVAS = activas
    state.OBJETOS_ALERTAR = objetos
    logger.info(f"🔔 Alertas: activas={activas} | objetos={len(objetos)}")

    cargar_config_antirrobo()
    logger.info(f"🛡️ Antirrobo cargado | activo={state.ANTIRROBO_CONFIG['activo']}")

    row = cargar_estado_operativo_db()
    if row and row.get("activo"):
        if not state.operativo_activo["activo"] and not state.operativo_recuperado:
            logger.info("⚙️ Restaurando operativo desde BD...")
            await restaurar_operativo_desde_db(row)
            logger.info("✅ Operativo restaurado")
        else:
            logger.info("ℹ️ Operativo activo detectado en memoria; se omite restauración duplicada")

    try:
        restaurados = await restaurar_operativos_programados_desde_db()
        logger.info(f"📌 Operativos programados restaurados: {restaurados}")
    except Exception as e:
        logger.error(f"❌ Error restaurando operativos programados: {e}", exc_info=True)

    try:
        await restaurar_deudas_activas()
        logger.info("✅ Deudas chemi restauradas")
    except Exception as e:
        logger.error(f"❌ Error restaurando deudas chemi: {e}", exc_info=True)

    try:
        synced = await bot.tree.sync()
        logger.info(f"✅ {len(synced)} comandos sincronizados")
    except Exception as e:
        logger.error(f"❌ Error sincronizando comandos: {e}", exc_info=True)

    await reactivar_botones_alertas()

    bot.loop.create_task(cargar_historial_canal(bot, LOGS_CHANNEL_ID, state.operativo_activo))

    try:
        channel = bot.get_channel(BOT_LOGS_CHANNEL_ID)
        if channel:
            embed = discord.Embed(
                title="🟢 Bot iniciado",
                description=f"Listo y conectado",
                color=discord.Color.green(),
                timestamp=datetime.now(),
            )
            embed.add_field(
                name="📋 Estado",
                value=(
                    f"• Alertas: {'✅ Activas' if state.ALERTAS_ACTIVAS else '❌ Desactivadas'}\n"
                    f"• Antirrobo: {'✅ Activo' if state.ANTIRROBO_CONFIG['activo'] else '❌ Inactivo'}\n"
                    f"• Operativo: {'✅ Activo' if state.operativo_activo['activo'] else '❌ Inactivo'}"
                ),
                inline=False,
            )
            embed.set_footer(text=datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
            await channel.send(embed=embed)
    except Exception as e:
        logger.warning(f"⚠️ No se pudo enviar mensaje de inicio: {e}")

    logger.info("🟢 Bot listo")


@bot.event
async def on_message(message: discord.Message):
    if message.author.id == bot.user.id:
        return

    if message.channel.id == JUSTIFICACION_CHANNEL_ID:
        if not message.webhook_id and not message.author.bot and message.content:
            await manejar_mensaje_justificacion(message)
        return

    if await manejar_mensaje_evento(message):
        return

    if await manejar_mensaje_plantilla_automatica(message):
        return

    # ── Borrar mensajes de texto en canal de razón ────────────
    if message.channel.id == RAZON_RETIRO_CHANNEL_ID:
        # Solo borrar mensajes de usuarios (no bots/webhooks)
        if not message.webhook_id and not message.author.bot:
            # Si es un mensaje de texto (sin embed), borrarlo
            if message.content and not message.embeds:
                try:
                    await message.delete()
                    try:
                        await message.author.send(
                            "⚠️ Tu mensaje fue eliminado del canal de razones de retiro.\n"
                            "Ese canal es solo para que el **sistema** mande los formularios.\n"
                            "Si necesitás cargar una razón, usá el botón que te aparece en el formulario."
                        )
                    except discord.Forbidden:
                        pass
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
                return

    if not message.webhook_id:
        await bot.process_commands(message)

    datos = None

    if message.embeds:
        for embed in message.embeds:
            datos = parsear_embed_arma(embed)
            if datos:
                break

    if not datos and message.content:
        datos = parsear_mensaje_texto_libre(message.content)

    if not datos:
        return

    datos.setdefault("timestamp", datetime.now())
    await autoiniciar_operativo_por_retiro(datos)
    datos["en_operativo"] = state.operativo_activo["activo"]

    from database import guardar_registro
    registro_id = guardar_registro(datos, state.operativo_activo)
    if not registro_id:
        logger.warning("⚠️ No se pudo guardar el registro en BD")
        return

    datos["registro_id"] = registro_id

    if datos.get("tipo") == "RETIRO" and state.operativo_activo["activo"]:
        discord_id_retiro = datos.get("discord_id")
        if discord_id_retiro:
            on_weapon_withdraw(int(discord_id_retiro))

    # ── Actualizar contadores del operativo ───────────────────
    if state.operativo_activo["activo"] and datos.get("tipo") == "RETIRO":
        obj  = datos.get("objeto")
        cant = datos.get("cantidad", 1)
        if obj:
            state.operativo_activo["pistolas_retiros"][obj]   += cant
    elif state.operativo_activo["activo"] and datos.get("tipo") == "DEPOSITO":
        obj  = datos.get("objeto")
        cant = datos.get("cantidad", 1)
        if obj:
            state.operativo_activo["pistolas_depositos"][obj] += cant
            from operativo import actualizar_control_operativo
            bot.loop.create_task(actualizar_control_operativo())

    # ── Borrar mensajes de depósito validados del canal de logs ─
    # (para no saturar el canal de solicitudes; el canal de logs es
    #  distinto al canal de depósito-solicitud; esto aplica a DEPOSITO_SOLICITUD_CHANNEL_ID)
    if datos.get("tipo") == "DEPOSITO":
        # Intentar borrar mensajes de solicitud de devolución asociados a este usuario/objeto
        # que ya estén marcados como "devueltos" → limpiar canal depósito-solicitud
        bot.loop.create_task(_limpiar_mensajes_devolucion_completados())

    # ── Chemi: retiro/deposito del armario para chemis ───────
    if datos.get("tipo") == "RETIRO" and es_retiro_chemi(datos):
        bot.loop.create_task(evaluar_retiro_chemi(datos))
        return

    if datos.get("tipo") == "DEPOSITO" and es_deposito_chemi(datos):
        bot.loop.create_task(evaluar_deposito_chemi(datos))

    # ── Alerta retiro fuera de operativo ──────────────────────
    if datos.get("tipo") == "RETIRO" and not state.operativo_activo["activo"]:
        from database import usuario_en_whitelist_antirrobo
        from utils import es_armero_o_alto_cargo

        discord_id = datos.get("discord_id")
        if discord_id and message.guild:
            try:
                member = await obtener_miembro_seguro(message.guild, int(discord_id))
                if member and not es_armero_o_alto_cargo(member):
                    en_whitelist = usuario_en_whitelist_antirrobo(str(discord_id))
                    if en_whitelist:
                        await enviar_alerta_retiro(
                            datos,
                            mention_armero=False,
                            nota_extra="Usuario en whitelist antirrobo. Se informa el retiro sin tag al armero y exento de evaluación antirrobo.",
                            bypass_filtro_roles=True,
                            exento_whitelist=True,
                        )
                    else:
                        await enviar_alerta_retiro(datos)
                elif member is None:
                    logger.debug(f"No se pudo resolver miembro {discord_id}; se reintenta la alerta sin filtro previo.")
                    await enviar_alerta_retiro(datos)
            except (TypeError, ValueError) as e:
                logger.debug(f"Discord ID inválido para alerta {discord_id}: {e}")
            except Exception as e:
                logger.debug(f"No se pudo obtener miembro {discord_id} para alerta: {e}")

    await evaluar_antirrobo(datos)


def _extraer_ids_scheduled_event(*args, **kwargs) -> tuple[Optional[int], Optional[int]]:
    payload = args[0] if args else None

    event_id = kwargs.get("event_id") or kwargs.get("guild_scheduled_event_id")
    user_id = kwargs.get("user_id")

    if event_id is None and payload is not None:
        event_id = getattr(payload, "guild_scheduled_event_id", None)
        if event_id is None:
            event_id = getattr(payload, "event_id", None)
        if event_id is None:
            event_id = getattr(payload, "id", None)

    if user_id is None and payload is not None:
        user_id = getattr(payload, "user_id", None)
        if user_id is None:
            user = getattr(payload, "user", None)
            user_id = getattr(user, "id", None) if user is not None else None

    if event_id is not None:
        try:
            event_id = int(event_id)
        except (TypeError, ValueError):
            event_id = None

    if user_id is not None:
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            user_id = None

    return event_id, user_id


@bot.event
async def on_guild_scheduled_event_user_add(*args, **kwargs):
    event_id, user_id = _extraer_ids_scheduled_event(*args, **kwargs)
    if event_id is None or user_id is None:
        return
    try:
        from asistencia import registrar_usuario_evento_cache

        registrar_usuario_evento_cache(event_id, user_id)
        logger.info(f"➕ [Eventos] User add cache | event_id={event_id} | user_id={user_id}")
    except Exception as e:
        logger.warning(f"⚠️ [Eventos] No se pudo cachear user add del event {event_id}: {e}")


@bot.event
async def on_guild_scheduled_event_user_remove(*args, **kwargs):
    event_id, user_id = _extraer_ids_scheduled_event(*args, **kwargs)
    if event_id is None or user_id is None:
        return
    try:
        from asistencia import quitar_usuario_evento_cache

        quitar_usuario_evento_cache(event_id, user_id)
        logger.info(f"➖ [Eventos] User remove cache | event_id={event_id} | user_id={user_id}")
    except Exception as e:
        logger.warning(f"⚠️ [Eventos] No se pudo cachear user remove del event {event_id}: {e}")


@bot.event
async def on_error(event_method: str, *args, **kwargs):
    logger.error(f"Error no manejado en evento {event_method}", exc_info=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    logger.error(
        f"Error no manejado en comando slash: {getattr(interaction.command, 'name', 'desconocido')}",
        exc_info=(type(error), error, error.__traceback__),
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send("Error procesando el comando.", ephemeral=True)
        else:
            await interaction.response.send_message("Error procesando el comando.", ephemeral=True)
    except Exception:
        pass


@bot.event
async def on_guild_scheduled_event_delete(event: discord.ScheduledEvent):
    try:
        from asistencia import cancelar_evento_operativo

        await cancelar_evento_operativo(event.id)
        logger.info(f"🗑️ [Eventos] Scheduled event eliminado | id={event.id} | nombre={getattr(event, 'name', 'N/A')}")
    except Exception as e:
        logger.warning(f"⚠️ [Eventos] No se pudo procesar la eliminación del scheduled event {event.id}: {e}")


@bot.event
async def on_guild_scheduled_event_update(before: discord.ScheduledEvent, after: discord.ScheduledEvent):
    status_name = getattr(getattr(after, "status", None), "name", str(getattr(after, "status", ""))).lower()
    if "cancel" not in status_name:
        return
    try:
        from asistencia import cancelar_evento_operativo

        await cancelar_evento_operativo(after.id)
        logger.info(
            f"🗑️ [Eventos] Scheduled event marcado como cancelado | id={after.id} | nombre={getattr(after, 'name', 'N/A')}"
        )
    except Exception as e:
        logger.warning(f"⚠️ [Eventos] No se pudo procesar la cancelación del scheduled event {after.id}: {e}")


async def _limpiar_mensajes_devolucion_completados():
    """
    Borra del canal DEPOSITO_SOLICITUD_CHANNEL los mensajes cuyo registro
    ya fue marcado como devuelto, para no saturar el canal.
    Solo borra hasta 10 por vez para no generar rate limiting.
    """
    from config import DEPOSITO_SOLICITUD_CHANNEL_ID
    try:
        conn   = __import__("database").get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT devolucion_request_message_id, devolucion_request_channel_id
            FROM registros_armas
            WHERE devuelto = TRUE
              AND validado = TRUE
              AND devolucion_request_message_id IS NOT NULL
              AND devolucion_request_channel_id = %s
            ORDER BY fecha_devolucion DESC
            LIMIT 10
        """, (DEPOSITO_SOLICITUD_CHANNEL_ID,))
        rows = cursor.fetchall()

        if rows:
            # Limpiar referencia en BD para no procesar dos veces
            ids = [r["devolucion_request_message_id"] for r in rows]
            cursor.execute(
                "UPDATE registros_armas SET devolucion_request_message_id = NULL WHERE devolucion_request_message_id = ANY(%s)",
                (ids,),
            )
            conn.commit()

        cursor.close()
        conn.close()

        for row in rows:
            msg_id = row.get("devolucion_request_message_id")
            ch_id  = row.get("devolucion_request_channel_id")
            if not msg_id or not ch_id:
                continue
            try:
                ch = bot.get_channel(int(ch_id))
                if ch:
                    msg = await ch.fetch_message(int(msg_id))
                    await msg.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            except Exception as e:
                logger.debug(f"No se pudo borrar msg depósito {msg_id}: {e}")

    except Exception as e:
        logger.debug(f"No se pudo limpiar mensajes depósito: {e}")


@bot.event
async def on_interaction(interaction: discord.Interaction):
    from config import BOT_LOGS_CHANNEL_ID
    from utils import es_armero

    if interaction.type != discord.InteractionType.application_command:
        return

    if not isinstance(interaction.user, discord.Member):
        return
    if es_armero(interaction.user):
        return

    COMANDOS_RESTRINGIDOS = {
        "armas", "balas", "pistolas", "arma_blanca", "otros", "drogas",
        "apagar_alertas", "encender_alertas", "configurar_alertas",
        "inicio_operativo", "terminar_operativo",
        "retiros_pendientes", "sincronizar_historial_texto",
        "antirrobo", "whitelist_antirrobo",
        "config_verificacion", "umbral_item", "ver_umbrales",
        "validar_retiros_dias",
        "chemi_activar", "chemi_desactivar", "chemi_estado", "chemi_deuda_ver", "chemi_deuda_saldar",
        "chemi_limite_reset", "chemi_panel_setup",
    }

    cmd_name = interaction.data.get("name", "") if interaction.data else ""
    if cmd_name not in COMANDOS_RESTRINGIDOS:
        return

    logs_channel = bot.get_channel(BOT_LOGS_CHANNEL_ID)
    if not logs_channel:
        return

    member = interaction.user
    embed = discord.Embed(
        title="⚠️ Comando restringido usado",
        color=discord.Color.orange(),
        timestamp=datetime.now(),
    )
    embed.add_field(name="👤 Usuario",  value=f"{member.mention} (`{member.id}`)", inline=True)
    embed.add_field(name="⌨️ Comando", value=f"`/{cmd_name}`",                    inline=True)
    embed.add_field(name="📢 Canal",   value=interaction.channel.mention if interaction.channel else "N/A", inline=True)
    embed.set_author(
        name=f"{member.display_name} ({member.id})",
        icon_url=member.display_avatar.url if member.display_avatar else None,
    )
    await logs_channel.send(embed=embed)


def main():
    from config import TOKEN
    inicializar_base_datos()
    if not TOKEN:
        logger.critical("❌ DISCORD_TOKEN no configurado")
        return
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
