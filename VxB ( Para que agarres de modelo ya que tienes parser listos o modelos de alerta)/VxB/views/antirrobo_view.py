import logging
import re
from datetime import datetime

import discord

import state
from database import toggle_whitelist_antirrobo, obtener_whitelist_antirrobo
from utils import traducir_objeto, es_armero
from antirrobo import (
    generar_preview_antirrobo,
    guardar_config_antirrobo,
    buscar_items_antirrobo,
)

logger = logging.getLogger("ArmamentBot")


# ─── CONFIG MODAL ─────────────────────────────────────────────

class AntirroboConfigModal(discord.ui.Modal, title="Configurar Antirrobo"):
    ventana_minutos      = discord.ui.TextInput(label="Ventana (minutos)",                       default="120",  required=True)
    retiros_masivos      = discord.ui.TextInput(label="Umbral retiros masivos",                  default="20",   required=True)
    desbalance_fuerte    = discord.ui.TextInput(label="Desbalance fuerte: retiros,depositos_max", default="20,5", required=True)
    desbalance_ratio     = discord.ui.TextInput(label="Desbalance ratio: retiros_min,factor",     default="5,5",  required=True)
    relajacion_operativo = discord.ui.TextInput(label="Factor relajación operativo",              default="1.8",  required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            ventana    = max(5, int(str(self.ventana_minutos.value).strip()))
            retiros_m  = max(1, int(str(self.retiros_masivos.value).strip()))
            desb_p     = [p.strip() for p in str(self.desbalance_fuerte.value).split(",")]
            ratio_p    = [p.strip() for p in str(self.desbalance_ratio.value).split(",")]
            if len(desb_p) != 2 or len(ratio_p) != 2:
                raise ValueError("Formato inválido.")
            relajacion = max(1.0, float(str(self.relajacion_operativo.value).strip()))

            state.ANTIRROBO_CONFIG.update({
                "ventana_minutos":                 ventana,
                "umbral_retiros_masivos":          max(1, int(desb_p[0])),
                "umbral_desbalance_retiros":       max(1, int(desb_p[0])),
                "umbral_desbalance_depositos_max": max(0, int(desb_p[1])),
                "umbral_ratio_retiros":            max(1, int(ratio_p[0])),
                "umbral_ratio_factor":             max(1.0, float(ratio_p[1])),
                "operativo_relajacion_factor":     relajacion,
                "umbral_retiros_masivos":          retiros_m,
            })
            ok = guardar_config_antirrobo(str(interaction.user))
            if not ok:
                await interaction.response.send_message("❌ No se pudo guardar en BD.", ephemeral=True)
                return

            from log_actions import log_accion
            embed = discord.Embed(
                title="🛡️ Antirrobo actualizado",
                description=generar_preview_antirrobo(),
                color=discord.Color.green(),
                timestamp=datetime.now(),
            )
            await log_accion(
                interaction.user, "Configuró parámetros antirrobo",
                f"Ventana: {ventana}min | Umbral: {retiros_m}",
                discord.Color.blue(), "⚙️",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


# ─── WHITELIST ────────────────────────────────────────────────

class AntiRobWhitelistUserSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="Buscar usuario para agregar/quitar", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        from log_actions import log_accion
        user   = self.values[0]
        estado = toggle_whitelist_antirrobo(user.id, user.display_name, str(interaction.user))
        if estado is None:
            await interaction.response.send_message("❌ Error actualizando whitelist.", ephemeral=True)
            return
        if estado:
            await log_accion(interaction.user, "Agregó a whitelist antirrobo", f"{user.mention} (`{user.id}`)", discord.Color.green(), "🛡️")
            await interaction.response.send_message(f"🛡️ {user.mention} agregado a whitelist.", ephemeral=True)
        else:
            await log_accion(interaction.user, "Removió de whitelist antirrobo", f"{user.mention} (`{user.id}`)", discord.Color.orange(), "🛡️")
            await interaction.response.send_message(f"🛡️ {user.mention} removido de whitelist.", ephemeral=True)


class AntiRobWhitelistByIdModal(discord.ui.Modal, title="Whitelist por Discord ID"):
    discord_id = discord.ui.TextInput(
        label="Discord ID o mención",
        placeholder="Ej: 123456789012345678 o <@123456789012345678>",
        required=True,
        max_length=64,
    )

    async def on_submit(self, interaction: discord.Interaction):
        from log_actions import log_accion
        raw      = str(self.discord_id.value).strip()
        id_match = re.search(r"\d{17,20}", raw)
        if not id_match:
            await interaction.response.send_message("❌ ID inválido.", ephemeral=True)
            return
        uid    = int(id_match.group(0))
        nombre = f"ID {uid}"
        if interaction.guild:
            member = interaction.guild.get_member(uid)
            if member:
                nombre = member.display_name

        estado = toggle_whitelist_antirrobo(uid, nombre, str(interaction.user))
        if estado is None:
            await interaction.response.send_message("❌ Error actualizando whitelist.", ephemeral=True)
            return
        if estado:
            await log_accion(interaction.user, "Agregó a whitelist antirrobo", f"ID: `{uid}` | {nombre}", discord.Color.green(), "🛡️")
            await interaction.response.send_message(f"🛡️ `{uid}` agregado.", ephemeral=True)
        else:
            await log_accion(interaction.user, "Removió de whitelist antirrobo", f"ID: `{uid}` | {nombre}", discord.Color.orange(), "🛡️")
            await interaction.response.send_message(f"🛡️ `{uid}` removido.", ephemeral=True)


class AntiRobWhitelistManageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(AntiRobWhitelistUserSelect())

    @discord.ui.button(label="Agregar/Quitar por ID", style=discord.ButtonStyle.secondary)
    async def por_id(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AntiRobWhitelistByIdModal())


class AntiRobWhitelistView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Gestionar whitelist", style=discord.ButtonStyle.primary)
    async def gestionar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not es_armero(interaction.user):
            await interaction.response.send_message("⛔ No tenés permiso.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Seleccioná un usuario. Si está → se elimina, si no está → se agrega.",
            view=AntiRobWhitelistManageView(),
            ephemeral=True,
        )


# ─── ITEMS CONFIG ─────────────────────────────────────────────

class AntiRobItemSelect(discord.ui.Select):
    def __init__(self, opciones: list):
        super().__init__(
            placeholder="Elegí items para monitorear",
            min_values=1,
            max_values=len(opciones),
            options=[
                discord.SelectOption(
                    label=traducir_objeto(item)[:100],
                    value=item,
                    description=item[:100],
                )
                for item in opciones
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.parent_state["seleccionados"].update(self.values)
        await interaction.response.send_message(
            f"ℹ️ {len(self.values)} item(s) agregados.", ephemeral=True
        )


class AntiRobItemsSearchResultView(discord.ui.View):
    def __init__(self, parent_state: dict, opciones: list):
        super().__init__(timeout=180)
        self.parent_state = parent_state
        self.add_item(AntiRobItemSelect(opciones))


class AntiRobItemsSearchModal(discord.ui.Modal, title="Buscar ítems antirrobo"):
    termino = discord.ui.TextInput(
        label="Buscar por código o nombre",
        placeholder="Ej: ammo, pistol, chaleco, lockpick",
        required=False,
        max_length=80,
    )

    def __init__(self, parent_state: dict):
        super().__init__()
        self.parent_state = parent_state

    async def on_submit(self, interaction: discord.Interaction):
        opciones = buscar_items_antirrobo(str(self.termino.value), limite=25)
        if not opciones:
            await interaction.response.send_message("❌ No se encontraron ítems.", ephemeral=True)
            return
        lista = "\n".join([f"• `{item}` ({traducir_objeto(item)})" for item in opciones[:10]])
        if len(opciones) > 10:
            lista += f"\n… y {len(opciones) - 10} más."
        embed = discord.Embed(
            title="🔍 Resultados",
            description=f"Usá el selector para marcar ítems.\n\n{lista}",
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(
            embed=embed,
            view=AntiRobItemsSearchResultView(self.parent_state, opciones),
            ephemeral=True,
        )


class AntiRobItemsConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.state = {"seleccionados": set(state.ANTIRROBO_CONFIG.get("objetos_monitoreados", set()) or set())}

    @discord.ui.button(label="Buscar ítems", style=discord.ButtonStyle.primary)
    async def buscar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AntiRobItemsSearchModal(self.state))

    @discord.ui.button(label="Ver selección", style=discord.ButtonStyle.secondary)
    async def ver(self, interaction: discord.Interaction, button: discord.ui.Button):
        seleccion = sorted(self.state["seleccionados"])
        if not seleccion:
            texto = "Configurado en **todos los ítems** (sin filtro)."
        else:
            preview = "\n".join([f"• `{item}` ({traducir_objeto(item)})" for item in seleccion[:20]])
            extra   = f"\n… y {len(seleccion) - 20} más." if len(seleccion) > 20 else ""
            texto   = f"Ítems seleccionados ({len(seleccion)}):\n{preview}{extra}"
        await interaction.response.send_message(texto, ephemeral=True)

    @discord.ui.button(label="Confirmar selección", style=discord.ButtonStyle.success)
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        state.ANTIRROBO_CONFIG["objetos_monitoreados"] = set(self.state["seleccionados"])
        ok = guardar_config_antirrobo(str(interaction.user))
        if not ok:
            await interaction.response.send_message("❌ No se pudo guardar.", ephemeral=True)
            return
        n = len(state.ANTIRROBO_CONFIG["objetos_monitoreados"])
        await interaction.response.send_message(
            f"🛡️ Selección confirmada. Ítems monitoreados: {n or 'TODOS'}.", ephemeral=True
        )

    @discord.ui.button(label="Monitorear todo", style=discord.ButtonStyle.danger)
    async def todo(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.state["seleccionados"].clear()
        state.ANTIRROBO_CONFIG["objetos_monitoreados"] = set()
        ok = guardar_config_antirrobo(str(interaction.user))
        if not ok:
            await interaction.response.send_message("❌ No se pudo guardar.", ephemeral=True)
            return
        await interaction.response.send_message("🛡️ Antirrobo configurado para monitorear todos los ítems.", ephemeral=True)


# ─── PANEL PRINCIPAL ──────────────────────────────────────────

class AntiRobControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Activar", style=discord.ButtonStyle.success)
    async def activar(self, interaction: discord.Interaction, button: discord.ui.Button):
        from log_actions import log_accion
        state.ANTIRROBO_CONFIG["activo"] = True
        guardar_config_antirrobo(str(interaction.user))
        await log_accion(interaction.user, "Activó sistema antirrobo", "", discord.Color.green(), "🛡️")
        await interaction.response.send_message("🛡️ Sistema antirrobo activado.", ephemeral=True)

    @discord.ui.button(label="Desactivar", style=discord.ButtonStyle.danger)
    async def desactivar(self, interaction: discord.Interaction, button: discord.ui.Button):
        from log_actions import log_accion
        state.ANTIRROBO_CONFIG["activo"] = False
        guardar_config_antirrobo(str(interaction.user))
        await log_accion(interaction.user, "Desactivó sistema antirrobo", "", discord.Color.red(), "🛡️")
        await interaction.response.send_message("🛡️ Sistema antirrobo desactivado.", ephemeral=True)

    @discord.ui.button(label="Configurar", style=discord.ButtonStyle.primary)
    async def configurar(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AntirroboConfigModal()
        modal.ventana_minutos.default      = str(state.ANTIRROBO_CONFIG["ventana_minutos"])
        modal.retiros_masivos.default      = str(state.ANTIRROBO_CONFIG["umbral_retiros_masivos"])
        modal.desbalance_fuerte.default    = f"{state.ANTIRROBO_CONFIG['umbral_desbalance_retiros']},{state.ANTIRROBO_CONFIG['umbral_desbalance_depositos_max']}"
        modal.desbalance_ratio.default     = f"{state.ANTIRROBO_CONFIG['umbral_ratio_retiros']},{state.ANTIRROBO_CONFIG['umbral_ratio_factor']}"
        modal.relajacion_operativo.default = str(state.ANTIRROBO_CONFIG["operativo_relajacion_factor"])
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Configurar ítems", style=discord.ButtonStyle.primary)
    async def configurar_items(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🛡️ Configuración de ítems",
            description=(
                "1) **Buscar ítems** → escribís nombre/código.\n"
                "2) Elegís en el selector.\n"
                "3) Repetís para sumar más.\n"
                "4) **Confirmar selección** para guardar.\n\n"
                "Sin selección → monitorea **todo**."
            ),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, view=AntiRobItemsConfigView(), ephemeral=True)

    @discord.ui.button(label="Ver estado", style=discord.ButtonStyle.secondary)
    async def ver(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🛡️ Estado antirrobo",
            description=generar_preview_antirrobo(),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Ayuda", style=discord.ButtonStyle.secondary)
    async def ayuda(self, interaction: discord.Interaction, button: discord.ui.Button):
        monitoreados = state.ANTIRROBO_CONFIG.get("objetos_monitoreados", set()) or set()
        items_txt    = "Todos" if not monitoreados else str(len(monitoreados))
        embed = discord.Embed(
            title="🛡️ Cómo funciona el antirrobo",
            description=(
                "El sistema evalúa retiros por usuario dentro de una ventana de tiempo.\n\n"
                f"• `ventana_minutos`: período analizado (actual {state.ANTIRROBO_CONFIG['ventana_minutos']}).\n"
                f"• `umbral_retiros_masivos`: alerta por volumen alto (actual {state.ANTIRROBO_CONFIG['umbral_retiros_masivos']}).\n"
                f"• `umbral_desbalance_*`: alerta por desbalance fuerte.\n"
                f"• `umbral_ratio_*`: alerta por ratio retiros/depósitos.\n"
                f"• `operativo_relajacion_factor`: sube umbrales durante operativo (x{state.ANTIRROBO_CONFIG['operativo_relajacion_factor']}).\n"
                f"• Items monitoreados: {items_txt}."
            ),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)