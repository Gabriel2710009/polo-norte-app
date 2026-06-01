import os
from typing import Optional

# ─── TOKEN / DB ───────────────────────────────────────────────
TOKEN        = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")


def _env_optional_int(name: str) -> Optional[int]:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return int(value)

# ─── LOGGING ──────────────────────────────────────────────────
LOG_LEVEL          = os.getenv("LOG_LEVEL", "INFO").upper()
BOT_LOGS_CHANNEL_ID = int(os.getenv("BOT_LOGS_CHANNEL_ID", "1482930516393984142"))

# ─── CANALES ──────────────────────────────────────────────────
LOGS_CHANNEL_ID                  = int(os.getenv("LOGS_CHANNEL_ID",                  "1219151502266863727"))
ARMARIO_CHANNEL_ID               = int(os.getenv("ARMARIO_CHANNEL_ID",               "1219151502266863727"))  # canal donde llegan los retiros/depósitos
ALERTAS_CHANNEL_ID               = int(os.getenv("ALERTAS_CHANNEL_ID",               "1447055354339786762"))
ALERTAS_ANTIRROBO_CHANNEL_ID     = int(os.getenv("ALERTAS_ANTIRROBO_CHANNEL_ID",     "968286555846377532"))
DEPOSITO_SOLICITUD_CHANNEL_ID    = int(os.getenv("DEPOSITO_SOLICITUD_CHANNEL_ID",    "1216861533208973412"))
RAZON_RETIRO_CHANNEL_ID          = int(os.getenv("RAZON_RETIRO_CHANNEL_ID",          "1482229060041179156"))
ONLY_ARMEROS_CHANNEL_ID          = int(os.getenv("ONLY_ARMEROS_CHANNEL_ID",          "968286555846377532"))
ASISTENCIA_SEMANAL_CHANNEL_ID    = int(os.getenv("ASISTENCIA_SEMANAL_CHANNEL_ID",    str(ONLY_ARMEROS_CHANNEL_ID)))
JUSTIFICACION_CHANNEL_ID         = int(os.getenv("JUSTIFICACION_CHANNEL_ID",         "1390748641454723223"))
PLANTILLA_AUTOMATICA_CHANNEL_ID  = int(os.getenv("PLANTILLA_AUTOMATICA_CHANNEL_ID", "968286556056068104"))
INACTIVIDAD_JUSTIFICADA_ROLE_ID  = int(os.getenv("INACTIVIDAD_JUSTIFICADA_ROLE_ID",  "980229207210737744"))
EVENTOS_CHANNEL_ID               = int(os.getenv("EVENTOS_CHANNEL_ID",               "1001499534557446154"))
OPERATIVOS_CHANNEL_ID            = int(os.getenv("OPERATIVOS_CHANNEL_ID",            "1390747283112464556"))
CLIPS_PANEL_CHANNEL_ID           = int(os.getenv("CLIPS_PANEL_CHANNEL_ID",           "1469042484268105869"))
CLIPS_ADMIN_PANEL_CHANNEL_ID     = int(os.getenv("CLIPS_ADMIN_PANEL_CHANNEL_ID",     "1469183178773237852"))
VOICE_ADMIN_PANEL_CHANNEL_ID = int(os.getenv("VOICE_ADMIN_PANEL_CHANNEL_ID", "1469183178773237852"))
VOICE_CATEGORY_ID = int(os.getenv("VOICE_CATEGORY_ID", "968286555418554500"))
VOICE_ALLOWED_CATEGORY_IDS = {
    968286555418554500,
}
VOICE_CHANNEL_OPERATIVO_1 = int(os.getenv("VOICE_CHANNEL_OPERATIVO_1", "1070462930539270235"))
VOICE_CHANNEL_OPERATIVO_2 = int(os.getenv("VOICE_CHANNEL_OPERATIVO_2", "1413285032088436756"))

# Integraciones opcionales
CHEMI_PAYICO_ROLE_ID          = _env_optional_int("CHEMI_PAYICO_ROLE_ID")
CHEMI_DEUDA_ROLE_ID           = int(os.getenv("CHEMI_DEUDA_ROLE_ID", "1501386381119721563"))
CHEMI_AVISO_CHANNEL_ID        = int(os.getenv("CHEMI_AVISO_CHANNEL_ID", "1501389273528664094"))
CHEMI_ALTOS_CARGOS_CHANNEL_ID = int(os.getenv("CHEMI_ALTOS_CARGOS_CHANNEL_ID", "968286555846377532"))

