"""
chemi.py - Sistema de balance y economia del Armario Chemi.

La integracion es tolerante a configuracion parcial: si faltan IDs de roles o
canales, el sistema sigue funcionando en modo reducido sin romper el bot.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import discord

import state
from config import (
    ALTO_CARGO_ROLE_ID,
    CHEMI_ALTOS_CARGOS_CHANNEL_ID,
    CHEMI_AVISO_CHANNEL_ID,
    CHEMI_DEUDA_ROLE_ID,
    CHEMI_PAYICO_ROLE_ID,
)
from database import get_db_connection

logger = logging.getLogger("ArmamentBot")

_bot = None

CHEMI_ALMACEN_NOMBRE = os.getenv("CHEMI_ALMACEN_NOMBRE", "Mata Panchos")
PAYICO_ROLE_ID = CHEMI_PAYICO_ROLE_ID
DEUDA_CHEMI_ROLE_ID = CHEMI_DEUDA_ROLE_ID
CHEMI_AVISO_CHANNEL_ID = CHEMI_AVISO_CHANNEL_ID
ALTOS_CARGOS_CHANNEL_ID = CHEMI_ALTOS_CARGOS_CHANNEL_ID
LIMITE_PISTOLAS_DIA = 3
HORAS_DEVOLUCION = 48
FACTOR_PROFIT = 1


def chemi_activo() -> bool:
    return bool(state.CHEMI_CONFIG.get("activo", True))


def activar_chemi(actualizado_por: Optional[str] = None) -> None:
    state.CHEMI_CONFIG["activo"] = True
    state.CHEMI_CONFIG["actualizado_por"] = actualizado_por
    state.CHEMI_CONFIG["actualizado_at"] = datetime.now()


def desactivar_chemi(actualizado_por: Optional[str] = None) -> None:
    state.CHEMI_CONFIG["activo"] = False
    state.CHEMI_CONFIG["actualizado_por"] = actualizado_por
    state.CHEMI_CONFIG["actualizado_at"] = datetime.now()


def set_bot(bot_instance):
    global _bot
    _bot = bot_instance


def es_payico(member: discord.Member) -> bool:
    return bool(PAYICO_ROLE_ID) and any(r.id == PAYICO_ROLE_ID for r in member.roles)


def tiene_deuda_chemi(member: discord.Member) -> bool:
    return bool(DEUDA_CHEMI_ROLE_ID) and any(r.id == DEUDA_CHEMI_ROLE_ID for r in member.roles)


def es_pistola(objeto: str) -> bool:
    from config import ACCESORIOS_PISTOLAS, CATEGORIAS

    pistolas = set(CATEGORIAS.get("pistolas", []))
    return objeto in pistolas and objeto not in ACCESORIOS_PISTOLAS


def es_retiro_chemi(datos: dict) -> bool:
    return (
        datos.get("tipo") == "RETIRO"
        and (datos.get("almacen") or "").strip() == CHEMI_ALMACEN_NOMBRE
    )


def es_deposito_chemi(datos: dict) -> bool:
    return (
        datos.get("tipo") == "DEPOSITO"
        and (datos.get("almacen") or "").strip() == CHEMI_ALMACEN_NOMBRE
    )


def _pistolas_retiradas_hoy(discord_id: str) -> int:
    return _get_contador_chemi(discord_id)


def _get_contador_info(discord_id: str) -> dict:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT discord_id, nombre, contador, updated_at
            FROM chemi_contadores
            WHERE discord_id = %s
            """,
            (str(discord_id),),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if not row:
            return {"discord_id": str(discord_id), "nombre": None, "contador": 0, "updated_at": None}
        return dict(row)
    except Exception as e:
        logger.error(f"❌ [Chemi] Error consultando contador: {e}", exc_info=True)
        return {"discord_id": str(discord_id), "nombre": None, "contador": 0, "updated_at": None}


def _get_contador_chemi(discord_id: str) -> int:
    return int((_get_contador_info(str(discord_id)) or {}).get("contador") or 0)


def _set_contador_chemi(discord_id: str, nombre: Optional[str], contador: int) -> int:
    contador = max(0, int(contador or 0))
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO chemi_contadores (discord_id, nombre, contador, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (discord_id) DO UPDATE SET
                nombre = COALESCE(EXCLUDED.nombre, chemi_contadores.nombre),
                contador = EXCLUDED.contador,
                updated_at = NOW()
            RETURNING contador
            """,
            (str(discord_id), nombre, contador),
        )
        row = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()
        return int((row or {}).get("contador") or contador)
    except Exception as e:
        logger.error(f"❌ [Chemi] Error actualizando contador: {e}", exc_info=True)
        return contador


def _sumar_contador_chemi(discord_id: str, nombre: Optional[str], cantidad: int) -> tuple[int, int]:
    anterior = _get_contador_chemi(str(discord_id))
    nuevo = _set_contador_chemi(str(discord_id), nombre, anterior + max(0, int(cantidad or 0)))
    return anterior, nuevo


def _reducir_contador_chemi(discord_id: str, nombre: Optional[str], cantidad: int) -> tuple[int, int, int]:
    anterior = _get_contador_chemi(str(discord_id))
    aplicado = min(anterior, max(0, int(cantidad or 0)))
    nuevo = _set_contador_chemi(str(discord_id), nombre, anterior - aplicado)
    return anterior, aplicado, nuevo


def _pistolas_retiradas_sin_profit(discord_id: str) -> int:
    return _get_contador_chemi(str(discord_id))


def _sincronizar_deuda_db(discord_id: str, nombre: Optional[str], contador: int) -> None:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if contador >= LIMITE_PISTOLAS_DIA:
            cursor.execute(
                """
                INSERT INTO chemi_deudas
                    (discord_id, nombre, pistolas_retiradas, debe_devolver,
                     activa, created_at, deadline, aviso_altos_cargos, cancelada_at)
                VALUES (%s, %s, %s, %s, TRUE, NOW(), NULL, FALSE, NULL)
                ON CONFLICT (discord_id) DO UPDATE
                SET nombre = COALESCE(EXCLUDED.nombre, chemi_deudas.nombre),
                    pistolas_retiradas = EXCLUDED.pistolas_retiradas,
                    debe_devolver = EXCLUDED.debe_devolver,
                    activa = TRUE,
                    cancelada_at = NULL
                """,
                (str(discord_id), nombre, contador, max(0, contador - LIMITE_PISTOLAS_DIA + 1)),
            )
        else:
            cursor.execute(
                """
                UPDATE chemi_deudas
                SET activa = FALSE, cancelada_at = NOW()
                WHERE discord_id = %s AND activa = TRUE
                """,
                (str(discord_id),),
            )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ [Chemi] Error sincronizando deuda: {e}", exc_info=True)


def _crear_credito_db(discord_id: str, nombre: str, cantidad: int) -> Optional[int]:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO chemi_creditos
                (owner_discord_id, owner_nombre, cantidad_total, cantidad_restante, estado, created_at, updated_at)
            VALUES (%s, %s, %s, %s, 'pendiente', NOW(), NOW())
            RETURNING id
            """,
            (str(discord_id), nombre, int(cantidad), int(cantidad)),
        )
        row = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()
        return int(row["id"]) if row else None
    except Exception as e:
        logger.error(f"❌ [Chemi] Error creando crédito: {e}", exc_info=True)
        return None


