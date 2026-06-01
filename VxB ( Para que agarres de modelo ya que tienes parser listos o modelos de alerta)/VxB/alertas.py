import asyncio
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
import discord

import state
from config import (
    ALERTAS_CHANNEL_ID,
    ARMERO_ROLE_ID,
    DEPOSITO_SOLICITUD_CHANNEL_ID,
    LOGS_CHANNEL_ID,
    ONLY_ARMEROS_CHANNEL_ID,
    RAZON_RETIRO_CHANNEL_ID,
    TIEMPO_DEVOLUCION,
    TIEMPO_DEVOLUCION_SOLICITUD,
    TIEMPO_RAZON_RETIRO,
)
from database import get_db_connection
from utils import traducir_objeto, es_armero_o_alto_cargo, _rol_aprobador

logger = logging.getLogger("ArmamentBot")

_bot = None

def set_bot(bot_instance):
    global _bot
    _bot = bot_instance

GUILD_ID = 968286555150110790


# ─── HELPERS ──────────────────────────────────────────────────

def debe_alertar(objeto: str, cantidad: int = 1) -> bool:
    if not state.ALERTAS_ACTIVAS:
        return False
    umbral = state.UMBRALES_CANTIDAD.get(objeto)
    if umbral is not None and cantidad < umbral:
        logger.debug(f"🔇 {objeto} cantidad {cantidad} < umbral {umbral}, sin alerta")
        return False
    if not state.OBJETOS_ALERTAR:
        return True
    return objeto in state.OBJETOS_ALERTAR


def generar_preview_alertas() -> str:
    if not state.ALERTAS_ACTIVAS:
        return "🔵 **Alertas desactivadas**\n\nActualmente no se envían alertas de retiros."
    if not state.OBJETOS_ALERTAR:
        return "🔊 **Alertas activas**\n\nSe alertarán **TODOS los retiros fuera de operativo**.\n\n🔊 *No hay filtros aplicados.*"
    ejemplos       = list(state.OBJETOS_ALERTAR)[:8]
    ejemplos_texto = "\n".join(f"• {traducir_objeto(o)}" for o in ejemplos)
    extra          = f"\n… y {len(state.OBJETOS_ALERTAR) - 8} más." if len(state.OBJETOS_ALERTAR) > 8 else ""
    return f"🔊 **Alertas configuradas**\n\nSe alertarán **SOLO** los siguientes objetos:\n\n{ejemplos_texto}{extra}\n\n🔊 *Cualquier otro objeto será ignorado.*"


def _obtener_registro_basico(retiro: Optional[dict], registro_id: Optional[int]) -> Optional[dict]:
    if retiro and retiro.get("id"):
        return retiro
    rid = registro_id or (retiro or {}).get("registro_id")
    if not rid:
        return None
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, discord_id, objeto, cantidad, nombre FROM registros_armas WHERE id = %s", (int(rid),))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return row
    except Exception as e:
        logger.error(f"❌ Error obteniendo registro básico {rid}: {e}", exc_info=True)
        return None


async def obtener_miembro_seguro(guild: discord.Guild, member_id: int, *, retries: int = 2, retry_delay: float = 0.6) -> Optional[discord.Member]:
    """
    Intenta resolver un miembro sin hacer caer el flujo por cortes transitorios de Discord.
    Primero usa la caché local y luego reintenta `fetch_member` si hace falta.
    """
    cached = guild.get_member(member_id)
    if cached is not None:
        return cached

    last_error: Optional[Exception] = None
    for attempt in range(max(1, retries)):
        try:
            return await guild.fetch_member(member_id)
        except (discord.NotFound, discord.Forbidden):
            return None
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                await asyncio.sleep(retry_delay * (attempt + 1))
        except Exception as exc:
            last_error = exc
            break

    if last_error:
        logger.debug(f"No se pudo resolver miembro {member_id}: {last_error}")
    return None


async def enviar_mensaje_seguro(channel: discord.abc.Messageable, *, retries: int = 2, retry_delay: float = 0.6, **kwargs):
    """
    Envía un mensaje con reintentos ante errores transitorios de red.
    """
    last_error: Optional[Exception] = None
    for attempt in range(max(1, retries)):
        try:
            return await channel.send(**kwargs)
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError, discord.HTTPException) as exc:
            last_error = exc
            if attempt + 1 < retries:
                await asyncio.sleep(retry_delay * (attempt + 1))
                continue
            break

    if last_error:
        raise last_error
    return None


# ─── ACTUALIZAR ESTADO DE ALERTA ──────────────────────────────

