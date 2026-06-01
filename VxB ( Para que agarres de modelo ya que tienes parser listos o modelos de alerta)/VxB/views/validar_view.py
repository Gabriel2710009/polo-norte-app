import logging
from datetime import datetime
from typing import Optional

import discord

import state
from config import DEVELOPER_ROLE_ID, RAZON_RETIRO_CHANNEL_ID, ARMERO_ROLE_ID, ONLY_ARMEROS_CHANNEL_ID
from database import get_db_connection
from utils import traducir_objeto, es_armero_o_alto_cargo

logger = logging.getLogger("ArmamentBot")


async def _cancelar_proceso_razon(client, registro_id: int):
    """
    Cancela el proceso de razón/devolución disparado por la alerta:
    - Cancela el timeout de razón (state.RAZON_TIMEOUTS)
    - Cancela el timeout de devolución (state.DEVOLUCION_TIMEOUTS)
    - Borra el mensaje de razón del canal si existe
    Llamar esto tanto al VALIDAR como al NO VALIDAR desde ALERTAS_CHANNEL_ID.
    """
    # Cancelar timeouts en memoria
    task_razon = state.RAZON_TIMEOUTS.pop(registro_id, None)
    if task_razon:
        task_razon.cancel()

    task_dev = state.DEVOLUCION_TIMEOUTS.pop(registro_id, None)
    if task_dev:
        task_dev.cancel()

    # Borrar mensaje de razón del canal y limpiar BD
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT razon_message_id, razon_channel_id FROM registros_armas WHERE id = %s",
            (registro_id,),
        )
        row = cursor.fetchone()

        if row and row.get("razon_message_id") and row.get("razon_channel_id"):
            try:
                canal_razon = client.get_channel(int(row["razon_channel_id"]))
                if canal_razon:
                    msg_razon = await canal_razon.fetch_message(int(row["razon_message_id"]))
                    await msg_razon.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            except Exception as e:
                logger.warning(f"⚠️ No se pudo borrar mensaje de razón (registro {registro_id}): {e}")

            # Limpiar campos en BD
            cursor.execute(
                "UPDATE registros_armas SET razon_message_id = NULL, razon_channel_id = NULL WHERE id = %s",
                (registro_id,),
            )
            conn.commit()

        cursor.close()
        conn.close()
    except Exception as e:
        logger.warning(f"⚠️ Error cancelando proceso de razón (registro {registro_id}): {e}")


async def _get_objeto_registro(client, registro_id: int) -> str:
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT objeto FROM registros_armas WHERE id = %s", (registro_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return (row or {}).get("objeto") or ""
    except Exception:
        return ""