def _actualizar_credito_mensaje(credito_id: int, channel_id: int, message_id: int) -> None:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE chemi_creditos SET channel_id = %s, message_id = %s, updated_at = NOW() WHERE id = %s",
            (int(channel_id), int(message_id), int(credito_id)),
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ [Chemi] Error ligando mensaje de crédito: {e}", exc_info=True)


def _get_credito_by_message(message_id: int) -> Optional[dict]:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, owner_discord_id, owner_nombre, cantidad_total, cantidad_restante, estado,
                   message_id, channel_id, created_at, updated_at
            FROM chemi_creditos
            WHERE message_id = %s
            ORDER BY id DESC LIMIT 1
            """,
            (int(message_id),),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"❌ [Chemi] Error obteniendo crédito por mensaje: {e}", exc_info=True)
        return None


def _get_credito_by_id(credito_id: int) -> Optional[dict]:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, owner_discord_id, owner_nombre, cantidad_total, cantidad_restante, estado,
                   message_id, channel_id, created_at, updated_at
            FROM chemi_creditos
            WHERE id = %s
            """,
            (int(credito_id),),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"❌ [Chemi] Error obteniendo crédito por id: {e}", exc_info=True)
        return None


def _listar_creditos_db(discord_id: str) -> list[dict]:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, owner_discord_id, owner_nombre, cantidad_total, cantidad_restante, estado,
                   created_at, updated_at
            FROM chemi_creditos
            WHERE owner_discord_id = %s AND cantidad_restante > 0 AND estado = 'pendiente'
            ORDER BY created_at DESC
            LIMIT 10
            """,
            (str(discord_id),),
        )
        rows = cursor.fetchall() or []
        cursor.close()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"❌ [Chemi] Error listando créditos: {e}", exc_info=True)
        return []


def _stats_creditos_pendientes() -> tuple[int, int]:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) AS creditos, COALESCE(SUM(cantidad_restante), 0) AS pipas
            FROM chemi_creditos
            WHERE estado = 'pendiente' AND cantidad_restante > 0
            """
        )
        row = cursor.fetchone() or {}
        cursor.close()
        conn.close()
        return int(row.get("creditos") or 0), int(row.get("pipas") or 0)
    except Exception as e:
        logger.error(f"❌ [Chemi] Error consultando stats créditos: {e}", exc_info=True)
        return 0, 0


def _consumir_credito_db(
    credito_id: int,
    owner_discord_id: str,
    target_discord_id: Optional[str],
    target_nombre: Optional[str],
    cantidad: int,
    tipo: str,
    actor_discord_id: str,
) -> dict:
    cantidad = max(1, int(cantidad or 1))
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, owner_discord_id, cantidad_restante
            FROM chemi_creditos
            WHERE id = %s AND owner_discord_id = %s AND estado = 'pendiente'
            FOR UPDATE
            """,
            (int(credito_id), str(owner_discord_id)),
        )
        credito = cursor.fetchone()
        if not credito:
            conn.rollback()
            cursor.close()
            conn.close()
            return {"ok": False, "motivo": "Crédito no disponible."}

        restante = int(credito.get("cantidad_restante") or 0)
        if restante <= 0:
            conn.rollback()
            cursor.close()
            conn.close()
            return {"ok": False, "motivo": "El crédito ya no tiene pipas disponibles."}
        if cantidad > restante:
            conn.rollback()
            cursor.close()
            conn.close()
            return {"ok": False, "motivo": f"Solo quedan {restante} pipa(s) en este crédito."}

        aplicado = cantidad
        anterior = nuevo = None
        if tipo in {"self", "transfer"}:
            if not target_discord_id:
                conn.rollback()
                cursor.close()
                conn.close()
                return {"ok": False, "motivo": "Falta el usuario destino."}
            cursor.execute(
                "SELECT contador FROM chemi_contadores WHERE discord_id = %s FOR UPDATE",
                (str(target_discord_id),),
            )
            row_contador = cursor.fetchone()
            anterior = int((row_contador or {}).get("contador") or 0)
            if anterior <= 0:
                conn.rollback()
                cursor.close()
                conn.close()
                return {"ok": False, "motivo": "Ese usuario tiene contador en 0; no se consumió crédito."}
            aplicado = min(cantidad, anterior)
            nuevo = anterior - aplicado
            cursor.execute(
                """
                INSERT INTO chemi_contadores (discord_id, nombre, contador, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (discord_id) DO UPDATE SET
                    nombre = COALESCE(EXCLUDED.nombre, chemi_contadores.nombre),
                    contador = EXCLUDED.contador,
                    updated_at = NOW()
                """,
                (str(target_discord_id), target_nombre, nuevo),
            )

        nuevo_restante = restante - aplicado
        estado = "agotado" if nuevo_restante <= 0 else "pendiente"
        cursor.execute(
            """
            UPDATE chemi_creditos
            SET cantidad_restante = %s, estado = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (nuevo_restante, estado, int(credito_id)),
        )
        cursor.execute(
            """
            INSERT INTO chemi_transferencias
                (credito_id, from_discord_id, to_discord_id, cantidad, tipo, actor_discord_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (int(credito_id), str(owner_discord_id), str(target_discord_id or ""), aplicado, tipo, str(actor_discord_id)),
        )
        conn.commit()
        cursor.close()
        conn.close()
        return {
            "ok": True,
            "aplicado": aplicado,
            "restante": nuevo_restante,
            "contador_anterior": anterior,
            "contador_nuevo": nuevo,
            "agotado": nuevo_restante <= 0,
        }
    except Exception as e:
        logger.error(f"❌ [Chemi] Error consumiendo crédito: {e}", exc_info=True)
        try:
            conn.rollback()
            cursor.close()
            conn.close()
        except Exception:
            pass
        return {"ok": False, "motivo": "Error interno consumiendo el crédito."}


def _tiene_deuda_activa_db(discord_id: str) -> bool:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM chemi_deudas WHERE discord_id = %s AND activa = TRUE LIMIT 1",
            (discord_id,),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return bool(row)
    except Exception as e:
        logger.error(f"❌ [Chemi] Error consultando deuda activa: {e}", exc_info=True)
        return False


def _registrar_deuda_db(
    discord_id: str,
    nombre: str,
    cantidad_retirada: int,
    debe_devolver: int,
) -> Optional[int]:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO chemi_deudas
                (discord_id, nombre, pistolas_retiradas, debe_devolver,
                 activa, created_at, deadline, aviso_altos_cargos, cancelada_at)
            VALUES (%s, %s, %s, %s, TRUE, NOW(), NOW() + INTERVAL '48 hours', FALSE, NULL)
            ON CONFLICT (discord_id) DO UPDATE
            SET pistolas_retiradas = EXCLUDED.pistolas_retiradas,
                debe_devolver      = EXCLUDED.debe_devolver,
                activa             = TRUE,
                created_at         = NOW(),
                deadline           = NOW() + INTERVAL '48 hours',
                aviso_altos_cargos = FALSE,
                cancelada_at       = NULL
            RETURNING id
            """,
            (discord_id, nombre, cantidad_retirada, debe_devolver),
        )
        row = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()
        return int(row["id"]) if row else None
    except Exception as e:
        logger.error(f"❌ [Chemi] Error registrando deuda: {e}", exc_info=True)
        return None