async def actualizar_alerta_estado(registro: dict, estado_texto: str, color: discord.Color, actor_mention: Optional[str] = None):
    try:
        if not registro or not registro.get("alerta_channel_id") or not registro.get("alerta_message_id"):
            return
        channel = _bot.get_channel(int(registro["alerta_channel_id"]))
        if not channel:
            return
        msg = await channel.fetch_message(int(registro["alerta_message_id"]))
        if not msg.embeds:
            return
        embed = msg.embeds[0]
        embed.color = color
        texto = estado_texto if not actor_mention else f"{estado_texto}\nPor: {actor_mention}"
        reemplazado = False
        for idx, field in enumerate(embed.fields):
            if field.name.lower() == "estado":
                embed.set_field_at(idx, name="Estado", value=texto, inline=False)
                reemplazado = True
                break
        if not reemplazado:
            embed.add_field(name="Estado", value=texto, inline=False)
        await msg.edit(embed=embed, view=None)
    except Exception as e:
        logger.error(f"❌ Error actualizando estado de alerta: {e}", exc_info=True)


# ─── MARCAR DEVUELTO ──────────────────────────────────────────

async def marcar_registro_devuelto(registro_id: int, actor: discord.Member):
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE registros_armas
            SET devuelto         = TRUE,
                devuelto_por     = %s,
                fecha_devolucion = NOW(),
                validado         = TRUE,
                validado_por     = COALESCE(validado_por, %s),
                fecha_validacion = COALESCE(fecha_validacion, NOW()),
                no_validado      = FALSE
            WHERE id = %s
            RETURNING id, alerta_message_id, alerta_channel_id
        """, (actor.name, actor.name, registro_id))
        row = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()
        return row
    except Exception as e:
        logger.error(f"❌ Error marcando registro devuelto: {e}", exc_info=True)
        return None


# ─── BUSCAR LOG DE DEPÓSITO ───────────────────────────────────

async def buscar_log_deposito_devolucion(discord_id: str, objeto: str, desde: datetime, hasta: datetime) -> Optional[str]:
    try:
        channel = _bot.get_channel(LOGS_CHANNEL_ID)
        if not channel:
            return None
        async for msg in channel.history(limit=200, after=desde, before=hasta, oldest_first=False):
            content = msg.content or ""
            if str(discord_id) not in content:
                deposito_encontrado = False
                for embed in msg.embeds:
                    desc = embed.description or ""
                    if str(discord_id) in desc and objeto.lower() in desc.lower():
                        if "metido" in desc.lower() or "deposito" in desc.upper() or "depósito" in desc.upper():
                            deposito_encontrado = True
                            break
                if not deposito_encontrado:
                    continue
            if content and str(discord_id) in content:
                if objeto.lower() in content.lower() or f"`{objeto}`" in content:
                    if "metido" in content.lower():
                        return f"https://discord.com/channels/{GUILD_ID}/{LOGS_CHANNEL_ID}/{msg.id}"
        return None
    except Exception as e:
        logger.warning(f"⚠️ No se pudo buscar log de depósito: {e}")
        return None


# ─── TIMEOUT DEVOLUCIÓN ───────────────────────────────────────

async def _programar_timeout_devolucion(registro_id: int, usuario_id: Optional[str], objeto: Optional[str], cantidad: int, solicitud_timestamp: Optional[datetime] = None):
    if registro_id in state.DEVOLUCION_TIMEOUTS:
        return

    async def _tarea():
        try:
            await asyncio.sleep(TIEMPO_DEVOLUCION_SOLICITUD)
            conn   = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT devuelto FROM registros_armas WHERE id = %s", (int(registro_id),))
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            if row and row.get("devuelto"):
                return

            ts_inicio = solicitud_timestamp or (datetime.now() - timedelta(seconds=TIEMPO_DEVOLUCION_SOLICITUD))
            log_link  = None
            if usuario_id and objeto:
                log_link = await buscar_log_deposito_devolucion(usuario_id, objeto, ts_inicio, datetime.now())

            channel = _bot.get_channel(ONLY_ARMEROS_CHANNEL_ID)
            if not channel:
                return

            usuario = f"<@{usuario_id}>" if usuario_id else "N/A"
            obj_txt = traducir_objeto(objeto) if objeto else "N/A"
            hora    = datetime.now().strftime("%H:%M:%S")

            if log_link:
                embed = discord.Embed(title="📦 Devolución detectada por log", color=discord.Color.green(), timestamp=datetime.now())
                embed.add_field(name="👤 Usuario",      value=usuario,                    inline=True)
                embed.add_field(name="📦 Objeto",       value=f"`{obj_txt}` x{cantidad}", inline=True)
                embed.add_field(name="🕐 Hora",         value=hora,                       inline=True)
                embed.add_field(name="🆔 Registro ID",  value=f"`{registro_id}`",         inline=True)
                embed.add_field(name="🔗 Log depósito", value=log_link,                   inline=False)
                await channel.send(embed=embed)
                return

            embed = discord.Embed(title="⚠️ No devolvió el item", color=discord.Color.orange(), timestamp=datetime.now())
            embed.add_field(name="👤 Usuario",     value=usuario,                    inline=True)
            embed.add_field(name="📦 Objeto",      value=f"`{obj_txt}` x{cantidad}", inline=True)
            embed.add_field(name="🕐 Hora",        value=hora,                       inline=True)
            embed.add_field(name="🆔 Registro ID", value=f"`{registro_id}`",         inline=True)
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"❌ Error en timeout de devolución (registro {registro_id}): {e}", exc_info=True)
        finally:
            state.DEVOLUCION_TIMEOUTS.pop(registro_id, None)

    state.DEVOLUCION_TIMEOUTS[registro_id] = _bot.loop.create_task(_tarea())


# ─── CERRAR MENSAJES DE DEVOLUCIÓN ───────────────────────────

async def cerrar_mensajes_devolucion(registro_id: int, actor_mention: str):
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT devolucion_request_message_id, devolucion_request_channel_id,
                   armero_confirm_message_id,     armero_confirm_channel_id
            FROM registros_armas WHERE id = %s
        """, (registro_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if not row:
            return

        for msg_id_key, ch_id_key, label in [
            ("devolucion_request_message_id", "devolucion_request_channel_id", "✅ Validado — cerrado"),
            ("armero_confirm_message_id",     "armero_confirm_channel_id",     "✅ Retiro validado — ya no es necesario confirmar"),
        ]:
            msg_id = row.get(msg_id_key)
            ch_id  = row.get(ch_id_key)
            if msg_id and ch_id:
                try:
                    ch  = _bot.get_channel(int(ch_id))
                    if ch:
                        msg  = await ch.fetch_message(int(msg_id))
                        view = discord.ui.View()
                        view.add_item(discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, disabled=True))
                        await msg.edit(view=view)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
                except Exception as e:
                    logger.warning(f"⚠️ No se pudo cerrar msg {msg_id}: {e}")
    except Exception as e:
        logger.error(f"❌ Error cerrando mensajes de devolución: {e}", exc_info=True)