class ValidarRetiroModal(discord.ui.Modal):
    def __init__(self, registro_id: int, alerta_channel_id: int, alerta_message_id: int):
        super().__init__(title="Validar retiro (justificación opcional)")
        self.registro_id       = registro_id
        self.alerta_channel_id = alerta_channel_id
        self.alerta_message_id = alerta_message_id
        self.justificacion     = discord.ui.TextInput(
            label="Justificación (opcional)",
            placeholder="Escribí el motivo de aprobación (opcional)",
            required=False,
            max_length=400,
        )
        self.add_item(self.justificacion)

    async def on_submit(self, interaction: discord.Interaction):
        from alertas import actualizar_alerta_estado, cerrar_mensajes_devolucion
        from log_actions import log_accion

        await interaction.response.defer(ephemeral=True)

        try:
            justificacion_txt = str(self.justificacion.value or "").strip()
            conn   = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE registros_armas
                SET validado                 = TRUE,
                    validado_por             = %s,
                    fecha_validacion         = NOW(),
                    no_validado              = FALSE,
                    justificacion_validacion = NULLIF(%s, '')
                WHERE id = %s
            """, (interaction.user.name, justificacion_txt, self.registro_id))
            conn.commit()
            cursor.close()
            conn.close()

            registro    = {"alerta_channel_id": self.alerta_channel_id, "alerta_message_id": self.alerta_message_id}
            estado_txt  = "✅ Validado"
            if justificacion_txt:
                estado_txt += f"\nJustificación: {justificacion_txt}"
            await actualizar_alerta_estado(registro, estado_txt, discord.Color.green(), interaction.user.mention)

            # Cancelar proceso de razón + devolución automática
            await _cancelar_proceso_razon(interaction.client, self.registro_id)

            # Cerrar mensajes de devolución pendientes
            await cerrar_mensajes_devolucion(self.registro_id, interaction.user.mention)

            await log_accion(
                interaction.user, "Validó retiro",
                f"Registro ID: `{self.registro_id}`" + (f" | Justif: {justificacion_txt}" if justificacion_txt else ""),
                discord.Color.green(), "✅",
            )
            await interaction.followup.send("📤 Retiro validado correctamente.", ephemeral=True)
        except Exception as e:
            logger.error(f"❌ Error en modal validar retiro: {e}", exc_info=True)
            try:
                await interaction.followup.send("❌ Error al validar retiro.", ephemeral=True)
            except Exception:
                pass


class RazonRetiroModal(discord.ui.Modal):
    def __init__(self, registro_id: int, alerta_channel_id: int, alerta_message_id: int):
        super().__init__(title="Razón del retiro")
        self.registro_id       = registro_id
        self.alerta_channel_id = alerta_channel_id
        self.alerta_message_id = alerta_message_id
        self.razon             = discord.ui.TextInput(
            label="Razón del retiro",
            placeholder="Explicá por qué se hizo este retiro",
            required=True,
            max_length=600,
        )
        self.add_item(self.razon)

    async def on_submit(self, interaction: discord.Interaction):
        # ── CRÍTICO: defer inmediato antes de cualquier operación lenta ──
        await interaction.response.defer(ephemeral=True)

        try:
            razon_txt = str(self.razon.value).strip()
            conn   = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE registros_armas SET razon_retiro = %s WHERE id = %s RETURNING razon_message_id, razon_channel_id",
                (razon_txt, self.registro_id),
            )
            row = cursor.fetchone()
            conn.commit()
            cursor.close()
            conn.close()

            # Cancelar timeout de razón
            task = state.RAZON_TIMEOUTS.pop(self.registro_id, None)
            if task:
                task.cancel()

            # Borrar el mensaje de razón del canal
            razon_msg_id = (row or {}).get("razon_message_id")
            razon_ch_id  = (row or {}).get("razon_channel_id")
            if razon_msg_id and razon_ch_id:
                try:
                    canal_razon = interaction.client.get_channel(int(razon_ch_id))
                    if canal_razon:
                        msg_razon = await canal_razon.fetch_message(int(razon_msg_id))
                        await msg_razon.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                    logger.warning(f"⚠️ No se pudo borrar mensaje de razón: {e}")
            elif interaction.message:
                try:
                    await interaction.message.delete()
                except Exception:
                    pass

            # Actualizar embed de alerta con la razón
            channel = interaction.client.get_channel(int(self.alerta_channel_id))
            alerta_msg = None
            if channel:
                try:
                    alerta_msg = await channel.fetch_message(int(self.alerta_message_id))
                    if alerta_msg.embeds:
                        embed     = alerta_msg.embeds[0]
                        colocado  = False
                        for idx, field in enumerate(embed.fields):
                            if field.name.lower().startswith("razón del retiro"):
                                embed.set_field_at(idx, name="Razón del retiro", value=razon_txt[:1024], inline=False)
                                colocado = True
                                break
                        if not colocado:
                            embed.add_field(name="Razón del retiro", value=razon_txt[:1024], inline=False)
                        await alerta_msg.edit(embed=embed)
                except Exception as e:
                    logger.error(f"❌ Error actualizando embed con razón: {e}", exc_info=True)

            # Notificar al armero que ya cargó la razón
            try:
                canal_armeros = interaction.client.get_channel(ONLY_ARMEROS_CHANNEL_ID)
                if canal_armeros:
                    obj_txt  = traducir_objeto(
                        await _get_objeto_registro(interaction.client, self.registro_id)
                    )
                    link = (
                        f"https://discord.com/channels/{interaction.guild_id}"
                        f"/{self.alerta_channel_id}/{self.alerta_message_id}"
                        if alerta_msg else ""
                    )
                    embed_notif = discord.Embed(
                        title="📋 Razón de retiro cargada",
                        color=discord.Color.from_rgb(88, 101, 242),
                        timestamp=datetime.now(),
                    )
                    embed_notif.add_field(name="👤 Usuario", value=interaction.user.mention, inline=True)
                    embed_notif.add_field(name="📦 Objeto", value=obj_txt, inline=True)
                    embed_notif.add_field(name="📝 Razón", value=razon_txt[:300], inline=False)
                    if link:
                        embed_notif.add_field(name="🔗 Ver alerta", value=link, inline=False)
                    embed_notif.set_footer(text=f"Registro #{self.registro_id}")
                    await canal_armeros.send(
                        content=f"<@&{ARMERO_ROLE_ID}>",
                        embed=embed_notif,
                    )
            except Exception as e:
                logger.warning(f"⚠️ No se pudo notificar al armero: {e}")

            await interaction.followup.send("📤 Razón de retiro cargada correctamente.", ephemeral=True)
        except Exception as e:
            logger.error(f"❌ Error guardando razón de retiro: {e}", exc_info=True)
            try:
                await interaction.followup.send("❌ Error guardando razón de retiro.", ephemeral=True)
            except Exception:
                pass


class RazonRetiroView(discord.ui.View):
    def __init__(self, registro_id: int, alerta_channel_id: int, alerta_message_id: int, autor_discord_id: Optional[int] = None):
        super().__init__(timeout=None)
        self.registro_id       = registro_id
        self.alerta_channel_id = alerta_channel_id
        self.alerta_message_id = alerta_message_id
        self.autor_discord_id  = autor_discord_id

    @discord.ui.button(
        label="Cargar razón del retiro",
        style=discord.ButtonStyle.secondary,
        custom_id="razon_retiro_cargar",
    )
    async def cargar_razon(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.message:
            await interaction.response.send_message("❌ No se encontró el mensaje.", ephemeral=True)
            return

        role_ids = {r.id for r in interaction.user.roles}
        es_dev   = DEVELOPER_ROLE_ID in role_ids

        es_retirador = False
        if self.autor_discord_id is not None:
            es_retirador = str(self.autor_discord_id) == str(interaction.user.id)
        else:
            try:
                conn   = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT discord_id FROM registros_armas WHERE id = %s", (self.registro_id,)
                )
                row = cursor.fetchone()
                cursor.close()
                conn.close()
            except Exception as e:
                logger.error(f"❌ Error consultando dueño del retiro {self.registro_id}: {e}", exc_info=True)
                await interaction.response.send_message("❌ Error verificando permisos.", ephemeral=True)
                return

            if not row or not row.get("discord_id"):
                await interaction.response.send_message("❌ No se pudo identificar quién hizo el retiro.", ephemeral=True)
                return

            es_retirador = str(row["discord_id"]) == str(interaction.user.id)

        if not es_dev and not es_retirador:
            await interaction.response.send_message(
                "❌ Este botón solo lo puede usar la persona que hizo el retiro.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            RazonRetiroModal(self.registro_id, self.alerta_channel_id, self.alerta_message_id)
        )


# ─── VIEW VALIDAR DESDE SOLICITUD DE DEVOLUCIÓN ──────────────

class ValidarDesdeDevolucionModal(discord.ui.Modal):
    """Modal para que el armero valide directamente desde el mensaje de devolución."""
    def __init__(self, registro_id: int):
        super().__init__(title="Validar retiro desde devolución")
        self.registro_id = registro_id
        self.justificacion = discord.ui.TextInput(
            label="Justificación (opcional)",
            placeholder="Ej: El retiro fue autorizado, ítem devuelto correctamente",
            required=False,
            max_length=400,
        )
        self.add_item(self.justificacion)

    async def on_submit(self, interaction: discord.Interaction):
        from alertas import actualizar_alerta_estado, cerrar_mensajes_devolucion, marcar_registro_devuelto
        from log_actions import log_accion

        await interaction.response.defer(ephemeral=True)

        try:
            justificacion_txt = str(self.justificacion.value or "").strip()

            # Marcar como devuelto Y validado
            conn   = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE registros_armas
                SET validado                 = TRUE,
                    validado_por             = %s,
                    fecha_validacion         = NOW(),
                    no_validado              = FALSE,
                    devuelto                 = TRUE,
                    devuelto_por             = %s,
                    fecha_devolucion         = NOW(),
                    justificacion_validacion = NULLIF(%s, '')
                WHERE id = %s
                RETURNING alerta_message_id, alerta_channel_id
            """, (interaction.user.name, interaction.user.name, justificacion_txt, self.registro_id))
            row = cursor.fetchone()
            conn.commit()
            cursor.close()
            conn.close()

            # Actualizar embed de alerta
            if row and row.get("alerta_message_id") and row.get("alerta_channel_id"):
                estado_txt = f"✅ Validado y devuelto por {interaction.user.mention}"
                if justificacion_txt:
                    estado_txt += f"\nJustificación: {justificacion_txt}"
                await actualizar_alerta_estado(row, estado_txt, discord.Color.green(), interaction.user.mention)

            # Cerrar mensajes de devolución
            await cerrar_mensajes_devolucion(self.registro_id, interaction.user.mention)

            await log_accion(
                interaction.user,
                "Validó retiro desde solicitud de devolución",
                f"Registro ID: `{self.registro_id}`" + (f" | Justif: {justificacion_txt}" if justificacion_txt else ""),
                discord.Color.green(), "✅",
            )
            await interaction.followup.send("✅ Retiro validado y marcado como devuelto.", ephemeral=True)
        except Exception as e:
            logger.error(f"❌ Error validando desde devolución: {e}", exc_info=True)
            try:
                await interaction.followup.send("❌ Error al validar.", ephemeral=True)
            except Exception:
                pass