def _cancelar_deuda_db(discord_id: str) -> bool:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE chemi_deudas
            SET activa = FALSE, cancelada_at = NOW()
            WHERE discord_id = %s AND activa = TRUE
            """,
            (discord_id,),
        )
        afectados = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()
        return afectados > 0
    except Exception as e:
        logger.error(f"❌ [Chemi] Error cancelando deuda: {e}", exc_info=True)
        return False


def _get_deuda_info(discord_id: str) -> Optional[dict]:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, discord_id, nombre, pistolas_retiradas, debe_devolver,
                   created_at, deadline, aviso_altos_cargos
            FROM chemi_deudas
            WHERE discord_id = %s AND activa = TRUE LIMIT 1
            """,
            (discord_id,),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"❌ [Chemi] Error obteniendo deuda: {e}", exc_info=True)
        return None


def _marcar_aviso_altos_cargos(discord_id: str) -> None:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE chemi_deudas SET aviso_altos_cargos = TRUE WHERE discord_id = %s AND activa = TRUE",
            (discord_id,),
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ [Chemi] Error marcando aviso altos cargos: {e}", exc_info=True)


async def _asignar_rol_deuda(guild: discord.Guild, discord_id: int) -> bool:
    if not DEUDA_CHEMI_ROLE_ID:
        return False
    try:
        member = guild.get_member(discord_id)
        if not member:
            try:
                member = await guild.fetch_member(discord_id)
            except Exception:
                return False
        if not member:
            return False
        role = guild.get_role(DEUDA_CHEMI_ROLE_ID)
        if not role:
            logger.warning(f"⚠️ [Chemi] Rol de deuda {DEUDA_CHEMI_ROLE_ID} no encontrado")
            return False
        if any(r.id == DEUDA_CHEMI_ROLE_ID for r in member.roles):
            return True
        await member.add_roles(role, reason="Deuda con armero chemi - no devolvió pistolas en 48h")
        logger.info(f"✅ [Chemi] Rol de deuda asignado a {member} ({discord_id})")
        return True
    except Exception as e:
        logger.error(f"❌ [Chemi] Error asignando rol de deuda: {e}", exc_info=True)
        return False


async def _quitar_rol_deuda(guild: discord.Guild, discord_id: int) -> bool:
    if not DEUDA_CHEMI_ROLE_ID:
        return False
    try:
        member = guild.get_member(discord_id)
        if not member:
            try:
                member = await guild.fetch_member(discord_id)
            except Exception:
                return False
        if not member:
            return False
        role = guild.get_role(DEUDA_CHEMI_ROLE_ID)
        if not role:
            return False
        if not any(r.id == DEUDA_CHEMI_ROLE_ID for r in member.roles):
            return True
        await member.remove_roles(role, reason="Deuda con armero chemi saldada")
        logger.info(f"✅ [Chemi] Rol de deuda quitado de {member} ({discord_id})")
        return True
    except Exception as e:
        logger.error(f"❌ [Chemi] Error quitando rol de deuda: {e}", exc_info=True)
        return False


async def _sincronizar_rol_deuda_por_contador(guild: Optional[discord.Guild], discord_id: int, nombre: Optional[str] = None) -> None:
    if not guild:
        return
    contador = _get_contador_chemi(str(discord_id))
    _sincronizar_deuda_db(str(discord_id), nombre, contador)
    if contador >= LIMITE_PISTOLAS_DIA:
        await _asignar_rol_deuda(guild, int(discord_id))
    else:
        await _quitar_rol_deuda(guild, int(discord_id))


async def _enviar_dm_limite(discord_id: int, contador: int) -> None:
    if not _bot:
        return
    try:
        user = _bot.get_user(discord_id) or await _bot.fetch_user(discord_id)
        if not user:
            return
        await user.send(
            f"⚠️ Llegaste al límite del armario Chemi: `{contador}/{LIMITE_PISTOLAS_DIA}` pipas. "
            "Se te asignó el rol de deuda hasta bajar el contador."
        )
    except Exception as e:
        logger.info(f"ℹ️ [Chemi] No se pudo enviar DM de límite a {discord_id}: {e}")


def _build_limite_embed(member, contador: int, creditos: list[dict], tiene_rol_deuda: bool) -> discord.Embed:
    usados = min(contador, LIMITE_PISTOLAS_DIA)
    barra = "🟩" * usados + "⬛" * max(0, LIMITE_PISTOLAS_DIA - usados)
    estado = "🔴 Bloqueado" if contador >= LIMITE_PISTOLAS_DIA or tiene_rol_deuda else "✅ Disponible"
    total_creditos = sum(int(c.get("cantidad_restante") or 0) for c in creditos)

    embed = discord.Embed(
        title=f"🔫 Límite Chemi - {getattr(member, 'display_name', str(member))}",
        color=discord.Color.red() if estado.startswith("🔴") else discord.Color.teal(),
        timestamp=datetime.now(),
    )
    embed.add_field(name="📊 Contador", value=f"{barra} `{contador}/{LIMITE_PISTOLAS_DIA}`", inline=False)
    embed.add_field(name="🔒 Estado", value=estado, inline=True)
    embed.add_field(name="🎟️ Créditos pendientes", value=f"{total_creditos} pipa(s)", inline=True)
    if creditos:
        lineas = []
        for c in creditos[:5]:
            created = c.get("created_at")
            fecha = created.strftime("%d/%m %H:%M") if created else "N/A"
            lineas.append(f"#{c['id']} · {c['cantidad_restante']}/{c['cantidad_total']} · {fecha}")
        embed.add_field(name="Créditos", value="\n".join(lineas), inline=False)
    return embed