# ─── VIEW ARMERO (ONLY_ARMEROS_CHANNEL) ───────────────────────
# Solo confirmar/rechazar. Botón "Validar retiro" está en el mensaje del usuario.

class DevolucionArmeroConfirmView(discord.ui.View):
    def __init__(self, registro_id: int, usuario_id: Optional[int], objeto: Optional[str], cantidad: int, log_link: Optional[str] = None):
        super().__init__(timeout=None)
        self.registro_id = registro_id
        self.usuario_id  = usuario_id
        self.objeto      = objeto
        self.cantidad    = cantidad
        self.log_link    = log_link

    @discord.ui.button(label="✅ Confirmar devolución", style=discord.ButtonStyle.success, custom_id="armero_confirmar_devolucion")
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Solo armeros o alto cargo pueden confirmar.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        result = await marcar_registro_devuelto(self.registro_id, interaction.user)
        if result and result.get("alerta_message_id") and result.get("alerta_channel_id"):
            try:
                await actualizar_alerta_estado(result, f"✅ Devuelto confirmado por {interaction.user.mention}", discord.Color.green())
            except Exception as e:
                logger.warning(f"⚠️ No se pudo actualizar alerta: {e}")
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass
        # Cerrar mensaje solicitud usuario
        try:
            conn   = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT devolucion_request_message_id, devolucion_request_channel_id FROM registros_armas WHERE id = %s", (self.registro_id,))
            row_req = cursor.fetchone()
            cursor.close()
            conn.close()
            if row_req:
                req_msg_id = row_req.get("devolucion_request_message_id")
                req_ch_id  = row_req.get("devolucion_request_channel_id")
                if req_msg_id and req_ch_id:
                    ch = _bot.get_channel(int(req_ch_id))
                    if ch:
                        msg_req     = await ch.fetch_message(int(req_msg_id))
                        view_closed = discord.ui.View()
                        view_closed.add_item(discord.ui.Button(label="✅ Devolución confirmada por armero", style=discord.ButtonStyle.secondary, disabled=True))
                        await msg_req.edit(view=view_closed)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        except Exception as e:
            logger.warning(f"⚠️ No se pudo cerrar msg solicitud: {e}")
        await interaction.followup.send(f"✅ Devolución confirmada para registro `{self.registro_id}`.", ephemeral=True)
        from log_actions import log_accion
        await log_accion(interaction.user, "Confirmó devolución", f"Registro ID: `{self.registro_id}` | {traducir_objeto(self.objeto)} x{self.cantidad}", discord.Color.green(), "📦")

    @discord.ui.button(label="❌ No devolvió", style=discord.ButtonStyle.danger, custom_id="armero_rechazar_devolucion")
    async def rechazar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Solo armeros o alto cargo pueden gestionar esto.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass
        await interaction.followup.send(f"❌ Marcado como NO devuelto para registro `{self.registro_id}`.", ephemeral=True)
        from log_actions import log_accion
        await log_accion(interaction.user, "Marcó como NO devuelto", f"Registro ID: `{self.registro_id}` | {traducir_objeto(self.objeto)} x{self.cantidad}", discord.Color.red(), "❌")


