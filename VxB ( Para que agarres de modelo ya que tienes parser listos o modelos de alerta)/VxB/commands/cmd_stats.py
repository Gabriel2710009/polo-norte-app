import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands

from config import CATEGORIAS, ALTO_CARGO_ROLE_ID, DEVELOPER_ROLE_ID, DEVELOPER_USER_IDS
from database import get_db_connection
from utils import traducir_objeto, es_armero, es_armero_o_alto_cargo

logger = logging.getLogger("ArmamentBot")


def _resumir_ids_registros(ids: list[int], limite: int = 35) -> str:
    if not ids:
        return "ninguno"
    visibles = ", ".join(f"`{rid}`" for rid in ids[:limite])
    if len(ids) > limite:
        visibles += f" y {len(ids) - limite} mas"
    return visibles


def _es_alto_o_dev(member: discord.Member) -> bool:
    role_ids = {r.id for r in getattr(member, "roles", [])}
    return (
        ALTO_CARGO_ROLE_ID in role_ids
        or DEVELOPER_ROLE_ID in role_ids
        or member.id in DEVELOPER_USER_IDS
    )


def _build_balance_embed(
    title: str,
    color: discord.Color,
    target_user: discord.Member,
    usuario_nombre: str,
    resultados: list,
    consultado_por: str,
) -> list[discord.Embed]:
    """Construye uno o más embeds con el balance por item."""
    lineas = []
    balance_total = total_dep = total_ret = 0

    for r in resultados:
        obj_txt   = traducir_objeto(r["objeto"])
        dep       = r["depositos"]
        ret       = r["retiros"]
        bal       = r["balance"]
        balance_total += bal
        total_dep     += dep
        total_ret     += ret
        emoji = "📈" if bal > 0 else ("📉" if bal < 0 else "📊")
        lineas.append(
            f"{emoji} **{obj_txt}**\n"
            f"   Depósitos: {dep} | Retiros: {ret} | Balance: {bal:+d}"
        )

    embed = discord.Embed(
        title=title,
        description=(
            f"**Usuario Discord:** {target_user.mention}\n"
            f"**Nombre IC:** {usuario_nombre}"
        ),
        color=color,
        timestamp=datetime.now(),
    )

    # Dividir si es necesario
    chunk, chunk_len, part = [], 0, 1
    chunks = []
    for linea in lineas:
        if chunk_len + len(linea) + 2 > 1024:
            chunks.append("\n\n".join(chunk))
            chunk, chunk_len = [linea], len(linea)
            part += 1
        else:
            chunk.append(linea)
            chunk_len += len(linea) + 2
    if chunk:
        chunks.append("\n\n".join(chunk))

    for i, c in enumerate(chunks, 1):
        embed.add_field(
            name=f"⚖️ Balance por Item" + (f" ({i}/{len(chunks)})" if len(chunks) > 1 else ""),
            value=c,
            inline=False,
        )

    b_emoji = "📈" if balance_total > 0 else ("📉" if balance_total < 0 else "📊")
    embed.add_field(
        name=f"{b_emoji} Balance Total",
        value=(
            f"**{balance_total:+d}**\n"
            f"Total depósitos: {total_dep} | Total retiros: {total_ret}"
        ),
        inline=False,
    )
    embed.set_footer(text=f"Items: {len(resultados)} | {consultado_por}")
    return embed


