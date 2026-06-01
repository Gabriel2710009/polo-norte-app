import logging
from datetime import datetime

import discord

import state
from config import ITEMS_POR_PAGINA
from utils import traducir_objeto, es_armero_o_alto_cargo
from database import get_db_connection

logger = logging.getLogger("ArmamentBot")


class SelectRetiroDropdown(discord.ui.Select):
    def __init__(self, retiros_pagina: list, parent_view):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(
                label=f"{traducir_objeto(r['objeto'])} x{r['cantidad']}",
                description=f"ID #{r['id']} | {r['nombre'][:30]}",
                value=str(r["id"]),
            )
            for r in retiros_pagina
        ]
        super().__init__(
            placeholder=f"Selecciona un retiro (Pág {parent_view.pagina + 1})",
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        retiro_id = int(self.values[0])
        retiro    = next(r for r in self.parent_view.retiros if r["id"] == retiro_id)
        embed     = _build_embed_retiro(retiro)
        view      = DetalleRetiroView(retiro, self.parent_view.retiros, self.parent_view.pagina)
        await interaction.response.edit_message(embed=embed, view=view)


class BotonPaginaAnterior(discord.ui.Button):
    def __init__(self, parent_view):
        super().__init__(label="⬅️ Anterior", style=discord.ButtonStyle.secondary, row=1)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        nueva_view = SeleccionarRetiroView(
            self.parent_view.retiros, self.parent_view.pagina - 1
        )
        await interaction.response.edit_message(
            content=f"📤 **Retiros pendientes ({len(self.parent_view.retiros)})**",
            view=nueva_view,
        )


class BotonPaginaSiguiente(discord.ui.Button):
    def __init__(self, parent_view):
        super().__init__(label="➡️ Siguiente", style=discord.ButtonStyle.secondary, row=1)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        nueva_view = SeleccionarRetiroView(
            self.parent_view.retiros, self.parent_view.pagina + 1
        )
        await interaction.response.edit_message(
            content=f"📤 **Retiros pendientes ({len(self.parent_view.retiros)})**",
            view=nueva_view,
        )


class SeleccionarRetiroView(discord.ui.View):
    def __init__(self, retiros: list, pagina: int = 0):
        super().__init__(timeout=300)
        self.retiros = retiros
        self.pagina  = pagina

        total      = len(retiros)
        inicio     = pagina * ITEMS_POR_PAGINA
        fin        = inicio + ITEMS_POR_PAGINA
        pagina_act = retiros[inicio:fin]

        self.add_item(SelectRetiroDropdown(pagina_act, self))
        if pagina > 0:
            self.add_item(BotonPaginaAnterior(self))
        if fin < total:
            self.add_item(BotonPaginaSiguiente(self))


def _build_embed_retiro(retiro: dict) -> discord.Embed:
    embed = discord.Embed(
        title="📤 Detalle de Retiro",
        color=discord.Color.orange(),
        timestamp=retiro.get("timestamp") or datetime.now(),
    )
    embed.add_field(name="ℹ️ Usuario",    value=f"<@{retiro['discord_id']}>",               inline=False)
    embed.add_field(name="ℹ️ Nombre IC",  value=retiro.get("nombre", "N/A"),                 inline=False)
    embed.add_field(name="ℹ️ Objeto",     value=traducir_objeto(retiro["objeto"]),            inline=True)
    embed.add_field(name="ℹ️ Cantidad",   value=str(retiro["cantidad"]),                      inline=True)
    embed.add_field(name="ℹ️ Almacén",   value=retiro.get("almacen", "N/A"),                 inline=True)
    embed.set_footer(text=f"Registro ID: {retiro['id']}")
    return embed


class DetalleRetiroView(discord.ui.View):
    def __init__(self, retiro: dict, retiros: list, page: int):
        super().__init__(timeout=300)
        self.retiro  = retiro
        self.retiros = retiros
        self.page    = page

    async def _volver_lista_actualizada(self, interaction: discord.Interaction):
        self.retiros[:] = [r for r in self.retiros if r["id"] != self.retiro["id"]]

        edit_fn = (
            interaction.edit_original_response
            if interaction.response.is_done()
            else interaction.response.edit_message
        )

        if not self.retiros:
            await edit_fn(content="📤 No quedan retiros pendientes.", embed=None, view=None)
            return

        max_page = (len(self.retiros) - 1) // ITEMS_POR_PAGINA
        if self.page > max_page:
            self.page = max_page

        config_info = (
            f"\nℹ️ *Mostrando solo: {len(state.OBJETOS_ALERTAR)} objetos configurados*"
            if state.OBJETOS_ALERTAR
            else "\nℹ️ *Mostrando todos los objetos*"
        )
        await edit_fn(
            content=f"📤 **Retiros pendientes ({len(self.retiros)})**{config_info}",
            embed=None,
            view=SeleccionarRetiroView(self.retiros, self.page),
        )

    async def _sync_alerta(self, estado_texto: str, color: discord.Color, actor: discord.Member):
        from alertas import actualizar_alerta_estado
        await actualizar_alerta_estado(self.retiro, estado_texto=estado_texto, color=color, actor_mention=actor.mention)

    @discord.ui.button(label="✅ Validar", style=discord.ButtonStyle.success, row=0)
    async def validar(self, interaction: discord.Interaction, button: discord.ui.Button):
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
                SET validado         = TRUE,
                    validado_por     = %s,
                    fecha_validacion = NOW(),
                    no_validado      = FALSE
                WHERE id = %s
            """, (interaction.user.name, self.retiro["id"]))
            conn.commit()
            cursor.close()
            conn.close()

            await self._sync_alerta("✅ VALIDADO", discord.Color.green(), interaction.user)
            await log_accion(
                interaction.user, "Validó retiro (panel pendientes)",
                f"Registro ID: `{self.retiro['id']}` | {traducir_objeto(self.retiro.get('objeto'))} x{self.retiro.get('cantidad', 1)}",
                discord.Color.green(), "✅",
            )
            await self._volver_lista_actualizada(interaction)
        except Exception as e:
            logger.error(f"❌ Error validando retiro pendiente: {e}", exc_info=True)
            await interaction.followup.send("❌ Error al validar.", ephemeral=True)

    @discord.ui.button(label="❌ No validar", style=discord.ButtonStyle.danger, row=0)
    async def no_validar(self, interaction: discord.Interaction, button: discord.ui.Button):
        from alertas import iniciar_solicitud_devolucion
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
            """, (interaction.user.name, self.retiro["id"]))
            conn.commit()
            cursor.close()
            conn.close()

            await self._sync_alerta("❌ NO VALIDADO", discord.Color.red(), interaction.user)
            await log_accion(
                interaction.user, "Rechazó retiro (panel pendientes)",
                f"Registro ID: `{self.retiro['id']}` | {traducir_objeto(self.retiro.get('objeto'))} x{self.retiro.get('cantidad', 1)}",
                discord.Color.red(), "❌",
            )
            await iniciar_solicitud_devolucion(self.retiro, interaction.user)
            await self._volver_lista_actualizada(interaction)
        except Exception as e:
            logger.error(f"❌ Error rechazando retiro pendiente: {e}", exc_info=True)
            await interaction.followup.send("❌ Error al rechazar.", ephemeral=True)

    @discord.ui.button(label="🔙 Volver a la lista", style=discord.ButtonStyle.secondary, row=1)
    async def volver(self, interaction: discord.Interaction, button: discord.ui.Button):
        config_info = (
            f"\nℹ️ *Mostrando solo: {len(state.OBJETOS_ALERTAR)} objetos configurados*"
            if state.OBJETOS_ALERTAR
            else "\nℹ️ *Mostrando todos los objetos*"
        )
        await interaction.response.edit_message(
            content=f"📤 **Retiros pendientes ({len(self.retiros)})**{config_info}",
            embed=None,
            view=SeleccionarRetiroView(self.retiros, self.page),
        )

    @discord.ui.button(label="🔒 Cerrar", style=discord.ButtonStyle.secondary, row=1)
    async def cerrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="🧩 Panel cerrado.", embed=None, view=None)