# ─── ROLES ────────────────────────────────────────────────────.
ARMERO_ROLE_ID         = int(os.getenv("ARMERO_ROLE_ID",         "978342236771217480"))
OPERATIVO_ROLE_ID      = int(os.getenv("OPERATIVO_ROLE_ID",      "1212120053936427049"))
OPERATIVO_ROLE_ID_2    = int(os.getenv("OPERATIVO_ROLE_ID_2",    "1040677618418200658"))
ALTO_CARGO_ROLE_ID     = 968286555204616255
DEVELOPER_ROLE_ID      = int(os.getenv("DEVELOPER_ROLE_ID",      "1469212211506577482"))
DEVELOPER_USER_IDS: set[int] = {
    691475896019714139,
}
CLIPS_CREATOR_ROLE_ID  = int(os.getenv("CLIPS_CREATOR_ROLE_ID",  "1212120053936427049"))
CLIPS_VIEW_ROLE_ID     = int(os.getenv("CLIPS_VIEW_ROLE_ID",     "1212120053936427049"))

# ─── CATEGORÍAS CLIPS ─────────────────────────────────────────
CLIPS_CATEGORY_ID = int(os.getenv("CLIPS_CATEGORY_ID", "1016927588247154718"))
CLIPS_FALLBACK_CATEGORY_ID = int(os.getenv("CLIPS_FALLBACK_CATEGORY_ID", "1501424531502534738"))
CLIPS_ALLOWED_CATEGORY_IDS = {
    1016927588247154718,
    1501424531502534738,
    1170079964696150087,
}

# ─── TIEMPOS ──────────────────────────────────────────────────
TIEMPO_DEVOLUCION          = 15    # segundos
TIEMPO_RAZON_RETIRO        = 300   # 5 minutos
TIEMPO_DEVOLUCION_SOLICITUD = 1800  # 30 minutos

# Asistencia semanal
ASISTENCIA_SEMANAL_OBJETIVO = int(os.getenv("ASISTENCIA_SEMANAL_OBJETIVO", "4"))

# Secretos opcionales
PERSPECTIVE_API_KEY = os.getenv("PERSPECTIVE_API_KEY")

# ─── PAGINACIÓN ───────────────────────────────────────────────
ITEMS_POR_PAGINA = 25

# ─── ANTIRROBO CACHE ──────────────────────────────────────────
ANTIRROBO_CACHE_MINUTOS = 15