class ValidarView(discord.ui.View):
    def __init__(self, registro_id: int, autor_discord_id: Optional[int] = None):
        super().__init__(timeout=None)
        self.registro_id = registro_id
        self.autor_discord_id = autor_discord_id

    @discord.ui.button(
        label="✅ Validar retiro",
        style=discord.ButtonStyle.success,
        custom_id="validar_retiro",
    )
    async def validar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ No tenés permiso.", ephemeral=True)
            return
        if not interaction.message:
            await interaction.response.send_message("❌ No se encontró el mensaje.", ephemeral=True)
            return
        await interaction.response.send_modal(
            ValidarRetiroModal(
                self.registro_id,
                interaction.message.channel.id,
                interaction.message.id,
            )
        )

    @discord.ui.button(
        label="❌ No validar retiro",
        style=discord.ButtonStyle.danger,
        custom_id="no_validar_retiro",
    )
    async def no_validar(self, interaction: discord.Interaction, button: discord.ui.Button):
        from alertas import iniciar_solicitud_devolucion, actualizar_alerta_estado
        from log_actions import log_accion

        if not es_armero_o_alto_cargo(interaction.user):
            await interaction.response.send_message("⛔ No tenés permiso.", ephemeral=True)
            return

        await interaction.response.defer()
        try:
            conn   = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE registros_armas
                SET no_validado       = TRUE,
                    no_validado_por   = %s,
                    fecha_no_validado = NOW(),
                    validado          = FALSE
                WHERE id = %s
                RETURNING id, discord_id, objeto, cantidad, nombre,
                          devolucion_request_message_id
            """, (interaction.user.name, self.registro_id))
            retiro_row = cursor.fetchone()
            conn.commit()
            cursor.close()
            conn.close()

            embed = interaction.message.embeds[0]
            embed.color = discord.Color.red()
            reemplazado = False
            for idx, field in enumerate(embed.fields):
                if field.name.lower() == "estado":
                    embed.set_field_at(
                        idx, name="Estado",
                        value=f"❌ NO validado por {interaction.user.mention}",
                        inline=False,
                    )
                    reemplazado = True
                    break
            if not reemplazado:
                embed.add_field(
                    name="Estado",
                    value=f"❌ NO validado por {interaction.user.mention}",
                    inline=False,
                )
            await interaction.message.edit(embed=embed, view=None)

            # Cancelar proceso de razón (timeout razón + timeout devolución + borrar msg razón)
            await _cancelar_proceso_razon(interaction.client, self.registro_id)

            # Iniciar devolución si NO hay una ya en curso
            ya_tiene_solicitud = retiro_row and retiro_row.get("devolucion_request_message_id")
            if not ya_tiene_solicitud:
                await iniciar_solicitud_devolucion(retiro_row or {}, interaction.user)

            await log_accion(
                interaction.user, "Rechazó retiro",
                f"Registro ID: `{self.registro_id}`",
                discord.Color.red(), "❌",
            )
            await interaction.followup.send("📤 Retiro marcado como NO válido.", ephemeral=True)

        except Exception as e:
            logger.error(f"❌ Error rechazando retiro: {e}", exc_info=True)
            await interaction.followup.send("❌ Error al rechazar.", ephemeral=True)
