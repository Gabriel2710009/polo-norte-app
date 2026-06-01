import logging
import logging
from datetime import datetime
from typing import Optional

import discord

from config import (
    CLIPS_ALLOWED_CATEGORY_IDS,
    CLIPS_CATEGORY_ID,
    CLIPS_FALLBACK_CATEGORY_ID,
    CLIPS_CREATOR_ROLE_ID,
    CLIPS_VIEW_ROLE_ID,
    DEVELOPER_ROLE_ID,
    DEVELOPER_USER_IDS,
)
from database import (
    delete_clip_channel,
    delete_clip_channel_by_channel_id,
    get_clip_channel_record,
    get_clip_channel_record_by_channel_id,
    upsert_clip_channel,
)
from utils import (
    _extract_id_from_text,
    _is_custom_emoji,
    _is_single_unicode_emoji,
    _is_unicode_emoji,
    _normalize_channel_display_name,
    _normalize_clip_emoji,
    _normalize_channel_name_from_raw,
    _split_trailing_unicode_emoji,
    es_admin_clips,
)

logger = logging.getLogger("ArmamentBot")
MAX_CHANNELS_PER_CATEGORY = 50


# ─── HELPERS INTERNOS ─────────────────────────────────────────

def _build_clip_overwrites(
    guild: discord.Guild,
    member: discord.Member,
    allow_role_access: bool,
    allow_member_access: bool,
):
    overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    role = guild.get_role(CLIPS_VIEW_ROLE_ID)
    if role:
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=allow_role_access,
            add_reactions=allow_role_access,
            read_message_history=allow_role_access,
            send_messages=False,
            attach_files=False,
        )
    if member:
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=allow_member_access,
            send_messages=allow_member_access,
            add_reactions=allow_member_access,
            attach_files=allow_member_access,
            read_message_history=allow_member_access,
        )
    return overwrites


def _merge_clip_overwrites(
    channel: discord.TextChannel,
    member,
    allow_role_access: bool,
    allow_member_access: bool,
):
    overwrites = dict(channel.overwrites)
    overwrites[channel.guild.default_role] = discord.PermissionOverwrite(view_channel=False)
    role = channel.guild.get_role(CLIPS_VIEW_ROLE_ID)
    if role:
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=allow_role_access,
            add_reactions=allow_role_access,
            read_message_history=allow_role_access,
            send_messages=False,
            attach_files=False,
        )
    if member:
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=allow_member_access,
            send_messages=allow_member_access,
            add_reactions=allow_member_access,
            attach_files=allow_member_access,
            read_message_history=allow_member_access,
        )
    return overwrites


async def _set_clip_channel_visibility(
    channel: discord.TextChannel, member, visible: bool
):
    overwrites = _merge_clip_overwrites(channel, member, visible, visible)
    await channel.edit(overwrites=overwrites)