class ChemiCantidadModal(discord.ui.Modal):
    def __init__(self, accion: str, credito_id: int):
        titulo = "Usar crédito Chemi" if accion == "self" else "Dar crédito Chemi"
        super().__init__(title=titulo)
        self.accion = accion
        self.credito_id = int(credito_id)
        self.cantidad = discord.ui.TextInput(
            label="Cantidad de pipas",
            placeholder="Ej: 1",
            required=True,
            max_length=4,
        )
        self.add_item(self.cantidad)
        self.target_id = None
        if accion == "transfer":
            self.target_id = discord.ui.TextInput(
                label="ID Discord del jugador",
                placeholder="123456789012345678",
                required=True,
                max_length=24,
            )
            self.add_item(self.target_id)

    async def on_submit(self, interaction: discord.Interaction):
        credito = _get_credito_by_id(self.credito_id)
        if not credito:
            await interaction.response.send_message("❌ Este crédito no está disponible.", ephemeral=True)
            return
        if str(interaction.user.id) != str(credito.get("owner_discord_id")):
            await interaction.response.send_message("⛔ Solo quien depositó estas pipas puede usar este crédito.", ephemeral=True)
            return
        try:
            cantidad = int(str(self.cantidad.value).strip())
        except (TypeError, ValueError):
            await interaction.response.send_message("❌ La cantidad debe ser un número.", ephemeral=True)
            return
        if cantidad <= 0:
            await interaction.response.send_message("❌ La cantidad debe ser mayor a 0.", ephemeral=True)
            return

        target_id = str(interaction.user.id)
        target_nombre = getattr(interaction.user, "display_name", interaction.user.name)
        if self.accion == "transfer":
            raw_target = str(self.target_id.value if self.target_id else "").strip().replace("<@", "").replace(">", "").replace("!", "")
            if not raw_target.isdigit():
                await interaction.response.send_message("❌ ID Discord destino inválido.", ephemeral=True)
                return
            target_id = raw_target
            target_nombre = None
            if interaction.guild:
                member = interaction.guild.get_member(int(target_id))
                if not member:
                    try:
                        member = await interaction.guild.fetch_member(int(target_id))
                    except Exception:
                        member = None
                if member:
                    target_nombre = member.display_name

        result = _consumir_credito_db(
            int(credito["id"]),
            str(credito["owner_discord_id"]),
            target_id,
            target_nombre,
            cantidad,
            self.accion,
            str(interaction.user.id),
        )
        if not result.get("ok"):
            await interaction.response.send_message(f"⚠️ {result.get('motivo', 'No se pudo usar el crédito.')}", ephemeral=True)
            return

        if interaction.guild and target_id:
            await _sincronizar_rol_deuda_por_contador(interaction.guild, int(target_id), target_nombre)

        if credito.get("message_id") and credito.get("channel_id") and _bot:
            try:
                channel = _bot.get_channel(int(credito["channel_id"]))
                if channel:
                    msg = await channel.fetch_message(int(credito["message_id"]))
                    await _actualizar_mensaje_credito(msg, result.get("restante", 0))
            except Exception as e:
                logger.warning(f"⚠️ [Chemi] No se pudo actualizar mensaje de crédito {credito.get('id')}: {e}")
        destino = "tu contador" if self.accion == "self" else f"<@{target_id}>"
        await interaction.response.send_message(
            f"✅ Se usaron `{result['aplicado']}` pipa(s) para bajar {destino}. "
            f"Crédito restante: `{result['restante']}`.",
            ephemeral=True,
        )


class ChemiCreditoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Usar en mí", style=discord.ButtonStyle.success, custom_id="chemi_credito_self")
    async def usar_en_mi(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.message:
            await interaction.response.send_message("❌ No se encontró el mensaje de crédito.", ephemeral=True)
            return
        credito = _get_credito_by_message(interaction.message.id)
        if not credito:
            await interaction.response.send_message("❌ Este crédito no está disponible.", ephemeral=True)
            return
        if str(interaction.user.id) != str(credito.get("owner_discord_id")):
            await interaction.response.send_message("⛔ Solo quien depositó estas pipas puede usar este crédito.", ephemeral=True)
            return
        await interaction.response.send_modal(ChemiCantidadModal("self", int(credito["id"])))

    @discord.ui.button(label="Dar a otro jugador", style=discord.ButtonStyle.primary, custom_id="chemi_credito_transfer")
    async def dar_a_otro(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.message:
            await interaction.response.send_message("❌ No se encontró el mensaje de crédito.", ephemeral=True)
            return
        credito = _get_credito_by_message(interaction.message.id)
        if not credito:
            await interaction.response.send_message("❌ Este crédito no está disponible.", ephemeral=True)
            return
        if str(interaction.user.id) != str(credito.get("owner_discord_id")):
            await interaction.response.send_message("⛔ Solo quien depositó estas pipas puede usar este crédito.", ephemeral=True)
            return
        await interaction.response.send_modal(ChemiCantidadModal("transfer", int(credito["id"])))

    @discord.ui.button(label="Dejar en armario", style=discord.ButtonStyle.secondary, custom_id="chemi_credito_armario")
    async def dejar_armario(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.message:
            await interaction.response.send_message("❌ No se encontró el mensaje de crédito.", ephemeral=True)
            return
        credito = _get_credito_by_message(interaction.message.id)
        if not credito:
            await interaction.response.send_message("❌ Este crédito no está disponible.", ephemeral=True)
            return
        if str(interaction.user.id) != str(credito.get("owner_discord_id")):
            await interaction.response.send_message("⛔ Solo quien depositó estas pipas puede cerrar este crédito.", ephemeral=True)
            return
        restante = int(credito.get("cantidad_restante") or 0)
        if restante <= 0:
            await interaction.response.send_message("ℹ️ Este crédito ya está agotado.", ephemeral=True)
            return
        result = _consumir_credito_db(
            int(credito["id"]),
            str(credito["owner_discord_id"]),
            None,
            None,
            restante,
            "armario",
            str(interaction.user.id),
        )
        if not result.get("ok"):
            await interaction.response.send_message(f"⚠️ {result.get('motivo', 'No se pudo cerrar el crédito.')}", ephemeral=True)
            return
        await _actualizar_mensaje_credito(interaction.message, 0)
        await interaction.response.send_message("✅ Las pipas restantes quedaron para el armario.", ephemeral=True)


class ChemiLimitPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Ver mi límite", style=discord.ButtonStyle.primary, custom_id="chemi_limit_panel_view")
    async def ver_mi_limite(self, interaction: discord.Interaction, button: discord.ui.Button):
        contador = _get_contador_chemi(str(interaction.user.id))
        creditos = _listar_creditos_db(str(interaction.user.id))
        tiene_rol = bool(getattr(interaction.user, "roles", None)) and tiene_deuda_chemi(interaction.user)
        embed = _build_limite_embed(interaction.user, contador, creditos, tiene_rol)

        # Si tiene créditos, adjuntar view con el primero disponible
        if creditos:
            primer_credito = creditos[0]
            view = ChemiCreditosView(
                credito_id=int(primer_credito["id"]),
                owner_id=int(interaction.user.id),
                restante=int(primer_credito.get("cantidad_restante") or 0),
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def _actualizar_mensaje_credito(message: discord.Message, restante: int) -> None:
    try:
        if not message.embeds:
            if restante <= 0:
                await message.edit(view=None)
            return
        embed = message.embeds[0]
        reemplazado = False
        for idx, field in enumerate(embed.fields):
            if field.name.lower().startswith("crédito restante"):
                embed.set_field_at(idx, name="Crédito restante", value=f"{max(0, int(restante))} pipa(s)", inline=True)
                reemplazado = True
                break
        if not reemplazado:
            embed.add_field(name="Crédito restante", value=f"{max(0, int(restante))} pipa(s)", inline=True)
        if restante <= 0:
            embed.color = discord.Color.green()
            embed.add_field(name="Estado", value="✅ Crédito agotado/cerrado", inline=False)
            await message.edit(embed=embed, view=None)
        else:
            await message.edit(embed=embed, view=ChemiCreditoView())
    except Exception as e:
        logger.error(f"❌ [Chemi] Error actualizando mensaje de crédito: {e}", exc_info=True)


async def _avisar_limite_alcanzado(discord_id: int, nombre: str, pistolas_hoy: int):
    # Embed reutilizable
    embed = discord.Embed(
        title="⚠️ Límite diario alcanzado - Armario Chemi",
        color=discord.Color.orange(),
        timestamp=datetime.now(),
    )
    embed.add_field(name="👤 Usuario", value=f"<@{discord_id}>", inline=True)
    embed.add_field(name="🏷️ Nombre IC", value=nombre or "N/A", inline=True)
    embed.add_field(name="🔫 Pistolas hoy", value=f"{pistolas_hoy}/{LIMITE_PISTOLAS_DIA}", inline=True)
    embed.add_field(
        name="ℹ️ Info",
        value=(
            f"Alcanzaste el límite de **{LIMITE_PISTOLAS_DIA} pipas** "
            "del armario chemi.\n"
            "El contador baja usando créditos de depósitos o con `/chemi_limite_reset`."
        ),
        inline=False,
    )
    embed.set_footer(text="Sistema Chemi - ArmamentBot")

    # 1) Enviar por DM
    if _bot:
        try:
            user = _bot.get_user(discord_id) or await _bot.fetch_user(discord_id)
            if user:
                await user.send(embed=embed)
                logger.info(f"✅ [Chemi] DM límite enviado a {discord_id}")
        except Exception as e:
            logger.info(f"ℹ️ [Chemi] No se pudo enviar DM de límite a {discord_id}: {e}")

    # 2) Enviar al canal de avisos SIN arrobar, se borra al minuto
    if not CHEMI_AVISO_CHANNEL_ID or not _bot:
        return
    try:
        channel = _bot.get_channel(CHEMI_AVISO_CHANNEL_ID)
        if not channel:
            return
        # Sin content para no arrobar al usuario
        msg = await channel.send(embed=embed)
        # Borrar después de 60 segundos
        async def _borrar_despues():
            await asyncio.sleep(60)
            try:
                await msg.delete()
            except Exception:
                pass
        asyncio.create_task(_borrar_despues())
    except Exception as e:
        logger.error(f"❌ [Chemi] Error avisando límite en canal: {e}", exc_info=True)


async def _avisar_deuda_altos_cargos(discord_id: int, nombre: str, deuda_info: dict):
    if not ALTOS_CARGOS_CHANNEL_ID or not _bot:
        return
    try:
        channel = _bot.get_channel(ALTOS_CARGOS_CHANNEL_ID)
        if not channel:
            return
        deadline = deuda_info.get("deadline")
        deadline_txt = deadline.strftime("%d/%m/%Y %H:%M") if deadline else "N/A"
        embed = discord.Embed(
            title="🚨 Usuario retira con DEUDA activa - Armario Chemi",
            color=discord.Color.dark_red(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="👤 Usuario", value=f"<@{discord_id}>", inline=True)
        embed.add_field(name="🏷️ Nombre IC", value=nombre or "N/A", inline=True)
        embed.add_field(name="🔫 Debe devolver", value=f"{deuda_info.get('debe_devolver', '?')} pipas", inline=True)
        embed.add_field(name="⏰ Deadline", value=deadline_txt, inline=True)
        embed.add_field(
            name="⚠️ Situación",
            value=(
                "El usuario sigue retirando armas del armario chemi "
                "sin haber saldado su deuda anterior.\n"
                f"Se le asignó el rol <@&{DEUDA_CHEMI_ROLE_ID}> automáticamente.\n"
                "Usá `/chemi_deuda_saldar` para saldar la deuda manualmente."
            ),
            inline=False,
        )
        embed.set_footer(text="Sistema Chemi - ArmamentBot")
        await channel.send(embed=embed)
        _marcar_aviso_altos_cargos(str(discord_id))
    except Exception as e:
        logger.error(f"❌ [Chemi] Error avisando altos cargos: {e}", exc_info=True)


async def _avisar_deuda_registrada(discord_id: int, nombre: str, falta: int):
    if not CHEMI_AVISO_CHANNEL_ID or not _bot:
        return
    try:
        channel = _bot.get_channel(CHEMI_AVISO_CHANNEL_ID)
        if not channel:
            return
        embed = discord.Embed(
            title="⏰ Deuda registrada - 48h sin profit - Armario Chemi",
            color=discord.Color.red(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="👤 Usuario", value=f"<@{discord_id}>", inline=True)
        embed.add_field(name="🏷️ Nombre IC", value=nombre, inline=True)
        embed.add_field(name="🔫 Pistolas faltantes", value=str(falta), inline=True)
        embed.add_field(
            name="ℹ️ Situación",
            value=(
                "No devolvió el profit en **48 horas**.\n"
                f"Se asignó el rol <@&{DEUDA_CHEMI_ROLE_ID}>.\n"
                "El armario queda bloqueado hasta saldar la deuda.\n"
                "Usá `/chemi_deuda_saldar` para saldar manualmente."
            ),
            inline=False,
        )
        embed.set_footer(text="Sistema Chemi - ArmamentBot")
        await channel.send(embed=embed)
    except Exception as e:
        logger.error(f"❌ [Chemi] Error avisando deuda registrada: {e}", exc_info=True)


async def _enviar_credito_deposito(discord_id: int, nombre: str, cantidad: int) -> None:
    if not _bot:
        logger.warning("⚠️ [Chemi] _bot es None en _enviar_credito_deposito")
        return

    credito_id = _crear_credito_db(str(discord_id), nombre, cantidad)
    if not credito_id:
        logger.error(f"❌ [Chemi] _crear_credito_db devolvió None para discord_id={discord_id}")
        return

    logger.info(f"✅ [Chemi] Crédito #{credito_id} creado en BD para {discord_id} ({cantidad} pipas)")

    try:
        user = _bot.get_user(int(discord_id)) or await _bot.fetch_user(int(discord_id))
        if not user:
            logger.warning(f"⚠️ [Chemi] No se encontró el usuario {discord_id} para enviar DM")
        contador = _get_contador_chemi(str(discord_id))
        embed = discord.Embed(
            title="🎟️ Crédito Chemi disponible",
            description=(
                f"Depositaste **{cantidad} pipa(s)**.\n"
                "Podés usar este crédito para bajar tu contador o el de otro jugador."
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="👤 Usuario", value=f"<@{discord_id}>", inline=True)
        embed.add_field(name="🏷️ Nombre IC", value=nombre or "N/A", inline=True)
        embed.add_field(name="📊 Tu contador", value=f"{contador}/{LIMITE_PISTOLAS_DIA}", inline=True)
        embed.add_field(name="Crédito restante", value=f"{cantidad} pipa(s)", inline=True)
        embed.set_footer(text=f"Crédito #{credito_id} | Sistema Chemi")
        if user:
            try:
                dm = await user.create_dm()
                msg = await dm.send(embed=embed, view=ChemiCreditoView())
                _actualizar_credito_mensaje(credito_id, msg.channel.id, msg.id)
                logger.info(f"✅ [Chemi] DM enviado a {discord_id} para crédito #{credito_id}")
                return
            except Exception as e:
                logger.warning(f"⚠️ [Chemi] DM falló para {discord_id}: {e} — intentando canal fallback")

        if CHEMI_AVISO_CHANNEL_ID:
            try:
                channel = _bot.get_channel(CHEMI_AVISO_CHANNEL_ID)
                if not channel:
                    logger.error(f"❌ [Chemi] Canal fallback {CHEMI_AVISO_CHANNEL_ID} no encontrado")
                else:
                    msg = await channel.send(content=f"<@{discord_id}>", embed=embed, view=ChemiCreditoView())
                    _actualizar_credito_mensaje(credito_id, channel.id, msg.id)
                    logger.info(f"✅ [Chemi] Crédito enviado por canal fallback #{credito_id} a {discord_id}")
            except Exception as e:
                logger.error(f"❌ [Chemi] Error enviando crédito (fallback canal): {e}", exc_info=True)
        else:
            logger.error(f"❌ [Chemi] Sin canal fallback configurado y DM falló para {discord_id}")
    except Exception as e:
        logger.error(f"❌ [Chemi] Error general en _enviar_credito_deposito: {e}", exc_info=True)

# ── Panel de créditos unificado (DM + canal público) ─────────────────────────
# Se usa tanto en /chemi_creditos como en el botón "Ver mi límite"
# Diseño anti-exploit:
#   - owner_id embebido, verificado en cada callback
#   - saldo re-leído de BD antes de operar
#   - lock suave _en_uso para evitar doble-click
#   - UserSelect para elegir destino (más cómodo en server)
#   - Botón alternativo por Discord ID para casos edge

class ChemiCreditosView(discord.ui.View):
    def __init__(self, credito_id: int, owner_id: int, restante: int):
        super().__init__(timeout=None)
        self.credito_id = int(credito_id)
        self.owner_id   = int(owner_id)
        self.restante   = int(restante)
        self._en_uso    = False

    def _lock(self):
        self._en_uso = True
        for child in self.children:
            child.disabled = True

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "⛔ Solo el dueño del crédito puede usar estos botones.",
                ephemeral=True,
            )
            return False
        if self._en_uso:
            await interaction.response.send_message(
                "⏳ Ya hay una operación en curso para este crédito.",
                ephemeral=True,
            )
            return False
        credito = _get_credito_by_id(self.credito_id)
        if not credito or int(credito.get("cantidad_restante") or 0) <= 0:
            await interaction.response.send_message(
                "ℹ️ Este crédito ya está agotado.",
                ephemeral=True,
            )
            self._lock()
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass
            return False
        return True

    @discord.ui.button(
        label="⬇️ Usar en mí",
        style=discord.ButtonStyle.success,
        custom_id="chemi_cred_v2_self",
    )
    async def usar_en_mi(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        # NO lockear aquí — el modal es exclusivo por naturaleza
        # El lock solo aplica si la operación realmente se completa (en on_submit)
        await interaction.response.send_modal(
            _ChemiCreditosSelfModal(self.credito_id, self.owner_id)
        )


    @discord.ui.button(
        label="🤝 Dar a un amigo",
        style=discord.ButtonStyle.primary,
        custom_id="chemi_cred_v2_friend",
    )
    async def dar_a_amigo(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        # NO lockear — el UserSelect es efímero y no bloquea otros clicks
        await interaction.response.send_message(
            "👇 Seleccioná el jugador al que querés dar el crédito:",
            view=_ChemiUserSelectView(self.credito_id, self.owner_id),
            ephemeral=True,
        )

    @discord.ui.button(
        label="🆔 Por Discord ID",
        style=discord.ButtonStyle.secondary,
        custom_id="chemi_cred_v2_byid",
    )
    async def por_discord_id(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        # NO lockear — el modal es exclusivo por naturaleza
        await interaction.response.send_modal(
            _ChemiCreditosByIDModal(self.credito_id, self.owner_id)
        )


# ── Modal: usar en mí mismo ──────────────────────────────────────────────────

class _ChemiCreditosSelfModal(discord.ui.Modal, title="Usar crédito en mí mismo"):
    cantidad = discord.ui.TextInput(
        label="Pipas a descontar de tu contador",  # era: "¿Cuántas pipas querés descontar de tu contador?" → 48 chars
        placeholder="Ej: 1",
        required=True,
        max_length=4,
    )

    def __init__(self, credito_id: int, owner_id: int):
        super().__init__()
        self.credito_id = int(credito_id)
        self.owner_id   = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction):
        # Verificar owner nuevamente (el modal puede llegar tarde)
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        try:
            cantidad = int(str(self.cantidad.value).strip())
        except (TypeError, ValueError):
            await interaction.response.send_message("❌ La cantidad debe ser un número entero.", ephemeral=True)
            return
        if cantidad <= 0:
            await interaction.response.send_message("❌ La cantidad debe ser mayor a 0.", ephemeral=True)
            return

        credito = _get_credito_by_id(self.credito_id)
        if not credito or str(credito.get("owner_discord_id")) != str(self.owner_id):
            await interaction.response.send_message("❌ Crédito no disponible.", ephemeral=True)
            return

        # Verificar saldo actual
        restante_actual = int(credito.get("cantidad_restante") or 0)
        if restante_actual <= 0:
            await interaction.response.send_message("ℹ️ Este crédito ya está agotado.", ephemeral=True)
            return
        if cantidad > restante_actual:
            await interaction.response.send_message(
                f"⚠️ Solo tenés `{restante_actual}` pipa(s) disponibles en este crédito.",
                ephemeral=True,
            )
            return

        result = _consumir_credito_db(
            int(credito["id"]),
            str(credito["owner_discord_id"]),
            str(interaction.user.id),
            getattr(interaction.user, "display_name", interaction.user.name),
            cantidad,
            "self",
            str(interaction.user.id),
        )
        if not result.get("ok"):
            await interaction.response.send_message(
                f"⚠️ {result.get('motivo', 'No se pudo usar el crédito.')}",
                ephemeral=True,
            )
            return

        if interaction.guild:
            await _sincronizar_rol_deuda_por_contador(
                interaction.guild,
                interaction.user.id,
                getattr(interaction.user, "display_name", None),
            )

        await interaction.response.send_message(
            f"✅ Se descontaron **{result['aplicado']}** pipa(s) de tu contador.\n"
            f"Crédito restante: `{result['restante']}`.",
            ephemeral=True,
        )


# ── View con UserSelect ──────────────────────────────────────────────────────

class _ChemiUserSelect(discord.ui.UserSelect):
    def __init__(self, credito_id: int, owner_id: int):
        super().__init__(
            placeholder="Seleccioná el jugador…",
            min_values=1,
            max_values=1,
            custom_id="chemi_cred_v2_user_select",
        )
        self.credito_id = int(credito_id)
        self.owner_id   = int(owner_id)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "⛔ Solo el dueño del crédito puede hacer esto.",
                ephemeral=True,
            )
            return

        target = self.values[0]

        # Anti-exploit: no darse a uno mismo por esta vía
        if target.id == self.owner_id:
            await interaction.response.send_message(
                "❌ No podés darte el crédito a vos mismo con esta opción.\n"
                "Usá **⬇️ Usar en mí**.",
                ephemeral=True,
            )
            return
        if target.bot:
            await interaction.response.send_message(
                "❌ No podés transferirle crédito a un bot.",
                ephemeral=True,
            )
            return

        # Verificar que el crédito sigue disponible
        credito = _get_credito_by_id(self.credito_id)
        if not credito or int(credito.get("cantidad_restante") or 0) <= 0:
            await interaction.response.send_message(
                "ℹ️ Este crédito ya está agotado.",
                ephemeral=True,
            )
            return
        if str(credito.get("owner_discord_id")) != str(self.owner_id):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        self.disabled = True
        try:
            await interaction.message.edit(view=self.view)
        except Exception:
            pass

        await interaction.response.send_modal(
            _ChemiCreditosFriendModal(
                self.credito_id,
                self.owner_id,
                target.id,
                target.display_name,
            )
        )


class _ChemiUserSelectView(discord.ui.View):
    def __init__(self, credito_id: int, owner_id: int):
        super().__init__(timeout=120)
        self.add_item(_ChemiUserSelect(credito_id, owner_id))


# ── Modal: dar a un amigo (desde UserSelect) ─────────────────────────────────

class _ChemiCreditosFriendModal(discord.ui.Modal, title="Dar crédito a un amigo"):
    cantidad = discord.ui.TextInput(
        label="¿Cuántas pipas querés darle?",
        placeholder="Ej: 1",
        required=True,
        max_length=4,
    )

    def __init__(self, credito_id: int, owner_id: int, target_id: int, target_nombre: str):
        super().__init__()
        self.credito_id    = int(credito_id)
        self.owner_id      = int(owner_id)
        self.target_id     = int(target_id)
        self.target_nombre = target_nombre

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        # Anti-exploit: verificar que el target no sea el mismo owner
        if self.target_id == self.owner_id:
            await interaction.response.send_message(
                "❌ No podés darte el crédito a vos mismo.", ephemeral=True
            )
            return
        try:
            cantidad = int(str(self.cantidad.value).strip())
        except (TypeError, ValueError):
            await interaction.response.send_message("❌ La cantidad debe ser un número entero.", ephemeral=True)
            return
        if cantidad <= 0:
            await interaction.response.send_message("❌ La cantidad debe ser mayor a 0.", ephemeral=True)
            return

        credito = _get_credito_by_id(self.credito_id)
        if not credito or str(credito.get("owner_discord_id")) != str(self.owner_id):
            await interaction.response.send_message("❌ Crédito no disponible.", ephemeral=True)
            return

        restante_actual = int(credito.get("cantidad_restante") or 0)
        if restante_actual <= 0:
            await interaction.response.send_message("ℹ️ Este crédito ya está agotado.", ephemeral=True)
            return
        if cantidad > restante_actual:
            await interaction.response.send_message(
                f"⚠️ Solo tenés `{restante_actual}` pipa(s) disponibles en este crédito.",
                ephemeral=True,
            )
            return

        result = _consumir_credito_db(
            int(credito["id"]),
            str(credito["owner_discord_id"]),
            str(self.target_id),
            self.target_nombre,
            cantidad,
            "transfer",
            str(interaction.user.id),
        )
        if not result.get("ok"):
            await interaction.response.send_message(
                f"⚠️ {result.get('motivo', 'No se pudo usar el crédito.')}",
                ephemeral=True,
            )
            return

        if interaction.guild:
            await _sincronizar_rol_deuda_por_contador(
                interaction.guild,
                self.target_id,
                self.target_nombre,
            )

        await interaction.response.send_message(
            f"✅ Se descontaron **{result['aplicado']}** pipa(s) del contador de "
            f"<@{self.target_id}>.\n"
            f"Crédito restante: `{result['restante']}`.",
            ephemeral=True,
        )


# ── Modal: dar por Discord ID (botón alternativo) ────────────────────────────

class _ChemiCreditosByIDModal(discord.ui.Modal, title="Dar crédito por Discord ID"):
    discord_id_input = discord.ui.TextInput(
        label="Discord ID del jugador destino",  # 33 chars — ok
        placeholder="Ej: 123456789012345678",
        required=True,
        max_length=24,
    )
    cantidad = discord.ui.TextInput(
        label="Pipas a darle",  # era: "¿Cuántas pipas querés darle?" 
        placeholder="Ej: 1",
        required=True,
        max_length=4,
    )

    def __init__(self, credito_id: int, owner_id: int):
        super().__init__()
        self.credito_id = int(credito_id)
        self.owner_id   = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return

        # Validar ID
        raw_id = str(self.discord_id_input.value).strip().replace("<@", "").replace(">", "").replace("!", "")
        if not raw_id.isdigit():
            await interaction.response.send_message("❌ Discord ID inválido. Solo números.", ephemeral=True)
            return

        target_id = int(raw_id)

        # Anti-exploit: no darse a uno mismo
        if target_id == self.owner_id:
            await interaction.response.send_message(
                "❌ No podés darte el crédito a vos mismo.\nUsá **⬇️ Usar en mí**.",
                ephemeral=True,
            )
            return

        try:
            cantidad = int(str(self.cantidad.value).strip())
        except (TypeError, ValueError):
            await interaction.response.send_message("❌ La cantidad debe ser un número entero.", ephemeral=True)
            return
        if cantidad <= 0:
            await interaction.response.send_message("❌ La cantidad debe ser mayor a 0.", ephemeral=True)
            return

        # Resolver nombre del target
        target_nombre = None
        if interaction.guild:
            member = interaction.guild.get_member(target_id)
            if not member:
                try:
                    member = await interaction.guild.fetch_member(target_id)
                except Exception:
                    member = None
            if member:
                if member.bot:
                    await interaction.response.send_message(
                        "❌ No podés transferirle crédito a un bot.", ephemeral=True
                    )
                    return
                target_nombre = member.display_name

        credito = _get_credito_by_id(self.credito_id)
        if not credito or str(credito.get("owner_discord_id")) != str(self.owner_id):
            await interaction.response.send_message("❌ Crédito no disponible.", ephemeral=True)
            return

        restante_actual = int(credito.get("cantidad_restante") or 0)
        if restante_actual <= 0:
            await interaction.response.send_message("ℹ️ Este crédito ya está agotado.", ephemeral=True)
            return
        if cantidad > restante_actual:
            await interaction.response.send_message(
                f"⚠️ Solo tenés `{restante_actual}` pipa(s) disponibles en este crédito.",
                ephemeral=True,
            )
            return

        result = _consumir_credito_db(
            int(credito["id"]),
            str(credito["owner_discord_id"]),
            str(target_id),
            target_nombre,
            cantidad,
            "transfer",
            str(interaction.user.id),
        )
        if not result.get("ok"):
            await interaction.response.send_message(
                f"⚠️ {result.get('motivo', 'No se pudo usar el crédito.')}",
                ephemeral=True,
            )
            return

        if interaction.guild:
            await _sincronizar_rol_deuda_por_contador(
                interaction.guild,
                target_id,
                target_nombre,
            )

        nombre_display = target_nombre or str(target_id)
        await interaction.response.send_message(
            f"✅ Se descontaron **{result['aplicado']}** pipa(s) del contador de "
            f"**{nombre_display}** (<@{target_id}>).\n"
            f"Crédito restante: `{result['restante']}`.",
            ephemeral=True,
        )
 
async def evaluar_retiro_chemi(datos: dict):
    if not chemi_activo():
        logger.info("ℹ️ [Chemi] Sistema desactivado; retiro ignorado")
        return
    if not _bot:
        return

    objeto = datos.get("objeto", "")
    if not es_pistola(objeto):
        return

    discord_id_str = str(datos.get("discord_id") or "")
    if not discord_id_str:
        return

    discord_id = int(discord_id_str)
    nombre = datos.get("nombre", "N/A")
    cantidad = int(datos.get("cantidad", 1))

    if not _bot.guilds:
        return
    guild = _bot.guilds[0]
    member = guild.get_member(discord_id)

    contador_anterior, contador_nuevo = _sumar_contador_chemi(discord_id_str, nombre, cantidad)
    logger.info(f"🧪 [Chemi] Retiro | {discord_id} {contador_anterior}->{contador_nuevo} (+{cantidad})")

    tenia_bloqueo = contador_anterior >= LIMITE_PISTOLAS_DIA or bool(member and tiene_deuda_chemi(member))
    _sincronizar_deuda_db(discord_id_str, nombre, contador_nuevo)

    if contador_nuevo >= LIMITE_PISTOLAS_DIA:
        await _asignar_rol_deuda(guild, discord_id)
        if contador_anterior < LIMITE_PISTOLAS_DIA:
            await _avisar_limite_alcanzado(discord_id, nombre, contador_nuevo)
            await _enviar_dm_limite(discord_id, contador_nuevo)

    if tenia_bloqueo:
        deuda_info = _get_deuda_info(discord_id_str) or {
            "debe_devolver": contador_nuevo,
            "deadline": None,
            "aviso_altos_cargos": False,
        }
        await _avisar_deuda_altos_cargos(discord_id, nombre, deuda_info)


async def evaluar_deposito_chemi(datos: dict):
    if not chemi_activo():
        logger.info("ℹ️ [Chemi] Sistema desactivado; depósito ignorado")
        return
    if not _bot:
        logger.warning("⚠️ [Chemi] _bot es None en evaluar_deposito_chemi")
        return

    objeto = datos.get("objeto", "")
    logger.info(f"🧪 [Chemi] Depósito recibido | objeto={objeto!r} | datos={datos}")

    if not es_pistola(objeto):
        logger.info(f"ℹ️ [Chemi] Depósito ignorado — es_pistola=False para objeto={objeto!r}")
        return

    discord_id_str = str(datos.get("discord_id") or "")
    if not discord_id_str:
        logger.warning("⚠️ [Chemi] Depósito sin discord_id; ignorado")
        return
    discord_id = int(discord_id_str)

    cantidad = int(datos.get("cantidad", 1))
    nombre = datos.get("nombre", "N/A")

    logger.info(f"🧪 [Chemi] Procesando depósito | discord_id={discord_id} | nombre={nombre!r} | cantidad={cantidad}")

    # Leer contador ANTES de reducir
    contador_antes = _get_contador_chemi(str(discord_id))
    logger.info(f"🧪 [Chemi] Contador antes del depósito: {contador_antes}")

    try:
        anterior, aplicado, nuevo = _reducir_contador_chemi(str(discord_id), nombre, cantidad)
        logger.info(f"🧪 [Chemi] Contador reducido | {discord_id} {anterior}->{nuevo} (-{aplicado})")
        if aplicado > 0:
            await _sincronizar_rol_deuda_por_contador(
                _bot.guilds[0] if _bot.guilds else None,
                discord_id,
                nombre,
            )
    except Exception as e:
        logger.error(f"❌ [Chemi] Error reduciendo contador por depósito: {e}", exc_info=True)
        aplicado = 0

    # Las pipas que no bajaron contador son crédito
    # Ej: contador=0, deposita 1 → aplicado=0, crédito=1
    # Ej: contador=1, deposita 1 → aplicado=1, crédito=0
    # Ej: contador=2, deposita 1 → aplicado=1, crédito=0
    credito = cantidad - aplicado
    logger.info(f"🧪 [Chemi] Crédito a generar: {credito} (cantidad={cantidad}, aplicado={aplicado})")

    if credito > 0:
        logger.info(f"🧪 [Chemi] Generando crédito por {credito} pipa(s) para {discord_id}")
        await _enviar_credito_deposito(discord_id, nombre, credito)
    else:
        logger.info(f"ℹ️ [Chemi] Sin crédito — la pipa bajó el contador ({aplicado} aplicado)")


def _programar_verificacion_profit(discord_id: str, nombre: str, cantidad: int):
    if _bot is None:
        return

    async def _verificar():
        try:
            await asyncio.sleep(HORAS_DEVOLUCION * 3600)
            if _tiene_deuda_activa_db(discord_id):
                return
            falta = _pistolas_retiradas_sin_profit(discord_id)
            if falta <= 0:
                logger.info(f"✅ [Chemi] {discord_id} cumplió profit - sin deuda")
                return
            logger.warning(
                f"⚠️ [Chemi] {discord_id} ({nombre}) no devolvió profit en {HORAS_DEVOLUCION}h. "
                f"Faltan {falta} pistolas"
            )
            _registrar_deuda_db(discord_id, nombre, cantidad, falta)
            if _bot.guilds:
                guild = _bot.guilds[0]
                uid = int(discord_id)
                await _asignar_rol_deuda(guild, uid)
                await _avisar_deuda_registrada(uid, nombre, falta)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ [Chemi] Error en tarea de profit: {e}", exc_info=True)

    _bot.loop.create_task(_verificar())


async def restaurar_deudas_activas():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT discord_id, nombre FROM chemi_contadores
            WHERE contador >= %s
            UNION
            SELECT discord_id, nombre FROM chemi_deudas
            WHERE activa = TRUE
            """,
            (LIMITE_PISTOLAS_DIA,),
        )
        deudas = cursor.fetchall() or []
        cursor.close()
        conn.close()

        if not deudas or not _bot or not _bot.guilds:
            return
        guild = _bot.guilds[0]

        logger.info(f"📌 [Chemi] Restaurando {len(deudas)} deudas activas al arrancar")
        for deuda in deudas:
            try:
                uid = int(deuda["discord_id"])
                await _asignar_rol_deuda(guild, uid)
            except Exception as e:
                logger.error(f"❌ [Chemi] Error restaurando deuda {deuda['discord_id']}: {e}")
    except Exception as e:
        logger.error(f"❌ [Chemi] Error restaurando deudas: {e}", exc_info=True)
