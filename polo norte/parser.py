import re

def extract_items(text: str) -> list[dict]:
    items = []
    lines = text.strip().split("\n")
    for line in lines:
        line_lower = line.lower()
        if "item:" not in line_lower and "🔫" not in line and "▪" not in line_lower:
            continue
        m = re.search(r"x(\d+)\s+(\S+)", line, re.IGNORECASE)
        if not m:
            m = re.search(r"(?:item|objeto)[:\s]*x?(\d+)?\s*[`\"]?(\w[\w\s]*)", line, re.IGNORECASE)
        if m:
            qty = int(m.group(1)) if m.lastindex and m.group(1) else 1
            name = m.group(2).strip().rstrip("`")
            items.append({"name": name, "quantity": qty})
    return items

def parse_embed(text: str) -> dict:
    result = {"action": None, "player": None, "identifier": None, "discord": None, "items": []}

    if "guardar" in text.lower() or "guardó" in text.lower() or "metido" in text.lower() or "deposito" in text.lower():
        result["action"] = "STASH"
    elif "sacar" in text.lower() or "sacó" in text.lower() or "retiro" in text.lower() or "sacado" in text.lower():
        result["action"] = "RETRIEVE"

    for line in text.split("\n"):
        l = line.strip()
        if "jugador" in l.lower() or "😊" in l:
            m = re.search(r"jugador:\s*(\[?\d*\]?\s*.+)", l, re.IGNORECASE)
            if m:
                result["player"] = re.sub(r"[😊\s]+", "", m.group(1)).strip()
        if "identificador" in l.lower() or "🎮" in l:
            m = re.search(r"identificador:\s*(.+)", l, re.IGNORECASE)
            if m:
                result["identifier"] = m.group(1).strip()
        if "discord" in l.lower() or "💬" in l:
            m = re.search(r"discord:\s*(.+)", l, re.IGNORECASE)
            if m:
                result["discord"] = m.group(1).strip()
        if "item" in l.lower() or "🔫" in l:
            m = re.search(r"x(\d+)\s+(\S+)", l, re.IGNORECASE)
            if m:
                result["items"].append({"name": m.group(2), "quantity": int(m.group(1))})

    return result