async def _cmd_categoria_simple(
    interaction: discord.Interaction,
    categoria: str,
    title: str,
    color: discord.Color,
    target_user: discord.Member,
):
    await interaction.response.defer(ephemeral=True)
    objetos = CATEGORIAS.get(categoria, [])

    conn   = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT discord_id, nombre
        FROM registros_armas
        WHERE discord_id = %s AND objeto = ANY(%s) LIMIT 1
    """, (str(target_user.id), objetos))
    result = cursor.fetchone()

    if not result:
        cursor.close()
        conn.close()
        await interaction.followup.send(f"❌ No hay registros para {target_user.mention}.", ephemeral=True)
        return

    cursor.execute("""
        SELECT objeto,
               SUM(CASE WHEN tipo='DEPOSITO' THEN cantidad ELSE 0 END) AS depositos,
               SUM(CASE WHEN tipo='RETIRO'   THEN cantidad ELSE 0 END) AS retiros,
               SUM(CASE WHEN tipo='DEPOSITO' THEN cantidad ELSE -cantidad END) AS balance
        FROM registros_armas
        WHERE discord_id = %s AND objeto = ANY(%s)
        GROUP BY objeto ORDER BY balance ASC
    """, (str(result["discord_id"]), objetos))
    resultados = cursor.fetchall()
    cursor.close()
    conn.close()

    embed = _build_balance_embed(title, color, target_user, result["nombre"], resultados, interaction.user.name)
    await interaction.followup.send(embed=embed, ephemeral=True)


def register(tree: app_commands.CommandTree):

    @tree.command(name="validar_retiros_dias", description="Validar retiros fuera de operativo por cantidad de dias")
    @app_commands.describe(
        dias="Cantidad de días hacia atrás, entre 1 y 30",
        usuario="Usuario de Discord a revisar, opcional",
        solo_pistolas="Validar solo pistolas y accesorios",
    )
    async def validar_retiros_dias(
        interaction: discord.Interaction,
        dias: Optional[int] = 1,
        usuario: Optional[discord.Member] = None,
        solo_pistolas: Optional[bool] = False,
    ):
        from alertas import actualizar_alerta_estado, cerrar_mensajes_devolucion
        from log_actions import log_accion
        from views.validar_view import _cancelar_proceso_razon

        if not _es_alto_o_dev(interaction.user):
            await interaction.response.send_message("⛔ Solo Altos Cargos o developers.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        dias = max(1, min(30, int(dias or 1)))
        inicio = datetime.now() - timedelta(days=dias)

        where = [
            "tipo = 'RETIRO'",
            "timestamp >= %s",
            "COALESCE(en_operativo, FALSE) = FALSE",
            "COALESCE(validado, FALSE) = FALSE",
        ]
        params = [inicio]
        if usuario:
            where.append("discord_id = %s")
            params.append(str(usuario.id))
        if solo_pistolas:
            where.append("objeto = ANY(%s)")
            params.append(list(CATEGORIAS.get("pistolas", [])))

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                SELECT id, discord_id, nombre, objeto, cantidad, almacen, timestamp,
                       alerta_message_id, alerta_channel_id
                FROM registros_armas
                WHERE {" AND ".join(where)}
                ORDER BY timestamp ASC, id ASC
                """,
                params,
            )
            rows = cursor.fetchall() or []
            if rows:
                ids = [int(row["id"]) for row in rows]
                cursor.execute(
                    """
                    UPDATE registros_armas
                    SET validado         = TRUE,
                        validado_por     = %s,
                        fecha_validacion = NOW(),
                        no_validado      = FALSE,
                        no_validado_por  = NULL,
                        fecha_no_validado = NULL,
                        justificacion_validacion = %s
                    WHERE id = ANY(%s)
                    """,
                    (
                        interaction.user.name,
                        f"Validado masivamente con /validar_retiros_dias ({dias} dia(s))",
                        ids,
                    ),
                )
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

        if not rows:
            await log_accion(
                interaction.user,
                "Uso /validar_retiros_dias",
                (
                    f"Dias: `{dias}` | Usuario: {usuario.mention if usuario else 'todos'} | "
                    f"Solo pistolas: `{'si' if solo_pistolas else 'no'}` | Registros validados: ninguno"
                ),
                discord.Color.orange(),
                "📋",
            )
            await interaction.followup.send("No hay retiros fuera de operativo sin validar para ese filtro.", ephemeral=True)
            return

        ids = [int(row["id"]) for row in rows]
        total = len(rows)
        progress_msg = await interaction.followup.send(
            f"Validando `{total}` retiro(s) fuera de operativo...\nRegistros: {_resumir_ids_registros(ids)}",
            ephemeral=True,
            wait=True,
        )

        actualizados = errores = sin_alerta = 0
        estado_txt = f"✅ Validado por /validar_retiros_dias ({dias} dia(s))"
        for idx, row in enumerate(rows, 1):
            try:
                if row.get("alerta_message_id") and row.get("alerta_channel_id"):
                    await actualizar_alerta_estado(row, estado_txt, discord.Color.green(), interaction.user.mention)
                    actualizados += 1
                else:
                    sin_alerta += 1
                await _cancelar_proceso_razon(interaction.client, int(row["id"]))
                await cerrar_mensajes_devolucion(int(row["id"]), interaction.user.mention)
            except Exception as e:
                errores += 1
                logger.error(f"Error actualizando Discord para retiro {row.get('id')}: {e}", exc_info=True)

            if idx % 5 == 0 or idx == total:
                try:
                    await progress_msg.edit(
                        content=(
                            f"Validando retiros fuera de operativo: `{idx}/{total}`\n"
                            f"Embeds actualizados: `{actualizados}` | Sin alerta: `{sin_alerta}` | Errores: `{errores}`\n"
                            f"Registros: {_resumir_ids_registros(ids)}"
                        )
                    )
                except Exception:
                    pass
                await asyncio.sleep(1.2)

        por_usuario = defaultdict(lambda: {"cantidad": 0, "items": defaultdict(int)})
        for row in rows:
            key = (row.get("discord_id"), row.get("nombre") or "N/A")
            por_usuario[key]["cantidad"] += 1
            por_usuario[key]["items"][row.get("objeto")] += int(row.get("cantidad") or 0)

        embed = discord.Embed(
            title=f"Retiros validados - ultimos {dias} dia(s)",
            description=(
                f"Se marcaron como validados `{total}` retiro(s) fuera de operativo.\n"
                f"Embeds actualizados: `{actualizados}` | Sin alerta: `{sin_alerta}` | Errores Discord: `{errores}`"
            ),
            color=discord.Color.green() if errores == 0 else discord.Color.orange(),
            timestamp=datetime.now(),
        )
        if usuario:
            embed.add_field(name="Usuario filtrado", value=usuario.mention, inline=False)
        embed.add_field(name="Registros", value=_resumir_ids_registros(ids), inline=False)

        for (discord_id, nombre), data in list(por_usuario.items())[:10]:
            header = nombre
            if discord_id:
                header += f" (<@{discord_id}>)"
            lineas = [
                f"{traducir_objeto(obj)}: {cant}"
                for obj, cant in sorted(data["items"].items(), key=lambda item: item[1], reverse=True)[:6]
            ]
            embed.add_field(
                name=f"{header} | {data['cantidad']} retiro(s)",
                value="\n".join(lineas)[:1024] or "Sin detalle",
                inline=False,
            )
        embed.set_footer(text=f"Solo pistolas: {'si' if solo_pistolas else 'no'} | Ejecutado por {interaction.user.name}")

        await log_accion(
            interaction.user,
            "Uso /validar_retiros_dias",
            (
                f"Dias: `{dias}` | Usuario: {usuario.mention if usuario else 'todos'} | "
                f"Solo pistolas: `{'si' if solo_pistolas else 'no'}` | "
                f"Registros validados ({total}): {_resumir_ids_registros(ids, limite=60)}"
            ),
            discord.Color.green() if errores == 0 else discord.Color.orange(),
            "✅",
        )

        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    # ── /armas ────────────────────────────────────────────────
    @tree.command(name="armas", description="Ver balance de retiros y depósitos de armas por usuario")
    @app_commands.describe(
        periodo="Período a consultar",
        usuario="Usuario de Discord (opcional)",
        id_personaje="ID del personaje en el servidor (opcional)",
    )
    @app_commands.choices(periodo=[
        app_commands.Choice(name="Día",       value="dia"),
        app_commands.Choice(name="Semana",    value="semana"),
        app_commands.Choice(name="Mes",       value="mes"),
        app_commands.Choice(name="Histórico", value="historico"),
    ])
    async def armas_command(
        interaction: discord.Interaction,
        periodo: app_commands.Choice[str],
        usuario: Optional[discord.Member] = None,
        id_personaje: Optional[str] = None,
    ):
        try:
            if not es_armero_o_alto_cargo(interaction.user):
                await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)

            if not usuario and not id_personaje:
                usuario = interaction.user

            ahora = datetime.now()
            inicio = {
                "dia":      ahora.replace(hour=0, minute=0, second=0, microsecond=0),
                "semana":   (ahora - timedelta(days=ahora.weekday())).replace(hour=0, minute=0, second=0, microsecond=0),
                "mes":      ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
                "historico": datetime.min,
            }.get(periodo.value, datetime.min)

            where_clauses = ["timestamp >= %s"]
            params        = [inicio.isoformat()]

            if usuario and id_personaje:
                where_clauses.append("(discord_id = %s OR id_personaje = %s)")
                params.extend([str(usuario.id), id_personaje])
            elif usuario:
                where_clauses.append("discord_id = %s")
                params.append(str(usuario.id))
            elif id_personaje:
                where_clauses.append("id_personaje = %s")
                params.append(id_personaje)

            where_sql = " AND ".join(where_clauses)
            conn   = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT objeto, nombre, id_personaje, discord_id,
                       SUM(CASE WHEN tipo='DEPOSITO' THEN cantidad ELSE 0 END)  AS depositos,
                       SUM(CASE WHEN tipo='RETIRO'   THEN cantidad ELSE 0 END)  AS retiros,
                       SUM(CASE WHEN tipo='DEPOSITO' THEN cantidad ELSE -cantidad END) AS balance
                FROM registros_armas
                WHERE {where_sql}
                GROUP BY objeto, nombre, id_personaje, discord_id
                ORDER BY id_personaje, nombre, balance ASC
            """, params)
            resultados = cursor.fetchall()
            cursor.close()
            conn.close()

            if not resultados:
                await interaction.followup.send("❌ No hay registros para ese período.", ephemeral=True)
                return

            # Agrupar por persona
            personas = defaultdict(lambda: {"nombre": None, "id_personaje": None, "discord_id": None, "objetos": defaultdict(lambda: {"depositos": 0, "retiros": 0, "balance": 0})})
            for r in resultados:
                key = f"{r['nombre']}_{r['id_personaje']}_{r['discord_id']}"
                personas[key]["nombre"]       = r["nombre"]
                personas[key]["id_personaje"] = r["id_personaje"]
                personas[key]["discord_id"]   = r["discord_id"]
                for campo in ("depositos", "retiros", "balance"):
                    personas[key]["objetos"][r["objeto"]][campo] += r[campo]

            embed = discord.Embed(
                title=f"⚖️ Balance de Armería - {periodo.name}",
                color=discord.Color.blue(),
                timestamp=datetime.now(),
            )
            if usuario:
                embed.description = f"**Usuario Discord:** {usuario.mention}"
            if id_personaje:
                embed.description = (embed.description or "") + f"\n**ID Personaje:** `{id_personaje}`"

            for idx, (_, data) in enumerate(personas.items(), 1):
                nombre       = data["nombre"]
                id_pj        = data["id_personaje"]
                discord_id   = data["discord_id"]
                objetos      = data["objetos"]
                bal_total    = sum(o["balance"]   for o in objetos.values())
                tot_dep      = sum(o["depositos"]  for o in objetos.values())
                tot_ret      = sum(o["retiros"]    for o in objetos.values())

                header = f"**{idx}. {nombre}**"
                if id_pj:
                    header += f" (ID: `{id_pj}`)"
                if discord_id:
                    header += f" <@{discord_id}>"

                lineas = []
                for obj, stats in sorted(objetos.items(), key=lambda x: x[1]["balance"]):
                    emoji = "📈" if stats["balance"] > 0 else ("📉" if stats["balance"] < 0 else "📊")
                    lineas.append(
                        f"{emoji} **{traducir_objeto(obj)}**: D:{stats['depositos']} R:{stats['retiros']} B:{stats['balance']:+d}"
                    )
                b_emoji = "📈" if bal_total > 0 else ("📉" if bal_total < 0 else "📊")
                lineas.append(f"\n{b_emoji} **Total**: D:{tot_dep} R:{tot_ret} B:{bal_total:+d}")

                texto = "\n".join(lineas)
                if len(texto) <= 1024:
                    embed.add_field(name=header, value=texto, inline=False)
                else:
                    mitad = len(lineas) // 2
                    embed.add_field(name=header + " (1/2)", value="\n".join(lineas[:mitad])[:1024], inline=False)
                    embed.add_field(name="↳ Continuación (2/2)", value="\n".join(lineas[mitad:])[:1024], inline=False)

            embed.set_footer(text=f"Consultado por {interaction.user.name} | {len(resultados)} registros")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except discord.errors.NotFound:
            logger.warning("⚠️ Webhook expirado para /armas")
        except Exception as e:
            logger.error(f"❌ Error en /armas: {e}", exc_info=True)
            try:
                await interaction.followup.send("❌ Error procesando el comando.", ephemeral=True)
            except Exception:
                pass

    # ── /balas ────────────────────────────────────────────────
    @tree.command(name="balas", description="Balance de munición por usuario")
    @app_commands.describe(usuario="Usuario a consultar (opcional)")
    async def balas_command(interaction: discord.Interaction, usuario: Optional[discord.Member] = None):
        if not es_armero(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        target = usuario or interaction.user
        try:
            await _cmd_categoria_simple(interaction, "balas", "⚖️ Balance de Munición", discord.Color.blurple(), target)
        except Exception as e:
            logger.error(f"❌ Error en /balas: {e}", exc_info=True)

    # ── /pistolas ─────────────────────────────────────────────
    @tree.command(name="pistolas", description="Balance de pistolas y accesorios por usuario")
    @app_commands.describe(usuario="Usuario a consultar (opcional)")
    async def pistolas_command(interaction: discord.Interaction, usuario: Optional[discord.Member] = None):
        if not es_armero(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        target = usuario or interaction.user
        try:
            await _cmd_categoria_simple(interaction, "pistolas", "⚖️ Balance de Pistolas", discord.Color.blue(), target)
        except Exception as e:
            logger.error(f"❌ Error en /pistolas: {e}", exc_info=True)

    # ── /arma_blanca ──────────────────────────────────────────
    @tree.command(name="arma_blanca", description="Balance de armas blancas por usuario")
    @app_commands.describe(usuario="Usuario a consultar (opcional)")
    async def arma_blanca_command(interaction: discord.Interaction, usuario: Optional[discord.Member] = None):
        if not es_armero(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        target = usuario or interaction.user
        try:
            await _cmd_categoria_simple(interaction, "arma_blanca", "⚖️ Balance de Armas Blancas", discord.Color.dark_gray(), target)
        except Exception as e:
            logger.error(f"❌ Error en /arma_blanca: {e}", exc_info=True)

    # ── /otros ────────────────────────────────────────────────
    @tree.command(name="otros", description="Balance de otros objetos por usuario")
    @app_commands.describe(usuario="Usuario a consultar (opcional)")
    async def otros_command(interaction: discord.Interaction, usuario: Optional[discord.Member] = None):
        if not es_armero(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        target = usuario or interaction.user
        try:
            await _cmd_categoria_simple(interaction, "otros", "⚖️ Balance de Otros Objetos", discord.Color.green(), target)
        except Exception as e:
            logger.error(f"❌ Error en /otros: {e}", exc_info=True)

    # ── /drogas ───────────────────────────────────────────────
    @tree.command(name="drogas", description="Balance de drogas y dinero negro por usuario")
    @app_commands.describe(usuario="Usuario a consultar (opcional)")
    async def drogas_command(interaction: discord.Interaction, usuario: Optional[discord.Member] = None):
        if not es_armero(interaction.user):
            await interaction.response.send_message("⛔ Sin permiso.", ephemeral=True)
            return
        target = usuario or interaction.user
        try:
            await _cmd_categoria_simple(interaction, "drogas", "⚖️ Balance de Drogas / Dinero Negro", discord.Color.red(), target)
        except Exception as e:
            logger.error(f"❌ Error en /drogas: {e}", exc_info=True)