# ─── VIEW USUARIO (DEPOSITO_SOLICITUD_CHANNEL) ────────────────
# Tiene: "Devolví el ítem" (usuario/dev) + "Validar retiro" (solo armeros)
# El botón "Validar retiro" está aquí, en el mensaje del usuario.

class DevolucionConfirmView(discord.ui.View):
    def __init__(self, registro_id: int, usuario_id: Optional[int], objeto: Optional[str], cantidad: int, log_link: Optional[str] = None):
        super().__init__(timeout=None)
        self.registro_id = registro_id
        self.usuario_id  = usuario_id
        self.objeto      = objeto
        self.cantidad    = cantidad
        self.log_link    = log_link

    @discord.ui.button(label="✅ Devolví el ítem", style=discord.ButtonStyle.success, custom_id="devolucion_confirm")
    async def confirmar_devolucion(self, interaction: discord.Interaction, button: discord.ui.Button):
        from config import DEVELOPER_ROLE_ID as _DEV
        role_ids     = {r.id for r in interaction.user.roles}
        es_developer = _DEV in role_ids

        if not es_developer:
            if self.usuario_id and interaction.user.id != self.usuario_id:
                await interaction.response.send_message("⛔ Solo el usuario al que se le solicitó la devolución puede confirmar.", ephemeral=True)
                return

        await interaction.response.defer(ephemeral=True)
        button.disabled = True
        button.label    = "✅ Devuelto (pendiente confirmación armero)"
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

        # Buscar log reciente
        log_link_real = self.log_link
        try:
            if self.usuario_id and self.objeto:
                encontrado = await buscar_log_deposito_devolucion(str(self.usuario_id), self.objeto, datetime.now() - timedelta(minutes=15), datetime.now())
                if encontrado:
                    log_link_real = encontrado
        except Exception as e:
            logger.warning(f"⚠️ No se pudo buscar log inmediato: {e}")

        # Notificar armeros
        try:
            canal_armeros = interaction.client.get_channel(ONLY_ARMEROS_CHANNEL_ID)
            if canal_armeros:
                obj_txt = traducir_objeto(self.objeto) if self.objeto else "N/A"
                hora    = datetime.now().strftime("%H:%M:%S")
                embed = discord.Embed(title="📦 Devolución notificada — pendiente confirmación", description="El usuario indica que devolvió el ítem. Confirmá o rechazá.", color=discord.Color.gold(), timestamp=datetime.now())
                embed.add_field(name="👤 Usuario",     value=f"<@{interaction.user.id}>",   inline=True)
                embed.add_field(name="📦 Objeto",      value=f"{obj_txt} x{self.cantidad}", inline=True)
                embed.add_field(name="🕐 Hora",        value=hora,                           inline=True)
                embed.add_field(name="🆔 Registro ID", value=f"`{self.registro_id}`",        inline=True)
                if self.log_link:
                    embed.add_field(name="🔗 Log del retiro",   value=self.log_link,  inline=False)
                if log_link_real and log_link_real != self.log_link:
                    embed.add_field(name="🔗 Log del depósito", value=log_link_real,  inline=False)
                armero_view = DevolucionArmeroConfirmView(registro_id=self.registro_id, usuario_id=self.usuario_id, objeto=self.objeto, cantidad=self.cantidad, log_link=log_link_real)
                armero_msg  = await canal_armeros.send(content=f"<@&{ARMERO_ROLE_ID}>", embed=embed, view=armero_view)
                try:
                    conn   = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("UPDATE registros_armas SET armero_confirm_message_id = %s, armero_confirm_channel_id = %s WHERE id = %s", (armero_msg.id, canal_armeros.id, self.registro_id))
                    conn.commit()
                    cursor.close()
                    conn.close()
                except Exception as e:
                    logger.warning(f"⚠️ No se pudo guardar armero_confirm_message_id: {e}")
        except Exception as e:
            logger.error(f"❌ Error notificando devolución a armeros: {e}", exc_info=True)

        await interaction.followup.send("✅ Notificamos a los armeros. Esperá la confirmación.", ephemeral=True)

    @discord.ui.button(label="✅ Validar retiro", style=discord.ButtonStyle.primary, custom_id="devolucion_validar_retiro")
    async def validar_retiro(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        Solo para armeros/alto cargo/developer.
        Valida el retiro directamente sin necesitar que el usuario confirme devolución.
        Aparece en DEPOSITO_SOLICITUD_CHANNEL_ID.
        """
        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ Solo armeros o alto cargo pueden validar el retiro desde aquí.", ephemeral=True)
            return
        from views.validar_view import ValidarDesdeDevolucionModal
        await interaction.response.send_modal(ValidarDesdeDevolucionModal(self.registro_id))


# ─── SOLICITUD DE DEVOLUCIÓN ──────────────────────────────────

async def iniciar_solicitud_devolucion(retiro: Optional[dict], moderador: Optional[discord.Member] = None, motivo: Optional[str] = None, registro_id: Optional[int] = None):
    try:
        registro = _obtener_registro_basico(retiro, registro_id)
        if not registro:
            logger.warning("⚠️ No se pudo obtener registro para solicitud de devolución")
            return
        rid = registro.get("id")

        # Anti-duplicado
        if rid:
            try:
                conn   = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT devolucion_request_message_id FROM registros_armas WHERE id = %s", (int(rid),))
                check = cursor.fetchone()
                cursor.close()
                conn.close()
                if check and check.get("devolucion_request_message_id"):
                    logger.info(f"⏩ Solicitud de devolución ya existe para registro {rid}, se omite")
                    return
            except Exception as e:
                logger.warning(f"⚠️ No se pudo verificar solicitud existente: {e}")

        channel = _bot.get_channel(DEPOSITO_SOLICITUD_CHANNEL_ID)
        if not channel:
            logger.warning(f"⚠️ Canal de solicitud no encontrado: {DEPOSITO_SOLICITUD_CHANNEL_ID}")
            return

        tipo_aprobador = _rol_aprobador(moderador) if moderador else "sistema"
        usuario_id     = registro.get("discord_id")
        usuario        = f"<@{usuario_id}>" if usuario_id else registro.get("nombre", "N/A")
        registro_line  = f"\n**Registro ID:** `{rid}`" if rid else ""
        motivo_line    = f"\n**Motivo:** {motivo}" if motivo else ""

        log_link = None
        if rid:
            try:
                conn_l   = get_db_connection()
                cursor_l = conn_l.cursor()
                cursor_l.execute("SELECT alerta_channel_id, alerta_message_id FROM registros_armas WHERE id = %s", (int(rid),))
                row_link = cursor_l.fetchone()
                cursor_l.close()
                conn_l.close()
                if row_link and row_link.get("alerta_channel_id") and row_link.get("alerta_message_id"):
                    log_link = f"https://discord.com/channels/{GUILD_ID}/{row_link['alerta_channel_id']}/{row_link['alerta_message_id']}"
            except Exception as e:
                logger.warning(f"⚠️ No se pudo obtener log_link: {e}")

        embed = discord.Embed(title="📦 Solicitud de devolución", color=discord.Color.orange(), timestamp=datetime.now())
        embed.add_field(
            name="📋 Información",
            value=(
                f"El **{tipo_aprobador}** {moderador.mention if moderador else 'sistema'} "
                f"solicita que {usuario} devuelva "
                f"`{traducir_objeto(registro.get('objeto'))}` x{registro.get('cantidad', 1)}."
                f"{registro_line}{motivo_line}"
            ),
            inline=False,
        )
        embed.add_field(
            name="⬇️ Instrucciones",
            value=(
                "• **Si sos el usuario:** presioná **Devolví el ítem** cuando hayas devuelto.\n"
                "• **Si sos armero y ya verificaste:** usá **Validar retiro** directamente."
            ),
            inline=False,
        )
        if log_link:
            embed.add_field(name="🔗 Ver alerta de retiro", value=log_link, inline=False)
        embed.set_footer(text=f"Registro #{rid}")

        dev_view = DevolucionConfirmView(
            registro_id=int(rid) if rid else 0,
            usuario_id=int(usuario_id) if usuario_id else None,
            objeto=registro.get("objeto"),
            cantidad=int(registro.get("cantidad", 1)),
            log_link=log_link,
        )
        ts_solicitud = datetime.now()
        msg = await channel.send(content=f"{usuario}" if usuario_id else None, embed=embed, view=dev_view)
        logger.info(f"📦 Solicitud devolución enviada | Registro: {rid} | Usuario: {usuario}")

        if rid:
            try:
                conn   = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("UPDATE registros_armas SET devolucion_request_message_id = %s, devolucion_request_channel_id = %s WHERE id = %s", (msg.id, channel.id, int(rid)))
                conn.commit()
                cursor.close()
                conn.close()
            except Exception as e:
                logger.error(f"❌ Error ligando solicitud de devolución a BD: {e}", exc_info=True)
            await _programar_timeout_devolucion(int(rid), str(usuario_id) if usuario_id else None, registro.get("objeto"), int(registro.get("cantidad", 1)), solicitud_timestamp=ts_solicitud)
    except Exception as e:
        logger.error(f"❌ Error enviando solicitud de devolución: {e}", exc_info=True)


# ─── TIMEOUT RAZÓN ────────────────────────────────────────────

async def _programar_timeout_razon(registro_id: int, alerta_channel_id: int, alerta_message_id: int):
    if registro_id in state.RAZON_TIMEOUTS:
        return

    async def _tarea():
        try:
            await asyncio.sleep(TIEMPO_RAZON_RETIRO)
            conn   = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT razon_retiro, devuelto, razon_message_id, razon_channel_id FROM registros_armas WHERE id = %s", (int(registro_id),))
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            if row and row.get("devuelto"):
                return
            if (row or {}).get("razon_retiro"):
                return
            razon_msg_id = (row or {}).get("razon_message_id")
            razon_ch_id  = (row or {}).get("razon_channel_id")
            if razon_msg_id and razon_ch_id:
                try:
                    canal_razon = _bot.get_channel(int(razon_ch_id))
                    if canal_razon:
                        msg_razon = await canal_razon.fetch_message(int(razon_msg_id))
                        await msg_razon.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                    logger.warning(f"⚠️ No se pudo borrar mensaje de razón (registro {registro_id}): {e}")
            await iniciar_solicitud_devolucion(retiro=None, moderador=None, motivo="No cargó razón en tiempo", registro_id=int(registro_id))
        except Exception as e:
            logger.error(f"❌ Error en timeout de razón (registro {registro_id}): {e}", exc_info=True)
        finally:
            state.RAZON_TIMEOUTS.pop(registro_id, None)

    state.RAZON_TIMEOUTS[registro_id] = _bot.loop.create_task(_tarea())


# ─── SOLICITUD DE RAZÓN ───────────────────────────────────────

async def enviar_solicitud_razon_retiro(datos: dict, alerta_channel_id: int, alerta_message_id: int):
    from views.validar_view import RazonRetiroView
    try:
        channel = _bot.get_channel(RAZON_RETIRO_CHANNEL_ID)
        if not channel:
            logger.warning(f"⚠️ Canal razón no encontrado: {RAZON_RETIRO_CHANNEL_ID}")
            return
        registro_id = datos.get("registro_id")
        if not registro_id:
            return
        usuario_mention = f"<@{datos.get('discord_id')}>" if datos.get("discord_id") else "Usuario"
        embed = discord.Embed(
            title="📤 Cargar razón del retiro",
            description=(
                f"**Usuario:** {usuario_mention}\n"
                f"**Nombre IC:** {datos.get('nombre', 'N/A')}\n"
                f"**Objeto:** {traducir_objeto(datos.get('objeto'))}\n"
                f"**Cantidad:** {datos.get('cantidad', 1)}\n"
                f"**Almacén:** {datos.get('almacen', 'N/A')}\n\n"
                f"Tenés {TIEMPO_RAZON_RETIRO // 60} minutos para cargar la razón.\n"
                "Si no lo hacés, se solicitará la devolución del item."
            ),
            color=discord.Color.orange(),
            timestamp=datetime.now(),
        )
        autor_discord_id = None
        try:
            if datos.get("discord_id") is not None:
                autor_discord_id = int(datos.get("discord_id"))
        except (TypeError, ValueError):
            autor_discord_id = None

        view = RazonRetiroView(
            int(registro_id),
            int(alerta_channel_id),
            int(alerta_message_id),
            autor_discord_id=autor_discord_id,
        )
        razon_msg = await enviar_mensaje_seguro(channel, content=usuario_mention, embed=embed, view=view)
        try:
            conn   = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE registros_armas SET razon_message_id = %s, razon_channel_id = %s WHERE id = %s", (razon_msg.id, channel.id, int(registro_id)))
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            logger.error(f"❌ Error guardando razon_message_id: {e}", exc_info=True)
        await _programar_timeout_razon(int(registro_id), int(alerta_channel_id), int(alerta_message_id))
    except Exception as e:
        logger.error(f"❌ Error enviando solicitud de razón: {e}", exc_info=True)


# ─── ENVIAR ALERTA DE RETIRO ──────────────────────────────────

async def enviar_alerta_retiro(datos: dict, mention_armero: bool = True, nota_extra: Optional[str] = None, bypass_filtro_roles: bool = False, exento_whitelist: bool = False):
    from views.validar_view import ValidarView
    try:
        if not state.ALERTAS_ACTIVAS:
            return
        objeto   = datos.get("objeto")
        cantidad = int(datos.get("cantidad", 1))
        if not objeto:
            return
        if not debe_alertar(objeto, cantidad):
            return
        discord_id  = datos.get("discord_id")
        registro_id = datos.get("registro_id")
        if not discord_id or not registro_id:
            return
        if not _bot.guilds:
            return
        guild = _bot.guilds[0]
        member = await obtener_miembro_seguro(guild, int(discord_id))
        if not member:
            return
        if not bypass_filtro_roles and es_armero_o_alto_cargo(member):
            return
        channel = _bot.get_channel(ALERTAS_CHANNEL_ID)
        if not channel:
            return
        hora = datetime.now().strftime("%H:%M:%S")
        embed = discord.Embed(
            title="📤 RETIRO FUERA DE OPERATIVO",
            color=discord.Color.from_rgb(87, 242, 135) if exento_whitelist else discord.Color.from_rgb(237, 66, 69),
            timestamp=datetime.now(),
        )
        embed.add_field(name="👤 Usuario",   value=f"<@{discord_id}>",         inline=True)
        embed.add_field(name="🏷️ Nombre IC", value=datos.get("nombre", "N/A"), inline=True)
        embed.add_field(name="🕐 Hora",      value=hora,                        inline=True)
        embed.add_field(name="📦 Objeto",    value=traducir_objeto(objeto),     inline=True)
        embed.add_field(name="🔢 Cantidad",  value=str(cantidad),               inline=True)
        embed.add_field(name="🏠 Almacén",   value=datos.get("almacen", "N/A"), inline=True)
        if nota_extra:
            embed.add_field(name="ℹ️ Nota", value=nota_extra, inline=False)
        if exento_whitelist:
            embed.add_field(name="Estado", value="📋 Validado por WHITELIST", inline=False)
        autor_discord_id = None
        try:
            autor_discord_id = int(discord_id)
        except (TypeError, ValueError):
            autor_discord_id = None
        view         = None if exento_whitelist else ValidarView(registro_id, autor_discord_id=autor_discord_id)
        ping_content = f"<@&{ARMERO_ROLE_ID}>" if mention_armero else None
        msg          = await enviar_mensaje_seguro(channel, content=ping_content, embed=embed, view=view)
        conn   = get_db_connection()
        cursor = conn.cursor()
        if exento_whitelist:
            cursor.execute("UPDATE registros_armas SET alerta_message_id = %s, alerta_channel_id = %s, validado = TRUE, validado_por = %s, fecha_validacion = NOW() WHERE id = %s", (msg.id, channel.id, "WHITELIST", registro_id))
        else:
            cursor.execute("UPDATE registros_armas SET alerta_message_id = %s, alerta_channel_id = %s WHERE id = %s", (msg.id, channel.id, registro_id))
        conn.commit()
        cursor.close()
        conn.close()
        if not exento_whitelist:
            await enviar_solicitud_razon_retiro(datos, channel.id, msg.id)
        logger.info(f"🚨 ALERTA | Usuario={member.name} ({discord_id}) | {traducir_objeto(objeto)} x{cantidad}{' | WHITELIST' if exento_whitelist else ''}")
    except Exception as e:
        logger.error(f"❌ Error enviando alerta: {e}", exc_info=True)


# ─── VERIFICAR DEVOLUCIÓN (timer legacy) ──────────────────────

async def verificar_devolucion(datos: dict):
    clave = f"{datos['discord_id']}|{datos['objeto']}"
    state.RETIROS_TEMPORALES[clave] = datetime.now()
    await asyncio.sleep(TIEMPO_DEVOLUCION)
    if clave not in state.RETIROS_TEMPORALES:
        return
    datos["registro_id"] = datos.get("registro_id")
    await enviar_alerta_retiro(datos)
    state.RETIROS_TEMPORALES.pop(clave, None)


# ─── SINCRONIZACIÓN ───────────────────────────────────────────

async def sincronizar_alertas_limite(limite: int = 50) -> str:
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, alerta_message_id, alerta_channel_id, validado, validado_por, fecha_validacion, devuelto, devuelto_por FROM registros_armas WHERE alerta_message_id IS NOT NULL AND alerta_channel_id IS NOT NULL ORDER BY id DESC LIMIT %s", (limite,))
        registros = cursor.fetchall()
        cursor.close()
        conn.close()
        if not registros:
            return "⚠️ No hay alertas para sincronizar"
        actualizados = errores = no_encontrados = sin_cambios = 0
        for i in range(0, len(registros), 5):
            for reg in registros[i:i+5]:
                try:
                    channel = _bot.get_channel(int(reg["alerta_channel_id"]))
                    if not channel:
                        errores += 1
                        continue
                    msg = await channel.fetch_message(int(reg["alerta_message_id"]))
                    if not msg.embeds:
                        errores += 1
                        continue
                    embed        = msg.embeds[0]
                    tiene_estado = any(f.name == "Estado" for f in embed.fields)
                    if reg.get("devuelto"):
                        embed.color = discord.Color.green()
                        estado_txt  = f"✅ Devuelto por {reg.get('devuelto_por') or 'N/A'}"
                        if tiene_estado:
                            for idx, f in enumerate(embed.fields):
                                if f.name == "Estado":
                                    embed.set_field_at(idx, name="Estado", value=estado_txt, inline=False)
                                    break
                        else:
                            embed.add_field(name="Estado", value=estado_txt, inline=False)
                        await msg.edit(embed=embed, view=None)
                        actualizados += 1
                    elif reg["validado"] and not tiene_estado:
                        embed.color = discord.Color.green()
                        embed.add_field(name="Estado", value=f"✅ Validado por {reg['validado_por']}", inline=False)
                        await msg.edit(embed=embed, view=None)
                        actualizados += 1
                    elif not reg["validado"] and msg.components:
                        embed.color = discord.Color.orange()
                        embed.add_field(name="Estado", value="⏱️ No fue validado por ningún armero", inline=False)
                        await msg.edit(embed=embed, view=None)
                        actualizados += 1
                    else:
                        sin_cambios += 1
                except discord.NotFound:
                    no_encontrados += 1
                except (discord.Forbidden, discord.HTTPException):
                    errores += 1
                except Exception as e:
                    logger.error(f"❌ Error sincronizando registro {reg['id']}: {e}", exc_info=True)
                    errores += 1
            if i + 5 < len(registros):
                await asyncio.sleep(6)
        return f"✅ **Sincronización completa**\n• Procesados: {len(registros)}\n• Actualizados: {actualizados}\n• Sin cambios: {sin_cambios}\n• No encontrados: {no_encontrados}\n• Errores: {errores}"
    except Exception as e:
        logger.error(f"❌ Error en sincronizar_alertas_limite: {e}", exc_info=True)
        return f"❌ Error: {e}"


async def sincronizar_por_message_id(message_id: str) -> str:
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, alerta_message_id, alerta_channel_id, validado, validado_por, devuelto, devuelto_por FROM registros_armas WHERE alerta_message_id = %s", (message_id,))
        registro = cursor.fetchone()
        cursor.close()
        conn.close()
        if not registro:
            return f"🔍 No se encontró ningún registro con `message_id={message_id}`"
        channel = _bot.get_channel(int(registro["alerta_channel_id"]))
        if not channel:
            return f"🔍 Canal no encontrado"
        try:
            msg = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            return "🔍 Mensaje no encontrado en Discord"
        except discord.Forbidden:
            return "🔐 Sin permisos"
        if not msg.embeds:
            return "🔍 El mensaje no tiene embeds"
        embed        = msg.embeds[0]
        tiene_estado = any(f.name == "Estado" for f in embed.fields)
        if registro.get("devuelto"):
            embed.color = discord.Color.green()
            embed.add_field(name="Estado", value=f"✅ Devuelto por {registro.get('devuelto_por') or 'N/A'}", inline=False)
            await msg.edit(embed=embed, view=None)
            return f"✅ Sincronizado (devuelto) | Registro ID: `{registro['id']}`"
        elif registro["validado"] and not tiene_estado:
            embed.color = discord.Color.green()
            embed.add_field(name="Estado", value=f"✅ Validado por {registro['validado_por']}", inline=False)
            await msg.edit(embed=embed, view=None)
            return f"✅ Sincronizado | Registro ID: `{registro['id']}`"
        elif not registro["validado"] and msg.components:
            embed.color = discord.Color.orange()
            embed.add_field(name="Estado", value="⏱️ No fue validado por ningún armero", inline=False)
            await msg.edit(embed=embed, view=None)
            return f"⏱️ Timeout aplicado | Registro ID: `{registro['id']}`"
        return "✅ El mensaje ya está sincronizado correctamente"
    except Exception as e:
        logger.error(f"❌ Error sincronizando message_id {message_id}: {e}", exc_info=True)
        return f"❌ Error: {e}"


async def sincronizar_por_registro_id(registro_id: int) -> str:
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT alerta_message_id FROM registros_armas WHERE id = %s", (registro_id,))
        registro = cursor.fetchone()
        cursor.close()
        conn.close()
        if not registro:
            return f"🔍 No se encontró el registro `{registro_id}`"
        if not registro["alerta_message_id"]:
            return f"🔍 El registro `{registro_id}` no tiene mensaje de alerta asociado"
        return await sincronizar_por_message_id(str(registro["alerta_message_id"]))
    except Exception as e:
        logger.error(f"❌ Error sincronizando registro_id {registro_id}: {e}", exc_info=True)
        return f"❌ Error: {e}"


async def reactivar_botones_alertas():
    from views.validar_view import ValidarView
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, discord_id, alerta_message_id, alerta_channel_id FROM registros_armas WHERE validado = FALSE AND COALESCE(devuelto, FALSE) = FALSE AND alerta_message_id IS NOT NULL AND alerta_channel_id IS NOT NULL ORDER BY id DESC LIMIT 100")
        registros = cursor.fetchall()
        cursor.close()
        conn.close()
        if not registros:
            return
        reactivados = errores = 0
        for reg in registros:
            try:
                channel = _bot.get_channel(int(reg["alerta_channel_id"]))
                if not channel:
                    continue
                msg = await channel.fetch_message(int(reg["alerta_message_id"]))
                if not msg.components:
                    autor_discord_id = None
                    try:
                        if reg.get("discord_id") is not None:
                            autor_discord_id = int(reg.get("discord_id"))
                    except (TypeError, ValueError):
                        autor_discord_id = None
                    await msg.edit(view=ValidarView(reg["id"], autor_discord_id=autor_discord_id))
                    reactivados += 1
            except discord.NotFound:
                errores += 1
            except Exception as e:
                logger.error(f"❌ Error reactivando registro {reg['id']}: {e}")
                errores += 1
        logger.info(f"✅ Reactivación completa | Reactivados: {reactivados} | Errores: {errores}")
    except Exception as e:
        logger.error(f"❌ Error en reactivar_botones_alertas: {e}", exc_info=True)
