from __future__ import annotations

import asyncio
import logging
import os as _os
from urllib import request as urllib_request

logger = logging.getLogger("ArmamentBot")

# Sightengine is the moderation backend used by the bot.
SIGHTENGINE_API_USER = _os.getenv("SIGHTENGINE_API_USER", "")
SIGHTENGINE_API_SECRET = _os.getenv("SIGHTENGINE_API_SECRET", "")
SIGHTENGINE_ENABLED = bool(SIGHTENGINE_API_USER and SIGHTENGINE_API_SECRET)

_SIGHTENGINE_URL = "https://api.sightengine.com/1.0/text/check.json"
_SIGHTENGINE_MODE = "rules"
_SIGHTENGINE_CATEGORIES = (
    "profanity,personal,link,drug,weapon,violence,self-harm,medical,"
    "extremism,spam,content-trade,money-transaction"
)
_SIGHTENGINE_LANG = "es"


async def moderar_texto_sightengine(texto: str) -> dict:
    """
    Modera texto usando Sightengine.
    Retorna:
      { toxic: bool, score: float, reasons: list, sightengine_used: bool }
    """
    if not SIGHTENGINE_ENABLED:
        return {"toxic": False, "score": 0.0, "reasons": [], "sightengine_used": False}

    try:
        import json as _json
        import urllib.parse as _urllib_parse

        params = _urllib_parse.urlencode({
            "text": (texto or "")[:800],
            "lang": _SIGHTENGINE_LANG,
            "mode": _SIGHTENGINE_MODE,
            "categories": _SIGHTENGINE_CATEGORIES,
            "api_user": SIGHTENGINE_API_USER,
            "api_secret": SIGHTENGINE_API_SECRET,
        })
        url = _SIGHTENGINE_URL + "?" + params

        def _do_request():
            req = urllib_request.Request(url, method="POST")
            with urllib_request.urlopen(req, timeout=10) as resp:
                return _json.loads(resp.read().decode("utf-8"))

        data = await asyncio.to_thread(_do_request)

        if data.get("status") != "success":
            return {
                "toxic": False,
                "score": 0.0,
                "reasons": ["sightengine_error"],
                "sightengine_used": True,
            }

        reasons = []
        max_score = 0.0

        intensity_scores = {
            "low": 0.35,
            "medium": 0.70,
            "high": 0.95,
        }

        for category in (
            "profanity",
            "personal",
            "link",
            "drug",
            "weapon",
            "violence",
            "self-harm",
            "medical",
            "extremism",
            "spam",
            "content-trade",
            "money-transaction",
        ):
            block = data.get(category, {}) or {}
            matches = block.get("matches") or []
            if not matches:
                continue

            for match in matches:
                intensity = str(match.get("intensity", "") or "").lower()
                score = intensity_scores.get(intensity, 1.0)
                if score > max_score:
                    max_score = score
                match_type = match.get("type") or category
                matched_text = match.get("match") or ""
                reasons.append(f"sightengine:{category}:{match_type}:{matched_text}")

        toxic = bool(reasons)
        return {
            "toxic": toxic,
            "score": max_score,
            "reasons": reasons,
            "sightengine_used": True,
        }
    except Exception as e:
        logger.warning(f"⚠️ [Sightengine] Error: {e}")
        return {"toxic": False, "score": 0.0, "reasons": ["sightengine_unavailable"], "sightengine_used": False}


async def moderate_text(text: str) -> dict:
    return await moderar_texto_sightengine(text)


def moderate_text_sync(text: str) -> dict:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(moderate_text(text))
    raise RuntimeError("moderate_text_sync() no puede ejecutarse dentro de un event loop; usá 'await moderate_text(...)'")