def _categoria_disponible_para_clips(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    candidatos = []
    for category_id in (CLIPS_CATEGORY_ID, CLIPS_FALLBACK_CATEGORY_ID):
        category = guild.get_channel(category_id)
        if isinstance(category, discord.CategoryChannel):
            candidatos.append(category)
        else:
            logger.warning(
                "⚠️ [Clips] Categoría candidata inválida | category_id=%s found=%s type=%s",
                category_id,
                getattr(category, "id", None),
                type(category).__name__ if category else None,
            )

    for category in candidatos:
        if len(category.channels) < MAX_CHANNELS_PER_CATEGORY:
            return category

    return candidatos[-1] if candidatos else None


# ─── CREAR CANAL (lógica compartida) ──────────────────────────

async def _create_clip_channel(interaction: discord.Interaction, emoji_text: str):
    from log_actions import log_accion

    guild  = interaction.guild
    member = interaction.user
    logger.info(
        "🎬 [Clips] Solicitud crear canal | user=%s (%s) guild=%s channel=%s emoji_raw=%r",
        getattr(member, "name", str(member)),
        getattr(member, "id", None),
        getattr(guild, "id", None),
        getattr(interaction.channel, "id", None),
        emoji_text,
    )
    if not guild or not isinstance(member, discord.Member):
        logger.warning("⚠️ [Clips] Solicitud fuera de guild o miembro inválido")
        msg = "No se puede usar fuera del servidor."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return

    role_ids = {r.id for r in member.roles}
    if (
        CLIPS_CREATOR_ROLE_ID not in role_ids
        and DEVELOPER_ROLE_ID not in role_ids
        and member.id not in DEVELOPER_USER_IDS
    ):
        logger.warning(
            "⛔ [Clips] Sin permisos para crear canal | user=%s (%s) roles=%s",
            member,
            member.id,
            sorted(role_ids),
        )
        msg = "⛔ No tienes permisos para crear canal de clips."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return

    if not interaction.response.is_done():
        logger.info("🎬 [Clips] Defer inicial enviado")
        await interaction.response.defer(ephemeral=True)

    record = get_clip_channel_record(member.id)
    logger.info("🎬 [Clips] Registro previo de canal=%s", record)
    if record:
        existing = guild.get_channel(int(record["channel_id"]))
        if existing:
            logger.info("🎬 [Clips] Ya existe canal asociado | channel_id=%s name=%s", existing.id, existing.name)
            await interaction.followup.send(f"Ya tienes un canal: {existing.mention}", ephemeral=True)
            return
        logger.info("🎬 [Clips] Registro huérfano detectado; se elimina user_id=%s", member.id)
        delete_clip_channel(member.id)

    category = _categoria_disponible_para_clips(guild)
    if not isinstance(category, discord.CategoryChannel):
        logger.error(
            "❌ [Clips] No hay categoría válida disponible | category_id=%s fallback_id=%s",
            CLIPS_CATEGORY_ID,
            CLIPS_FALLBACK_CATEGORY_ID,
        )
        await interaction.followup.send("❌ No se encontró la categoría configurada.", ephemeral=True)
        return

    slug        = _normalize_channel_display_name(member)
    logger.info("🎬 [Clips] Nombre base normalizado=%r", slug)
    emoji_text, emoji_warn, emoji_ok = _normalize_clip_emoji(emoji_text, guild)
    logger.info(
        "🎬 [Clips] Emoji normalizado=%r warn=%r ok=%s",
        emoji_text,
        emoji_warn,
        emoji_ok,
    )
    if not emoji_ok:
        logger.warning("⚠️ [Clips] Emoji inválido | warn=%s", emoji_warn)
        await interaction.followup.send(emoji_warn, ephemeral=True)
        return

    use_emoji   = bool(emoji_text) and _is_unicode_emoji(emoji_text)
    channel_name = f"{slug}{emoji_text}" if use_emoji else slug
    channel_name = channel_name[:90]
    logger.info(
        "🎬 [Clips] Preparando create_text_channel | name=%r use_emoji=%s category=%s overwrites=%s",
        channel_name,
        use_emoji,
        getattr(category, "id", None),
        len(_build_clip_overwrites(guild, member, True, True)),
    )
    overwrites   = _build_clip_overwrites(guild, member, True, True)

    try:
        logger.info("🎬 [Clips] Intentando crear canal principal")
        channel = await guild.create_text_channel(
            channel_name, category=category, overwrites=overwrites,
            reason=f"Canal de clips para {member} ({member.id})",
        )
        logger.info("✅ [Clips] Canal creado OK | id=%s name=%s", channel.id, channel.name)
    except discord.Forbidden as e:
        logger.exception(
            "❌ [Clips] Forbidden creando canal | user=%s (%s) category=%s name=%r error=%s",
            member,
            member.id,
            getattr(category, "id", None),
            channel_name,
            e,
        )
        await interaction.followup.send("❌ No tengo permisos para crear el canal en esa categoría.", ephemeral=True)
        return
    except discord.HTTPException as e:
        logger.exception(
            "❌ [Clips] HTTPException creando canal principal | status=%s code=%s text=%r",
            getattr(e, "status", None),
            getattr(e, "code", None),
            getattr(e, "text", None),
        )
        if use_emoji:
            try:
                logger.info("🎬 [Clips] Reintentando sin emoji")
                channel = await guild.create_text_channel(
                    slug, category=category, overwrites=overwrites,
                    reason=f"Canal de clips para {member} ({member.id})",
                )
                logger.info("✅ [Clips] Canal creado sin emoji | id=%s name=%s", channel.id, channel.name)
            except discord.HTTPException as e:
                logger.exception(
                    "❌ [Clips] HTTPException creando canal sin emoji | status=%s code=%s text=%r",
                    getattr(e, "status", None),
                    getattr(e, "code", None),
                    getattr(e, "text", None),
                )
                await interaction.followup.send(
                    "❌ Error creando el canal. Revisa los logs del bot para ver el paso exacto que falló.",
                    ephemeral=True,
                )
                return
        else:
            await interaction.followup.send(
                "❌ Error creando el canal. Revisa los logs del bot para ver el paso exacto que falló.",
                ephemeral=True,
            )
            return

    logger.info("🎬 [Clips] Guardando registro del canal | user=%s channel=%s", member.id, channel.id)
    upsert_clip_channel(member.id, channel.id)
    try:
        logger.info("🎬 [Clips] Enviando mensaje inicial al canal %s", channel.id)
        await channel.send(f"{member.mention} tu canal de clips fue creado.{' ' + emoji_text if emoji_text else ''}")
    except discord.HTTPException as e:
        logger.warning("⚠️ [Clips] No se pudo enviar mensaje inicial al canal %s: %s", channel.id, e)

    logger.info("🎬 [Clips] Registrando acción de log")
    await log_accion(member, "Creó su canal de clips", f"{channel.mention} (`{channel.id}`)", discord.Color.green(), "🎬")
    mensaje = f"Canal creado: {channel.mention}"
    if emoji_warn:
        mensaje += f"\n{emoji_warn}"
    await interaction.followup.send(mensaje, ephemeral=True)


async def _create_clip_channel_for_member(
    guild: discord.Guild, member: discord.Member, emoji_text: str
):
    logger.info(
        "🎬 [Clips] _create_clip_channel_for_member | member=%s (%s) emoji_raw=%r",
        member,
        member.id,
        emoji_text,
    )
    record = get_clip_channel_record(member.id)
    if record:
        existing = guild.get_channel(int(record["channel_id"]))
        if existing:
            logger.info("🎬 [Clips] Canal ya existente para miembro | channel_id=%s", existing.id)
            return existing, None, f"Ya tiene un canal: {existing.mention}"
        logger.info("🎬 [Clips] Registro huérfano eliminado | user_id=%s", member.id)
        delete_clip_channel(member.id)

    category = _categoria_disponible_para_clips(guild)
    if not isinstance(category, discord.CategoryChannel):
        logger.error(
            "❌ [Clips] No hay categoría válida disponible | category_id=%s fallback_id=%s",
            CLIPS_CATEGORY_ID,
            CLIPS_FALLBACK_CATEGORY_ID,
        )
        return None, None, "❌ No se encontró la categoría configurada."

    slug        = _normalize_channel_display_name(member)
    logger.info("🎬 [Clips] Nombre base normalizado=%r", slug)
    emoji_text, emoji_warn, emoji_ok = _normalize_clip_emoji(emoji_text, guild)
    logger.info("🎬 [Clips] Emoji normalizado=%r warn=%r ok=%s", emoji_text, emoji_warn, emoji_ok)
    if not emoji_ok:
        return None, None, emoji_warn

    use_emoji    = bool(emoji_text) and _is_unicode_emoji(emoji_text)
    channel_name = f"{slug}{emoji_text}" if use_emoji else slug
    channel_name = channel_name[:90]
    overwrites   = _build_clip_overwrites(guild, member, True, True)
    logger.info(
        "🎬 [Clips] Creando canal miembro | name=%r use_emoji=%s category=%s overwrites=%s",
        channel_name,
        use_emoji,
        getattr(category, "id", None),
        len(overwrites),
    )

    try:
        logger.info("🎬 [Clips] Intentando create_text_channel principal")
        channel = await guild.create_text_channel(
            channel_name, category=category, overwrites=overwrites,
            reason=f"Canal de clips para {member} ({member.id})",
        )
        logger.info("✅ [Clips] Canal creado OK | id=%s name=%s", channel.id, channel.name)
    except discord.Forbidden as e:
        logger.exception(
            "❌ [Clips] Forbidden creando canal miembro | member=%s (%s) category=%s name=%r error=%s",
            member,
            member.id,
            getattr(category, "id", None),
            channel_name,
            e,
        )
        return None, None, "❌ No tengo permisos para crear el canal en esa categoría."
    except discord.HTTPException as e:
        logger.exception(
            "❌ [Clips] HTTPException creando canal miembro principal | status=%s code=%s text=%r",
            getattr(e, "status", None),
            getattr(e, "code", None),
            getattr(e, "text", None),
        )
        if use_emoji:
            try:
                logger.info("🎬 [Clips] Reintentando canal miembro sin emoji")
                channel = await guild.create_text_channel(
                    slug, category=category, overwrites=overwrites,
                    reason=f"Canal de clips para {member} ({member.id})",
                )
                logger.info("✅ [Clips] Canal creado sin emoji | id=%s name=%s", channel.id, channel.name)
            except discord.HTTPException as e:
                logger.exception(
                    "❌ [Clips] HTTPException creando canal miembro sin emoji | status=%s code=%s text=%r",
                    getattr(e, "status", None),
                    getattr(e, "code", None),
                    getattr(e, "text", None),
                )
                return None, None, "❌ Error creando el canal. Revisa los logs del bot."
        else:
            return None, None, "❌ Error creando el canal."

    logger.info("🎬 [Clips] Guardando registro del canal miembro | user=%s channel=%s", member.id, channel.id)
    upsert_clip_channel(member.id, channel.id)
    try:
        logger.info("🎬 [Clips] Enviando mensaje inicial al canal miembro %s", channel.id)
        await channel.send(f"{member.mention} tu canal de clips fue creado.{' ' + emoji_text if emoji_text else ''}")
    except discord.HTTPException as e:
        logger.warning("⚠️ [Clips] No se pudo enviar mensaje inicial al canal miembro %s: %s", channel.id, e)
    return channel, emoji_warn, None


# ─── MODAL EMOJI ──────────────────────────────────────────────

class ClipEmojiModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Crear canal de clips")
        self.emoji = discord.ui.TextInput(
            label="Emoji opcional (solo emoji o :nombre:)",
            placeholder="Ej: 🐐 o :goat: | Si no querés emoji, dejalo vacío",
            required=False,
            max_length=32,
        )
        self.add_item(self.emoji)

    async def on_submit(self, interaction: discord.Interaction):
        await _create_clip_channel(interaction, self.emoji.value)


# ─── PANEL USUARIO ────────────────────────────────────────────

class ClipChannelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Crear mi canal de clips",
        style=discord.ButtonStyle.primary,
        emoji="🎬",
        custom_id="clips_create_button",
    )
    async def crear(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_ids = {r.id for r in interaction.user.roles}
        if (
            CLIPS_CREATOR_ROLE_ID not in role_ids
            and DEVELOPER_ROLE_ID not in role_ids
            and interaction.user.id not in DEVELOPER_USER_IDS
        ):
            await interaction.response.send_message("⛔ No tienes permisos.", ephemeral=True)
            return
        await interaction.response.send_modal(ClipEmojiModal())


# ─── MODALS ADMIN ─────────────────────────────────────────────

class ClipAdminDeleteModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Borrar canal de clips")
        self.channel_id = discord.ui.TextInput(label="ID del canal", placeholder="1469...", required=True, max_length=32)
        self.add_item(self.channel_id)

    async def on_submit(self, interaction: discord.Interaction):
        from log_actions import log_accion
        if not es_admin_clips(interaction.user):
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
        if not channel.category or channel.category.id not in CLIPS_ALLOWED_CATEGORY_IDS:
            await interaction.response.send_message("❌ Canal fuera de categorías autorizadas.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await channel.delete(reason=f"Admin delete by {interaction.user} ({interaction.user.id})")
        except discord.Forbidden:
            await interaction.followup.send("❌ Sin permisos para borrar.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            return
        delete_clip_channel_by_channel_id(channel_id)
        await log_accion(interaction.user, "Borró canal de clips", f"#{channel.name} (`{channel_id}`)", discord.Color.red(), "🗑️")
        await interaction.followup.send("Canal borrado.", ephemeral=True)


class ClipAdminHideModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Ocultar canal de clips")
        self.channel_id = discord.ui.TextInput(label="ID del canal", placeholder="1469...", required=True, max_length=32)
        self.add_item(self.channel_id)

    async def on_submit(self, interaction: discord.Interaction):
        from log_actions import log_accion
        if not es_admin_clips(interaction.user):
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
        if not channel.category or channel.category.id not in CLIPS_ALLOWED_CATEGORY_IDS:
            await interaction.response.send_message("❌ Canal fuera de categorías autorizadas.", ephemeral=True)
            return
        record = get_clip_channel_record_by_channel_id(channel_id)
        member = interaction.guild.get_member(int(record["user_id"])) if record else None
        await interaction.response.defer(ephemeral=True)
        try:
            overwrites = _merge_clip_overwrites(channel, member, False, False)
            await channel.edit(overwrites=overwrites)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            return
        await log_accion(interaction.user, "Ocultó canal de clips", f"#{channel.name} (`{channel_id}`)", discord.Color.greyple(), "👁️")
        await interaction.followup.send("Canal ocultado.", ephemeral=True)


class ClipAdminUnhideModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Desocultar canal de clips")
        self.channel_id = discord.ui.TextInput(label="ID del canal", placeholder="1469...", required=True, max_length=32)
        self.add_item(self.channel_id)

    async def on_submit(self, interaction: discord.Interaction):
        from log_actions import log_accion
        if not es_admin_clips(interaction.user):
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
        if not channel.category or channel.category.id not in CLIPS_ALLOWED_CATEGORY_IDS:
            await interaction.response.send_message("❌ Canal fuera de categorías autorizadas.", ephemeral=True)
            return
        record = get_clip_channel_record_by_channel_id(channel_id)
        member = interaction.guild.get_member(int(record["user_id"])) if record else None
        await interaction.response.defer(ephemeral=True)
        try:
            overwrites = _merge_clip_overwrites(channel, member, True, True)
            await channel.edit(overwrites=overwrites)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            return
        await log_accion(interaction.user, "Desocultó canal de clips", f"#{channel.name} (`{channel_id}`)", discord.Color.green(), "✅")
        await interaction.followup.send("Canal desocultado.", ephemeral=True)


class ClipAdminRenameModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Renombrar canal de clips")
        self.channel_id = discord.ui.TextInput(label="ID del canal", placeholder="1469...", required=True, max_length=32)
        self.new_name   = discord.ui.TextInput(label="Nuevo nombre (emoji permitido)", placeholder="Ej: nombre🐐", required=True, max_length=90)
        self.add_item(self.channel_id)
        self.add_item(self.new_name)

    async def on_submit(self, interaction: discord.Interaction):
        from log_actions import log_accion
        if not es_admin_clips(interaction.user):
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
        if not channel.category or channel.category.id not in CLIPS_ALLOWED_CATEGORY_IDS:
            await interaction.response.send_message("❌ Canal fuera de categorías autorizadas.", ephemeral=True)
            return

        raw_name = (self.new_name.value or "").strip()
        if _is_custom_emoji(raw_name):
            await interaction.response.send_message("❌ No se permiten emojis de Discord en el nombre.", ephemeral=True)
            return

        name_part, emoji_suffix = _split_trailing_unicode_emoji(raw_name)
        if emoji_suffix and not _is_single_unicode_emoji(emoji_suffix):
            await interaction.response.send_message("❌ Solo se permite un emoji por canal.", ephemeral=True)
            return

        name_slug = _normalize_channel_name_from_raw(name_part or raw_name)
        if emoji_suffix:
            new_name = f"{name_slug}{emoji_suffix}" if name_slug else emoji_suffix
        else:
            new_name = _normalize_channel_name_from_raw(raw_name)

        if not new_name:
            await interaction.response.send_message("❌ Nombre inválido.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await channel.edit(name=new_name)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            return

        await log_accion(interaction.user, "Renombró canal de clips", f"#{channel.name} → `{new_name}` (`{channel_id}`)", discord.Color.blue(), "✏️")
        await interaction.followup.send(f"Canal renombrado a `{new_name}`.", ephemeral=True)


class ClipAdminCreateModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Crear o registrar canal de clips")
        self.channel_id = discord.ui.TextInput(label="ID del canal (opcional)", placeholder="1469...", required=False, max_length=32)
        self.user_id    = discord.ui.TextInput(label="ID del usuario",           placeholder="1234...", required=True,  max_length=32)
        self.emoji      = discord.ui.TextInput(label="Emoji (opcional)",         placeholder="Ej: 🐐 o :goat:", required=False, max_length=32)
        self.add_item(self.channel_id)
        self.add_item(self.user_id)
        self.add_item(self.emoji)

    async def on_submit(self, interaction: discord.Interaction):
        from log_actions import log_accion
        if not es_admin_clips(interaction.user):
            await interaction.response.send_message("⛔ Sin permisos.", ephemeral=True)
            return
        if not interaction.guild:
            await interaction.response.send_message("❌ Solo funciona en el servidor.", ephemeral=True)
            return

        user_id = _extract_id_from_text(self.user_id.value)
        if not user_id:
            await interaction.response.send_message("❌ ID de usuario inválido.", ephemeral=True)
            return
        member = interaction.guild.get_member(user_id)
        if not member:
            await interaction.response.send_message("❌ Usuario no encontrado.", ephemeral=True)
            return

        emoji_text, emoji_warn, emoji_ok = _normalize_clip_emoji(self.emoji.value, interaction.guild)
        if not emoji_ok:
            await interaction.response.send_message(emoji_warn, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        channel_id = _extract_id_from_text(self.channel_id.value)
        if channel_id:
            channel = interaction.guild.get_channel(channel_id)
            if not channel:
                await interaction.followup.send("❌ Canal no encontrado.", ephemeral=True)
                return
            if not channel.category or channel.category.id not in CLIPS_ALLOWED_CATEGORY_IDS:
                await interaction.followup.send("❌ Canal fuera de categorías autorizadas.", ephemeral=True)
                return
            try:
                overwrites = _merge_clip_overwrites(channel, member, True, True)
                await channel.edit(overwrites=overwrites)
            except (discord.Forbidden, discord.HTTPException) as e:
                await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
                return
            upsert_clip_channel(member.id, channel.id)
            await log_accion(interaction.user, "Registró canal de clips", f"{channel.mention} → {member.mention}", discord.Color.green(), "➕")
            mensaje = f"Canal registrado: {channel.mention}"
            if emoji_text:
                mensaje += "\nℹ️ El emoji no se aplica a canales existentes."
            await interaction.followup.send(mensaje, ephemeral=True)
            return

        channel, emoji_warn, error = await _create_clip_channel_for_member(interaction.guild, member, emoji_text)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        await log_accion(interaction.user, "Creó canal de clips (admin)", f"{channel.mention} → {member.mention}", discord.Color.green(), "➕")
        mensaje = f"Canal creado: {channel.mention}"
        if emoji_warn:
            mensaje += f"\n{emoji_warn}"
        await interaction.followup.send(mensaje, ephemeral=True)


# ─── PANEL ADMIN ──────────────────────────────────────────────

class ClipAdminPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Borrar canal",     style=discord.ButtonStyle.danger,     custom_id="clips_admin_delete")
    async def borrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not es_admin_clips(interaction.user):
            await interaction.response.send_message("⛔ Sin permisos.", ephemeral=True)
            return
        await interaction.response.send_modal(ClipAdminDeleteModal())

    @discord.ui.button(label="Ocultar canal",    style=discord.ButtonStyle.secondary,  custom_id="clips_admin_hide")
    async def ocultar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not es_admin_clips(interaction.user):
            await interaction.response.send_message("⛔ Sin permisos.", ephemeral=True)
            return
        await interaction.response.send_modal(ClipAdminHideModal())

    @discord.ui.button(label="Desocultar canal", style=discord.ButtonStyle.secondary,  custom_id="clips_admin_unhide")
    async def desocultar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not es_admin_clips(interaction.user):
            await interaction.response.send_message("⛔ Sin permisos.", ephemeral=True)
            return
        await interaction.response.send_modal(ClipAdminUnhideModal())

    @discord.ui.button(label="Renombrar canal",  style=discord.ButtonStyle.primary,    custom_id="clips_admin_rename")
    async def renombrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not es_admin_clips(interaction.user):
            await interaction.response.send_message("⛔ Sin permisos.", ephemeral=True)
            return
        await interaction.response.send_modal(ClipAdminRenameModal())

    @discord.ui.button(label="Crear canal",      style=discord.ButtonStyle.success,    custom_id="clips_admin_create")
    async def crear(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not es_admin_clips(interaction.user):
            await interaction.response.send_message("⛔ Sin permisos.", ephemeral=True)
            return
        await interaction.response.send_modal(ClipAdminCreateModal())
