import sys
from parser import parse_embed, extract_items
from items import is_allowed, normalize, ITEM_ALIASES

ROLE_ID = "<@&978342236771217480>"
CHANNEL_ID = "1447055354339786762"

def validate(text: str) -> str:
    parsed = parse_embed(text)
    raw_items = parsed.get("items", [])
    if not raw_items:
        raw_items = extract_items(text)

    illegal = []
    all_items_display = []

    for item in raw_items:
        name = item.get("name", "")
        qty = item.get("quantity", 1)
        display = f"{name} x{qty}" if qty > 1 else name
        all_items_display.append(display)

        norm = normalize(name)
        if norm in ITEM_ALIASES or not is_allowed(norm):
            if norm not in [normalize(x) for x in illegal]:
                illegal.append(display)

    if not illegal:
        return "STATUS: OK\nALERT: false"

    illegal_str = ", ".join(illegal)
    items_str = ", ".join(all_items_display) if all_items_display else illegal_str

    return (
        f"STATUS: ALERT\n"
        f"ALERT: true\n"
        f"ILLEGAL_ITEMS: {illegal_str}\n"
        f"SEVERITY: HIGH\n"
        f"\n"
        f"DISCORD_ALERT:\n"
        f"SEND: true\n"
        f"PING_ROLE: {ROLE_ID}\n"
        f"CHANNEL_ID: {CHANNEL_ID}\n"
        f"\n"
        f"MESSAGE:\n"
        f"Unauthorized inventory detected.\n"
        f"Detected: {items_str}"
    )

if __name__ == "__main__":
    text = sys.stdin.read()
    print(validate(text))
