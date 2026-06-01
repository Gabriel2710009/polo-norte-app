import logging
from datetime import datetime
from typing import Optional

import discord

from config import (
    DEVELOPER_ROLE_ID,
    VOICE_ADMIN_PANEL_CHANNEL_ID,
    VOICE_CATEGORY_ID,
    VOICE_ALLOWED_CATEGORY_IDS,
)
from database import (
    delete_voice_channel,
    delete_voice_channel_by_channel_id,
    get_voice_channel_record,
    get_voice_channel_record_by_channel_id,
    upsert_voice_channel,
)
from utils import es_admin_clips, _extract_id_from_text, _normalize_channel_display_name

logger = logging.getLogger("ArmamentBot")


def _es_admin_voz(member: discord.Member) -> bool:
    return es_admin_clips(member)


def _build_voice_overwrites(
    guild: discord.Guild,
    owner: Optional[discord.Member],
    role: Optional[discord.Role],
    visible: bool,
):
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False)
    }
    if role:
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=visible, connect=visible, speak=True, use_voice_activation=True,
        )
    if owner:
        overwrites[owner] = discord.PermissionOverwrite(
            view_channel=True, connect=True, speak=True, use_voice_activation=True,
            move_members=True, mute_members=True, deafen_members=True, manage_channels=False,
        )
    return overwrites


async def _crear_rol_canal_voz(guild: discord.Guild, nombre: str) -> Optional[discord.Role]:
    try:
        return await guild.create_role(name=f"🎙️ {nombre}", mentionable=False, reason="Rol auto-creado para canal de voz")
    except discord.HTTPException as e:
        logger.error(f"❌ Error creando rol para canal de voz: {e}")
        return None


async def _crear_canal_voz_para_miembro(guild, owner, nombre_override=None):
    record = get_voice_channel_record(owner.id)
    if record:
        existing = guild.get_channel(int(record["channel_id"]))
        if existing:
            return None, None, f"Ya tiene un canal de voz: {existing.mention}"
        delete_voice_channel(owner.id)

    category = guild.get_channel(VOICE_CATEGORY_ID)
    if not isinstance(category, discord.CategoryChannel):
        return None, None, "❌ No se encontró la categoría de voz configurada."

    nombre = (nombre_override or _normalize_channel_display_name(owner))[:90]
    role = await _crear_rol_canal_voz(guild, nombre)
    overwrites = _build_voice_overwrites(guild, owner, role, True)

    try:
        channel = await guild.create_voice_channel(nombre, category=category, overwrites=overwrites, reason=f"Canal de voz para {owner} ({owner.id})")
    except discord.HTTPException as e:
        if role:
            try:
                await role.delete(reason="Canal de voz no se pudo crear")
            except Exception:
                pass
        return None, None, f"❌ Error creando el canal: {e}"

    upsert_voice_channel(owner.id, channel.id, role.id if role else None)
    logger.info(f"🎙️ Canal de voz creado: {channel.name} ({channel.id}) | Rol: {role.id if role else 'N/A'}")
    return channel, role, None


# ── MODALES ───────────────────────────────────────────────────

class VoiceAdminCreateModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Crear canal de voz")
        self.user_id = discord.ui.TextInput(label="ID del usuario (dueño)", placeholder="123456789012345678", required=True, max_length=32)
        self.nombre  = discord.ui.TextInput(label="Nombre del canal (opcional)", placeholder="Deja vacío para usar el nick", required=False, max_length=90)
        self.add_item(self.user_id)
        self.add_item(self.nombre)

    async def on_submit(self, interaction: discord.Interaction):
        from log_actions import log_accion
        if not _es_admin_voz(interaction.user):
            await interaction.response.send_message("⛔ Sin permisos.", ephemeral=True)
            return
        if not interaction.guild:
            await interaction.response.send_message("❌ Solo funciona en el servidor.", ephemeral=True)
            return
        uid = _extract_id_from_text(self.user_id.value)
        if not uid:
            await interaction.response.send_message("❌ ID de usuario inválido.", ephemeral=True)
            return
        member = interaction.guild.get_member(uid)
        if not member:
            await interaction.response.send_message("❌ Usuario no encontrado en el servidor.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        nombre_override = self.nombre.value.strip() or None
        channel, role, error = await _crear_canal_voz_para_miembro(interaction.guild, member, nombre_override)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        if role:
            try:
                await member.add_roles(role, reason="Rol de canal de voz propio")
            except discord.HTTPException as e:
                logger.warning(f"⚠️ No se pudo dar el rol al owner: {e}")
        await log_accion(interaction.user, "Creó canal de voz (admin)", f"{channel.mention} → {member.mention} | Rol: {role.mention if role else 'N/A'}", discord.Color.green(), "🎙️")
        await interaction.followup.send(
            f"✅ Canal {channel.mention} creado.\nRol: {role.mention if role else '❌ no creado'}\n"
            f"{member.mention} puede usar el botón **Gestionar mi acceso** en el panel para invitar a otros.",
            ephemeral=True,
        )


class VoiceAdminDeleteModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Borrar canal de voz")
        self.channel_id = discord.ui.TextInput(label="ID del canal de voz", placeholder="1469...", required=True, max_length=32)
        self.add_item(self.channel_id)

    async def on_submit(self, interaction: discord.Interaction):
        from log_actions import log_accion
        if not _es_admin_voz(interaction.user):
            await interaction.response.send_message("⛔ Sin permisos.", ephemeral=True)
            return
        channel_id = _extract_id_from_text(self.channel_id.value)
        if not channel_id or not interaction.guild:
            await interaction.response.send_message("❌ ID inválido.", ephemeral=True)
            return
        channel = interaction.guild.get_channel(channel_id)
        if not channel:
            await interaction.response.send_message("❌ Canal no encontrado.", ephemeral=True)
            return
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message("❌ Ese canal no es un canal de voz.", ephemeral=True)
            return
        if channel.category_id not in VOICE_ALLOWED_CATEGORY_IDS:
            await interaction.response.send_message("❌ Canal fuera de las categorías autorizadas.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        record  = get_voice_channel_record_by_channel_id(channel_id)
        role_id = int(record["role_id"]) if record and record.get("role_id") else None
        if role_id:
            role = interaction.guild.get_role(role_id)
            if role:
                try:
                    await role.delete(reason=f"Canal de voz borrado por {interaction.user}")
                except discord.HTTPException as e:
                    logger.warning(f"⚠️ No se pudo borrar rol {role_id}: {e}")
        nombre = channel.name
        try:
            await channel.delete(reason=f"Admin delete by {interaction.user} ({interaction.user.id})")
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            return
        delete_voice_channel_by_channel_id(channel_id)
        await log_accion(interaction.user, "Borró canal de voz", f"#{nombre} (`{channel_id}`)", discord.Color.red(), "🗑️")
        await interaction.followup.send(f"✅ Canal `{nombre}` y su rol fueron eliminados.", ephemeral=True)


class VoiceAdminHideModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Ocultar canal de voz")
        self.channel_id = discord.ui.TextInput(label="ID del canal de voz", placeholder="1469...", required=True, max_length=32)
        self.add_item(self.channel_id)

    async def on_submit(self, interaction: discord.Interaction):
        from log_actions import log_accion
        if not _es_admin_voz(interaction.user):
            await interaction.response.send_message("⛔ Sin permisos.", ephemeral=True)
            return
        channel_id = _extract_id_from_text(self.channel_id.value)
        if not channel_id or not interaction.guild:
            await interaction.response.send_message("❌ ID inválido.", ephemeral=True)
            return
        channel = interaction.guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message("❌ Canal de voz no encontrado.", ephemeral=True)
            return
        if channel.category_id not in VOICE_ALLOWED_CATEGORY_IDS:
            await interaction.response.send_message("❌ Canal fuera de categorías autorizadas.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        record  = get_voice_channel_record_by_channel_id(channel_id)
        owner   = interaction.guild.get_member(int(record["user_id"])) if record else None
        role_id = int(record["role_id"]) if record and record.get("role_id") else None
        role    = interaction.guild.get_role(role_id) if role_id else None
        overwrites = dict(channel.overwrites)
        overwrites[interaction.guild.default_role] = discord.PermissionOverwrite(view_channel=False, connect=False)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=False, connect=False)
        if owner:
            overwrites[owner] = discord.PermissionOverwrite(view_channel=False, connect=False)
        try:
            await channel.edit(overwrites=overwrites)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            return
        await log_accion(interaction.user, "Ocultó canal de voz", f"#{channel.name} (`{channel_id}`)", discord.Color.greyple(), "👁️")
        await interaction.followup.send(f"✅ Canal `{channel.name}` ocultado.", ephemeral=True)


class VoiceAdminUnhideModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Desocultar canal de voz")
        self.channel_id = discord.ui.TextInput(label="ID del canal de voz", placeholder="1469...", required=True, max_length=32)
        self.add_item(self.channel_id)

    async def on_submit(self, interaction: discord.Interaction):
        from log_actions import log_accion
        if not _es_admin_voz(interaction.user):
            await interaction.response.send_message("⛔ Sin permisos.", ephemeral=True)
            return
        channel_id = _extract_id_from_text(self.channel_id.value)
        if not channel_id or not interaction.guild:
            await interaction.response.send_message("❌ ID inválido.", ephemeral=True)
            return
        channel = interaction.guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message("❌ Canal de voz no encontrado.", ephemeral=True)
            return
        if channel.category_id not in VOICE_ALLOWED_CATEGORY_IDS:
            await interaction.response.send_message("❌ Canal fuera de categorías autorizadas.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        record  = get_voice_channel_record_by_channel_id(channel_id)
        owner   = interaction.guild.get_member(int(record["user_id"])) if record else None
        role_id = int(record["role_id"]) if record and record.get("role_id") else None
        role    = interaction.guild.get_role(role_id) if role_id else None
        overwrites = _build_voice_overwrites(interaction.guild, owner, role, True)
        try:
            await channel.edit(overwrites=overwrites)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            return
        await log_accion(interaction.user, "Desocultó canal de voz", f"#{channel.name} (`{channel_id}`)", discord.Color.green(), "✅")
        await interaction.followup.send(f"✅ Canal `{channel.name}` desocultado.", ephemeral=True)


class VoiceAdminRenameModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Renombrar canal de voz")
        self.channel_id = discord.ui.TextInput(label="ID del canal de voz", placeholder="1469...", required=True, max_length=32)
        self.new_name   = discord.ui.TextInput(label="Nuevo nombre", placeholder="Ej: mi-canal-privado", required=True, max_length=90)
        self.add_item(self.channel_id)
        self.add_item(self.new_name)

    async def on_submit(self, interaction: discord.Interaction):
        from log_actions import log_accion
        if not _es_admin_voz(interaction.user):
            await interaction.response.send_message("⛔ Sin permisos.", ephemeral=True)
            return
        channel_id = _extract_id_from_text(self.channel_id.value)
        if not channel_id or not interaction.guild:
            await interaction.response.send_message("❌ ID inválido.", ephemeral=True)
            return
        channel = interaction.guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message("❌ Canal de voz no encontrado.", ephemeral=True)
            return
        if channel.category_id not in VOICE_ALLOWED_CATEGORY_IDS:
            await interaction.response.send_message("❌ Canal fuera de categorías autorizadas.", ephemeral=True)
            return
        new_name = self.new_name.value.strip()[:90]
        if not new_name:
            await interaction.response.send_message("❌ Nombre inválido.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        record  = get_voice_channel_record_by_channel_id(channel_id)
        role_id = int(record["role_id"]) if record and record.get("role_id") else None
        role    = interaction.guild.get_role(role_id) if role_id else None
        old_name = channel.name
        try:
            await channel.edit(name=new_name)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            return
        if role:
            try:
                await role.edit(name=f"🎙️ {new_name}")
            except discord.HTTPException as e:
                logger.warning(f"⚠️ No se pudo renombrar rol: {e}")
        await log_accion(interaction.user, "Renombró canal de voz", f"#{old_name} → `{new_name}` (`{channel_id}`)", discord.Color.blue(), "✏️")
        await interaction.followup.send(f"✅ Canal renombrado a `{new_name}`.", ephemeral=True)


class VoiceDarAccesoModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Dar/Quitar acceso por ID")
        self.target_id = discord.ui.TextInput(label="ID del usuario", placeholder="123456789012345678", required=True, max_length=32)
        self.add_item(self.target_id)

    async def on_submit(self, interaction: discord.Interaction):
        from log_actions import log_accion
        if not interaction.guild:
            await interaction.response.send_message("❌ Solo funciona en el servidor.", ephemeral=True)
            return
        uid = _extract_id_from_text(self.target_id.value)
        if not uid:
            await interaction.response.send_message("❌ ID inválido.", ephemeral=True)
            return
        record = get_voice_channel_record(interaction.user.id)
        if not record:
            await interaction.response.send_message("❌ No tenés un canal de voz registrado.", ephemeral=True)
            return
        role_id = int(record["role_id"]) if record.get("role_id") else None
        role    = interaction.guild.get_role(role_id) if role_id else None
        if not role:
            await interaction.response.send_message("❌ El rol de tu canal no fue encontrado. Contactá a un admin.", ephemeral=True)
            return
        target = interaction.guild.get_member(uid)
        if not target:
            await interaction.response.send_message("❌ Usuario no encontrado en el servidor.", ephemeral=True)
            return
        if target.id == interaction.user.id:
            await interaction.response.send_message("❌ No podés modificar tu propio acceso.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if role in target.roles:
            await target.remove_roles(role, reason=f"Acceso quitado por {interaction.user}")
            await log_accion(interaction.user, "Quitó acceso a canal de voz", f"{target.mention}", discord.Color.orange(), "🔇")
            await interaction.followup.send(f"🔇 Acceso quitado a {target.mention}.", ephemeral=True)
        else:
            await target.add_roles(role, reason=f"Acceso dado por {interaction.user}")
            await log_accion(interaction.user, "Dio acceso a canal de voz", f"{target.mention}", discord.Color.green(), "🎙️")
            await interaction.followup.send(f"✅ Acceso dado a {target.mention}.", ephemeral=True)


# ── VIEW GESTIÓN ACCESO ───────────────────────────────────────

class VozGestionAccesoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.select(placeholder="Seleccioná un usuario para dar/quitar acceso", row=0, cls=discord.ui.UserSelect)
    async def select_usuario(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        from log_actions import log_accion
        if not interaction.guild:
            await interaction.response.send_message("❌ Solo funciona en el servidor.", ephemeral=True)
            return
        target = select.values[0]
        record = get_voice_channel_record(interaction.user.id)
        if not record:
            await interaction.response.send_message("❌ No tenés un canal de voz registrado.", ephemeral=True)
            return
        role_id = int(record["role_id"]) if record.get("role_id") else None
        role    = interaction.guild.get_role(role_id) if role_id else None
        if not role:
            await interaction.response.send_message("❌ El rol de tu canal no fue encontrado.", ephemeral=True)
            return
        member = interaction.guild.get_member(target.id)
        if not member:
            await interaction.response.send_message("❌ Usuario no encontrado.", ephemeral=True)
            return
        if member.id == interaction.user.id:
            await interaction.response.send_message("❌ No podés modificar tu propio acceso.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if role in member.roles:
            await member.remove_roles(role, reason=f"Acceso quitado por {interaction.user}")
            await log_accion(interaction.user, "Quitó acceso a canal de voz", f"{member.mention}", discord.Color.orange(), "🔇")
            await interaction.followup.send(f"🔇 Acceso quitado a {member.mention}.", ephemeral=True)
        else:
            await member.add_roles(role, reason=f"Acceso dado por {interaction.user}")
            await log_accion(interaction.user, "Dio acceso a canal de voz", f"{member.mention}", discord.Color.green(), "🎙️")
            await interaction.followup.send(f"✅ Acceso dado a {member.mention}.", ephemeral=True)

    @discord.ui.button(label="Por ID", style=discord.ButtonStyle.secondary, row=1)
    async def por_id(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VoiceDarAccesoModal())


# ── PANEL ADMIN ───────────────────────────────────────────────

class VoiceAdminPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎙️ Crear canal", style=discord.ButtonStyle.success, custom_id="voice_admin_create", row=0)
    async def crear(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _es_admin_voz(interaction.user):
            await interaction.response.send_message("⛔ Sin permisos.", ephemeral=True)
            return
        await interaction.response.send_modal(VoiceAdminCreateModal())

    @discord.ui.button(label="🗑️ Borrar canal", style=discord.ButtonStyle.danger, custom_id="voice_admin_delete", row=0)
    async def borrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _es_admin_voz(interaction.user):
            await interaction.response.send_message("⛔ Sin permisos.", ephemeral=True)
            return
        await interaction.response.send_modal(VoiceAdminDeleteModal())

    @discord.ui.button(label="👁️ Ocultar", style=discord.ButtonStyle.secondary, custom_id="voice_admin_hide", row=1)
    async def ocultar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _es_admin_voz(interaction.user):
            await interaction.response.send_message("⛔ Sin permisos.", ephemeral=True)
            return
        await interaction.response.send_modal(VoiceAdminHideModal())

    @discord.ui.button(label="✅ Desocultar", style=discord.ButtonStyle.secondary, custom_id="voice_admin_unhide", row=1)
    async def desocultar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _es_admin_voz(interaction.user):
            await interaction.response.send_message("⛔ Sin permisos.", ephemeral=True)
            return
        await interaction.response.send_modal(VoiceAdminUnhideModal())

    @discord.ui.button(label="✏️ Renombrar", style=discord.ButtonStyle.primary, custom_id="voice_admin_rename", row=2)
    async def renombrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _es_admin_voz(interaction.user):
            await interaction.response.send_message("⛔ Sin permisos.", ephemeral=True)
            return
        await interaction.response.send_modal(VoiceAdminRenameModal())

    @discord.ui.button(label="🎙️ Gestionar mi acceso", style=discord.ButtonStyle.primary, custom_id="voice_gestionar_acceso", row=2)
    async def gestionar_acceso(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = get_voice_channel_record(interaction.user.id)
        if not record:
            await interaction.response.send_message(
                "❌ No tenés un canal de voz registrado. Pedile a un admin que te cree uno.",
                ephemeral=True,
            )
            return
        channel = interaction.guild.get_channel(int(record["channel_id"])) if record else None
        role_id = int(record["role_id"]) if record and record.get("role_id") else None
        role    = interaction.guild.get_role(role_id) if role_id else None
        embed = discord.Embed(
            title="🎙️ Gestionar acceso a canal de voz",
            description=(
                f"Tu canal de voz: {channel.mention if channel else '❌ no encontrado'}\n"
                f"Rol de acceso: {role.mention if role else '❌ no encontrado'}\n\n"
                "Seleccioná un usuario para **dar o quitarle acceso** a tu canal."
            ),
            color=discord.Color.blue(),
            timestamp=datetime.now(),
        )
        await interaction.response.send_message(embed=embed, view=VozGestionAccesoView(), ephemeral=True)
