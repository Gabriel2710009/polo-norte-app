import re
import logging
from typing import Optional

import discord

from config import (
    ARMERO_ROLE_ID,
    ALTO_CARGO_ROLE_ID,
    DEVELOPER_ROLE_ID,
    DEVELOPER_USER_IDS,
    CLIPS_VIEW_ROLE_ID,
    TRADUCCIONES,
)

logger = logging.getLogger("ArmamentBot")


# ─── TRADUCCIÓN ───────────────────────────────────────────────

def traducir_objeto(nombre_objeto: Optional[str]) -> str:
    if not nombre_objeto:
        return "Desconocido"
    return TRADUCCIONES.get(nombre_objeto.strip(), nombre_objeto.strip())


def normalizar_objeto(objeto: str) -> str:
    if not objeto:
        return ""
    return objeto.lower().strip()


# ─── VERIFICACIÓN DE ROLES ────────────────────────────────────

def es_armero(user: discord.Member) -> bool:
    """Armero O Developer (por rol o por user ID)."""
    role_ids = {r.id for r in user.roles}
    return (
        ARMERO_ROLE_ID    in role_ids
        or DEVELOPER_ROLE_ID in role_ids
        or user.id in DEVELOPER_USER_IDS
    )


# Modificar es_armero_o_alto_cargo():
def es_armero_o_alto_cargo(member: discord.Member) -> bool:
    """Armero, Alto Cargo O Developer (por rol o por user ID)."""
    role_ids = {r.id for r in member.roles}
    if ARMERO_ROLE_ID in role_ids:
        logger.info(f"🔫 {member.name} ES ARMERO")
        return True
    if DEVELOPER_ROLE_ID in role_ids or member.id in DEVELOPER_USER_IDS:
        logger.info(f"👨‍💻 {member.name} ES DEVELOPER")
        return True
    if ALTO_CARGO_ROLE_ID in role_ids:
        logger.info(f"👑 {member.name} ES ALTO CARGO")
        return True
    logger.info(f"❌ {member.name} NO es armero ni alto cargo")
    return False

# Modificar es_admin_clips():
def es_admin_clips(member: Optional[discord.Member]) -> bool:
    """Alto Cargo O Developer (admin de clips/voz)."""
    if not member:
        return False
    role_ids = {r.id for r in member.roles}
    return (
        ALTO_CARGO_ROLE_ID in role_ids
        or DEVELOPER_ROLE_ID in role_ids
        or member.id in DEVELOPER_USER_IDS
    )


def _rol_aprobador(member: discord.Member) -> str:
    role_ids = {r.id for r in member.roles}
    if ARMERO_ROLE_ID in role_ids:
        return "armero"
    if ALTO_CARGO_ROLE_ID in role_ids:
        return "alto rango"
    return "moderador"


# ─── HELPERS DE CANAL / NOMBRE ────────────────────────────────

def _slugify_channel_name(name: str) -> str:
    base = name.lower().strip()
    base = re.sub(r"ℹ️ +", "-", base)
    base = re.sub(r"[^a-z0-9\-]", "", base)
    base = re.sub(r"-{2,}", "-", base).strip("-")
    return base[:80] or "canal"


def _normalize_channel_display_name(member: discord.Member) -> str:
    raw = (member.nick or member.display_name or member.name or "").strip()

    def _sanitize(text: str) -> str:
        text = re.sub(r"ℹ️ +", "-", text.strip())
        text = re.sub(r"[^\w\-]", "", text, flags=re.UNICODE)
        text = re.sub(r"-{2,}", "-", text).strip("-")
        return text

    cleaned = _sanitize(raw)
    if cleaned:
        return cleaned[:80]
    fallback = _sanitize(member.name or "")
    return (fallback or "canal")[:80]


def _normalize_channel_name_from_raw(raw: str) -> str:
    text = re.sub(r"ℹ️ +", "-", (raw or "").strip())
    text = re.sub(r"[^\w\-]", "", text, flags=re.UNICODE)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:80]


# ─── HELPERS DE EMOJI ─────────────────────────────────────────

_UNICODE_EMOJI_RE = re.compile(
    r"^(?:"
    r"[\U0001F300-\U0001FAFF]"
    r"|[\u2600-\u26FF]"
    r"|[\u2700-\u27BF]"
    r"|[\u2300-\u23FF]"
    r"|\u200d|\ufe0f"
    r")+?$"
)
_UNICODE_EMOJI_BASE_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2300-\u23FF]"
)


def _is_custom_emoji(text: str) -> bool:
    return bool(re.fullmatch(r"<a?:\w+:\d+>", text or ""))


def _is_unicode_emoji(text: str) -> bool:
    text = (text or "").strip()
    return bool(text) and bool(_UNICODE_EMOJI_RE.fullmatch(text))


def _is_single_unicode_emoji(text: str) -> bool:
    text = (text or "").strip()
    if not _is_unicode_emoji(text):
        return False
    return len(_UNICODE_EMOJI_BASE_RE.findall(text)) == 1


def _split_trailing_unicode_emoji(text: str):
    raw = (text or "").strip()
    if not raw:
        return "", ""
    match = re.match(
        r"^(.*?)([\U0001F300-\U0001FAFF\u2600-\u27BF\u2300-\u23FF]"
        r"[\uFE0F\u200D\U0001F300-\U0001FAFF\u2600-\u27BF\u2300-\u23FF]*)$",
        raw,
    )
    if not match:
        return raw, ""
    name_part  = match.group(1).strip()
    emoji_part = match.group(2).strip()
    if _is_single_unicode_emoji(emoji_part):
        return name_part, emoji_part
    return raw, ""


def _resolve_discord_emoji_short_name(
    text: str, guild: Optional[discord.Guild]
) -> Optional[discord.Emoji]:
    if not guild:
        return None
    match = re.fullmatch(r":([a-zA-Z0-9_]{2,32}):", text or "")
    if not match:
        return None
    return discord.utils.get(guild.emojis, name=match.group(1))


def _normalize_clip_emoji(raw_text: str, guild: Optional[discord.Guild]):
    """Devuelve (emoji_str, advertencia_o_None, ok: bool)."""
    text = (raw_text or "").strip()
    if not text:
        return "", None, True

    if _is_custom_emoji(text):
        return text, None, True

    resolved = _resolve_discord_emoji_short_name(text, guild)
    if resolved:
        return str(resolved), None, True

    if re.fullmatch(r":\w+:", text):
        return (
            "",
            f"Aviso: no se pudo reconocer el emoji de Discord {text}. "
            "Se creará el canal sin emoji.",
            True,
        )

    if _is_unicode_emoji(text):
        if not _is_single_unicode_emoji(text):
            return "", "Solo se permite un emoji por canal.", False
        return text, None, True

    return (
        "",
        "Solo se permiten emojis (Unicode o de Discord como :nombre:). "
        "No se permiten nombres ni texto.",
        False,
    )


def _extract_id_from_text(raw: str) -> Optional[int]:
    match = re.search(r"\d{5,}", raw or "")
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None