# ─── TRADUCCIONES ─────────────────────────────────────────────
TRADUCCIONES = {
    # Pistolas
    "WEAPON_PISTOL":        "9MM",
    "WEAPON_PISTOL_MK2":    "9MM MK2",
    "WEAPON_SNSPISTOL_MK2": "Pistola SNS",
    "WEAPON_DOUBLEACTION":  "Revolver",
    "WEAPON_COMBATPISTOL":  "Glock",
    "WEAPON_PISTOL50":      "Calibre.50",
    "WEAPON_ASSAULTRIFLE":  "Rifle de Asalto",
    "WEAPON_CARBINERIFLE":  "Rifle Carabina",
    "WEAPON_ADVANCEDRIFLE": "Rifle Avanzado",
    "WEAPON_SPECIALCARBINE":"Rifle Especial",
    "WEAPON_MG":            "Ametralladora Media",
    "WEAPON_COMBATMG":      "Ametralladora de Combate",
    "WEAPON_HEAVYSNIPER":   "Francotirador Pesado",
    "WEAPON_SNIPERRIFLE":   "Rifle Francotirador",
    "WEAPON_SAWNOFFSHOTGUN":"Escopeta Recortada",
    "WEAPON_MICROSMG":      "Micro SMG",
    # Accesorios
    "at_compensator":             "Compensador",
    "at_flashlight":              "Linterna Pistola",
    "at_suppressor_light":        "Silenciador Pistola",
    "at_clip_extended_pistol":    "Cargador ext. Pistola",
    "at_clip_extended_shotgun":   "Cargador ext. Escopeta",
    "at_clip_extended_smg":       "Cargador ext. SMG",
    "at_clip_drum_smg":           "Cargador tambor SMG",
    # Armas blancas
    "WEAPON_SWITCHBLADE": "Navaja",
    "WEAPON_KNIFE":        "Cuchillo",
    "WEAPON_KNUCKLE":      "Puño Americano",
    "WEAPON_BAT":          "Bate",
    # Munición
    "ammo-9":       "Munición 9MM",
    "ammo-38":      "Munición .38",
    "ammo-50":      "Munición .50",
    "ammo-rifle":   "Munición Rifle",
    "ammo-45":      "Munición SNS",
    "ammo-shotgun": "Munición Escopeta",
    "ammo-smg":     "Munición SMG",
    "ammo-sniper":  "Munición Francotirador",
    # Objetos
    "radio":          "Radio",
    "chaleco":        "Chaleco Antibalas",
    "oxygenmask":     "Máscara de Oxígeno",
    "repairkit":      "Kit de Reparación",
    "medikit":        "Botiquín",
    "lockpick":       "Ganzúa",
    "money":          "Dinero",
    "black_money":    "Dinero Negro",
    "bandage":        "Venda",
    "defibrillator":  "Desfibrilador",
    "phone":          "Teléfono",
    "police_cad":     "Tablet Policía",
    "boombox":        "Radio Música",
    "bike":           "Bicicleta",
    # Comida / bebida
    "sandwich":   "Sándwich",
    "water":      "Agua",
    "chocolate":  "Chocolate",
    "roscon":     "Roscón",
    "polvoron":   "Polvorón",
    "turron":     "Turrón",
    "coquito":    "Coquito",
    "supervodka": "Vodka",
    # Drogas
    "weed_amnesia":   "Hoja Amnesia",
    "bag_amnesia":    "Amnesia Procesada",
    "peyote":         "Peyote",
    "coca_leaf":      "Hoja de Coca",
    "cocaine":        "Cocaína",
    "poppy_plant":    "Amapola",
    "weed_purplepack":"Hoja Purple",
    # Otros
    "WEAPON_SNOWBALL":        "Nieve",
    "WEAPON_PETROLCAN":       "Gasolina",
    "WEAPON_FIREEXTINGUISHER":"Extintor",
    "lspd_badge":             "Placa LSPD",
}

# ─── CATEGORÍAS ───────────────────────────────────────────────
CATEGORIAS = {
    "pistolas": [
        "WEAPON_PISTOL", "WEAPON_PISTOL_MK2", "WEAPON_SNSPISTOL_MK2",
        "WEAPON_DOUBLEACTION", "WEAPON_COMBATPISTOL", "WEAPON_PISTOL50",
        "WEAPON_SAWNOFFSHOTGUN", "WEAPON_MICROSMG",
        "at_compensator", "at_flashlight", "at_suppressor_light",
        "at_clip_extended_pistol", "at_clip_extended_shotgun",
        "at_clip_extended_smg", "at_clip_drum_smg",
    ],
    "arma_blanca": [
        "WEAPON_SWITCHBLADE", "WEAPON_KNIFE", "WEAPON_KNUCKLE", "WEAPON_BAT",
    ],
    "balas": [
        "ammo-9", "ammo-38", "ammo-50", "ammo-rifle", "ammo-45",
        "ammo-shotgun", "ammo-smg", "ammo-sniper",
    ],
    "drogas": [
        "weed_amnesia", "bag_amnesia", "peyote", "coca_leaf",
        "cocaine", "poppy_plant", "black_money",
    ],
    "comida_bebida": [
        "sandwich", "water", "chocolate", "roscon",
        "polvoron", "turron", "coquito", "supervodka",
    ],
    "kits_equipamiento": [
        "medikit", "bandage", "defibrillator",
        "repairkit", "chaleco", "oxygenmask", "radio",
    ],
    "otros_items": [
        "money", "lockpick", "phone", "police_cad",
        "boombox", "bike", "WEAPON_SNOWBALL", "weed_purplepack", "lspd_badge",
    ],
    "otros": [
        "money", "radio", "chaleco", "oxygenmask", "repairkit", "medikit",
        "lockpick", "bandage", "defibrillator", "phone", "police_cad",
        "boombox", "bike", "sandwich", "water", "chocolate", "roscon",
        "polvoron", "turron", "coquito", "supervodka",
        "WEAPON_SNOWBALL", "WEAPON_PETROLCAN", "lspd_badge",
    ],
}

ACCESORIOS_PISTOLAS = {
    "at_compensator", "at_flashlight", "at_suppressor_light",
    "at_clip_extended_pistol", "at_clip_extended_shotgun",
    "at_clip_extended_smg", "at_clip_drum_smg",
}
