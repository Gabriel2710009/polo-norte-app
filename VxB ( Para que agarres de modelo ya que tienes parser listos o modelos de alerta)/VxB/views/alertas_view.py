import logging
from datetime import datetime

import discord

import state
from config import CATEGORIAS
from database import guardar_config_alertas
from utils import traducir_objeto, es_armero
from alertas import generar_preview_alertas

logger = logging.getLogger("ArmamentBot")


# ─── SELECTS ──────────────────────────────────────────────────

class SelectBalas(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="🔹 Munición",
            options=[
                discord.SelectOption(label=traducir_objeto(o), value=o, emoji="🔹")
                for o in CATEGORIAS["balas"]
            ],
            min_values=0,
            max_values=len(CATEGORIAS["balas"]),
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.objetos_seleccionados -= set(CATEGORIAS["balas"])
        self.view.objetos_seleccionados |= set(self.values)
        await interaction.response.send_message(f"🔹 {len(self.values)} municiones", ephemeral=True, delete_after=3)


class SelectDrogas(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="💊 Drogas y Dinero Negro",
            options=[
                discord.SelectOption(label=traducir_objeto(o), value=o, emoji="💊")
                for o in CATEGORIAS["drogas"]
            ],
            min_values=0,
            max_values=len(CATEGORIAS["drogas"]),
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.objetos_seleccionados -= set(CATEGORIAS["drogas"])
        self.view.objetos_seleccionados |= set(self.values)
        await interaction.response.send_message(f"💊 {len(self.values)} drogas", ephemeral=True, delete_after=3)


class SelectPistolas(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="🔫 Pistolas y Accesorios",
            options=[
                discord.SelectOption(
                    label=traducir_objeto(o),
                    value=o,
                    emoji="🔫" if "WEAPON" in o else "🔧",
                )
                for o in CATEGORIAS["pistolas"]
            ],
            min_values=0,
            max_values=len(CATEGORIAS["pistolas"]),
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.objetos_seleccionados -= set(CATEGORIAS["pistolas"])
        self.view.objetos_seleccionados |= set(self.values)
        await interaction.response.send_message(f"🔫 {len(self.values)} pistolas/accesorios", ephemeral=True, delete_after=3)


class SelectComidaBebida(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="🍔 Comida y Bebida",
            options=[
                discord.SelectOption(label=traducir_objeto(o), value=o, emoji="🍔")
                for o in CATEGORIAS["comida_bebida"]
            ],
            min_values=0,
            max_values=len(CATEGORIAS["comida_bebida"]),
            row=3,
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.objetos_seleccionados -= set(CATEGORIAS["comida_bebida"])
        self.view.objetos_seleccionados |= set(self.values)
        await interaction.response.send_message(f"🍔 {len(self.values)} comida/bebida", ephemeral=True, delete_after=3)


class SelectArmaBlanca(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="🔪 Armas Blancas",
            options=[
                discord.SelectOption(label=traducir_objeto(o), value=o, emoji="🔪")
                for o in CATEGORIAS["arma_blanca"]
            ],
            min_values=0,
            max_values=len(CATEGORIAS["arma_blanca"]),
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.objetos_seleccionados -= set(CATEGORIAS["arma_blanca"])
        self.view.objetos_seleccionados |= set(self.values)
        await interaction.response.send_message(f"🔪 {len(self.values)} armas blancas", ephemeral=True, delete_after=3)


class SelectKitsEquipamiento(discord.ui.Select):
    def __init__(self):
        opciones = CATEGORIAS["kits_equipamiento"]
        super().__init__(
            placeholder="🧰 Kits y Equipamiento",
            options=[
                discord.SelectOption(
                    label=traducir_objeto(o),
                    value=o,
                    emoji="🚑" if o in {"medikit", "bandage", "defibrillator"} else "🧰",
                )
                for o in opciones
            ],
            min_values=0,
            max_values=len(opciones),
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.objetos_seleccionados -= set(CATEGORIAS["kits_equipamiento"])
        self.view.objetos_seleccionados |= set(self.values)
        await interaction.response.send_message(f"🧰 {len(self.values)} kits", ephemeral=True, delete_after=3)


class SelectOtrosItems(discord.ui.Select):
    def __init__(self):
        opciones = CATEGORIAS["otros_items"]
        super().__init__(
            placeholder="💰 Dinero y Otros Items",
            options=[
                discord.SelectOption(
                    label=traducir_objeto(o),
                    value=o,
                    emoji="💰" if o == "money" else "📦",
                )
                for o in opciones
            ],
            min_values=0,
            max_values=len(opciones),
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.objetos_seleccionados -= set(CATEGORIAS["otros_items"])
        self.view.objetos_seleccionados |= set(self.values)
        await interaction.response.send_message(f"💰 {len(self.values)} items", ephemeral=True, delete_after=3)


# ─── HELPERS COMUNES ─────────────────────────────────────────-

async def _guardar_y_responder(interaction: discord.Interaction, objetos: set):
    from log_actions import log_accion

    state.OBJETOS_ALERTAR = objetos.copy()
    state.ALERTAS_ACTIVAS = True
    ok = guardar_config_alertas(state.OBJETOS_ALERTAR, state.ALERTAS_ACTIVAS, interaction.user.name)

    if ok:
        await log_accion(
            interaction.user, "Configuró alertas",
            f"Objetos: {len(state.OBJETOS_ALERTAR) or 'Todos'}",
            discord.Color.green(), "⚙️",
        )
        embed = discord.Embed(
            title="🚨 Configuración guardada",
            description=generar_preview_alertas(),
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )
        embed.set_footer(text=f"Configurado por {interaction.user.name}")
    else:
        embed = discord.Embed(
            title="❌ Error al guardar",
            description="No se pudo guardar en la base de datos.",
            color=discord.Color.red(),
        )
    await interaction.response.edit_message(embed=embed, view=None)


# ─── VIEWS PAGINADAS ──────────────────────────────────────────

class ConfigurarAlertasView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.objetos_seleccionados = set(state.OBJETOS_ALERTAR)
        self.add_item(SelectBalas())
        self.add_item(SelectDrogas())
        self.add_item(SelectPistolas())
        self.add_item(SelectComidaBebida())

    @discord.ui.button(label="➡️ Siguiente", style=discord.ButtonStyle.primary, row=4)
    async def siguiente(self, interaction: discord.Interaction, button: discord.ui.Button):
        view2 = ConfigurarAlertasView2()
        view2.objetos_seleccionados = self.objetos_seleccionados.copy()
        await interaction.response.edit_message(view=view2)

    @discord.ui.button(label="💾 Guardar", style=discord.ButtonStyle.success, row=4)
    async def guardar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _guardar_y_responder(interaction, self.objetos_seleccionados)

    @discord.ui.button(label="📣 Alertar todo", style=discord.ButtonStyle.primary, row=4)
    async def alertar_todo(self, interaction: discord.Interaction, button: discord.ui.Button):
        from log_actions import log_accion

        state.OBJETOS_ALERTAR.clear()
        state.ALERTAS_ACTIVAS = True
        ok = guardar_config_alertas(state.OBJETOS_ALERTAR, state.ALERTAS_ACTIVAS, interaction.user.name)
        if ok:
            await log_accion(interaction.user, "Alertar TODO activado", "", discord.Color.green(), "🔊")
            await interaction.response.edit_message(content="🚨 Alertar todo activado y guardado.", view=None)
        else:
            await interaction.response.edit_message(content="🚨 Alertar todo activado pero error al guardar.", view=None)

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.secondary, row=4)
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ Cancelado.", view=None)


class ConfigurarAlertasView2(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.objetos_seleccionados = set()
        self.add_item(SelectArmaBlanca())
        self.add_item(SelectKitsEquipamiento())
        self.add_item(SelectOtrosItems())

    @discord.ui.button(label="🔙 Volver", style=discord.ButtonStyle.secondary, row=3)
    async def volver(self, interaction: discord.Interaction, button: discord.ui.Button):
        view1 = ConfigurarAlertasView()
        view1.objetos_seleccionados = self.objetos_seleccionados.copy()
        await interaction.response.edit_message(view=view1)

    @discord.ui.button(label="💾 Guardar", style=discord.ButtonStyle.success, row=3)
    async def guardar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _guardar_y_responder(interaction, self.objetos_seleccionados)

    @discord.ui.button(label="⚙️ Ver config", style=discord.ButtonStyle.secondary, row=3)
    async def ver_config(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🚨 Configuración actual",
            description=generar_preview_alertas(),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
