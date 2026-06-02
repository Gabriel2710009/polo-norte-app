import re

def _strip_emoji(line: str) -> str:
    return re.sub(r'^`[^`]+`\s*', '', line).strip()

def extract_items(text: str) -> list[dict]:
    items = []
    for line in text.strip().split("\n"):
        cleaned = _strip_emoji(line)
        if "item" not in cleaned.lower() and "🔫" not in cleaned and "▪" not in cleaned.lower():
            continue
        m = re.search(r"x(\d+)\s+(.+)", cleaned, re.IGNORECASE)
        if m:
            items.append({"name": m.group(2).strip().rstrip("`"), "quantity": int(m.group(1))})
    return items

def parse_embed(text: str) -> dict:
    result = {
        "action": None, "player": None, "identifier": None,
        "discord": None, "discord_id": None, "stash_id": None,
        "items": [], "coords": None,
    }

    title = text.strip().split("\n")[0].upper() if text.strip() else ""
    if re.search(r"GUARDAR|GUARDÓ|METIDO|DEPOSITO|DEPÓSITO", title):
        result["action"] = "STASH"
    elif re.search(r"SACAR|SACÓ|RETIRO", title):
        result["action"] = "RETRIEVE"

    for line in text.split("\n"):
        l = _strip_emoji(line)
        if not l:
            continue

        m = re.search(r"\*\*Jugador\*\*:\s*(.+)", l, re.IGNORECASE)
        if not m:
            m = re.search(r"Jugador:\s*(.+)", l, re.IGNORECASE)
        if m:
            result["player"] = m.group(1).strip().lstrip("😊").strip()

        m = re.search(r"\*\*Identificador\*\*:\s*(.+)", l, re.IGNORECASE)
        if not m:
            m = re.search(r"Identificador:\s*(.+)", l, re.IGNORECASE)
        if m:
            result["identifier"] = m.group(1).strip()

        m = re.search(r"\*\*Discord\*\*:\s*(.+)", l, re.IGNORECASE)
        if not m:
            m = re.search(r"Discord:\s*(.+)", l, re.IGNORECASE)
        if m:
            raw = m.group(1).strip()
            result["discord"] = raw
            id_m = re.search(r"(\d{17,20})", raw)
            if id_m:
                result["discord_id"] = id_m.group(1)

        m = re.search(r"\*\*ID\*\*:\s*(.+)", l, re.IGNORECASE)
        if not m:
            m = re.search(r"(?:🔢)?\s*ID:\s*(.+)", l, re.IGNORECASE)
        if m:
            result["stash_id"] = m.group(1).strip()

        m = re.search(r"\*\*Item\*\*:\s*x?(\d+)?\s*(.+)", l, re.IGNORECASE)
        if not m:
            m = re.search(r"Item:\s*x?(\d+)?\s*(.+)", l, re.IGNORECASE)
        if m:
            qty = int(m.group(1)) if m.group(1) else 1
            name = m.group(2).strip().rstrip("`")
            result["items"].append({"name": name, "quantity": qty})

        m = re.search(r"\*\*Coordenadas\*\*:\s*(.+)", l, re.IGNORECASE)
        if not m:
            m = re.search(r"Coordenadas:\s*(.+)", l, re.IGNORECASE)
        if m:
            result["coords"] = m.group(1).strip().strip("`")

    return result
