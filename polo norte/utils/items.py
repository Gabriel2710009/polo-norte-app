ALLOWED_ITEMS = {
    "sandwich", "water", "chocolate", "roscon", "polvoron", "turron",
    "coquito", "supervodka", "ballbarry_cupcake", "cupcake",
    "bread", "cheese", "apple", "banana", "orange", "pizza",
    "hamburger", "hotdog", "donut", "candy", "energy_drink",
    "soda", "coffee", "tea", "beer", "wine", "whisky", "vodka",
    "tacos", "burrito", "nachos", "popcorn", "icecream",

    "radio", "walkie", "walkietalkie", "walkie_talkie",

    "weapon_stungun", "stungun", "taser", "weapon_taser",

    "medikit",

    "money",
}

ITEM_ALIASES = {
    "weapon_pistol":      "unauthorized_weapon",
    "weapon_pistol_mk2":  "unauthorized_weapon",
    "weapon_snspistol_mk2":"unauthorized_weapon",
    "weapon_combatpistol": "unauthorized_weapon",
    "weapon_pistol50":    "unauthorized_weapon",
    "weapon_doubleaction": "unauthorized_weapon",
    "weapon_assaultrifle": "unauthorized_weapon",
    "weapon_carbinerifle": "unauthorized_weapon",
    "weapon_advancedrifle":"unauthorized_weapon",
    "weapon_specialcarbine":"unauthorized_weapon",
    "weapon_mg":          "unauthorized_weapon",
    "weapon_combatmg":    "unauthorized_weapon",
    "weapon_heavysniper": "unauthorized_weapon",
    "weapon_sniperrifle": "unauthorized_weapon",
    "weapon_sawnoffshotgun":"unauthorized_weapon",
    "weapon_microsmg":    "unauthorized_weapon",
    "weapon_switchblade": "unauthorized_melee",
    "weapon_knife":       "unauthorized_melee",
    "weapon_knuckle":     "unauthorized_melee",
    "weapon_bat":         "unauthorized_melee",
    "weapon_hammer":      "unauthorized_melee",
    "weapon_crowbar":     "unauthorized_melee",
    "weapon_golfclub":    "unauthorized_melee",
    "weapon_poolcue":     "unauthorized_melee",
    "weapon_machete":     "unauthorized_melee",
    "weapon_hatchet":     "unauthorized_melee",
    "weapon_bottle":      "unauthorized_melee",
    "ammo-9":             "unauthorized_ammo",
    "ammo-38":            "unauthorized_ammo",
    "ammo-50":            "unauthorized_ammo",
    "ammo-rifle":         "unauthorized_ammo",
    "ammo-45":            "unauthorized_ammo",
    "ammo-shotgun":       "unauthorized_ammo",
    "ammo-smg":           "unauthorized_ammo",
    "ammo-sniper":        "unauthorized_ammo",
    "lockpick":           "unauthorized_tool",
    "repairkit":          "unauthorized_tool",
    "chaleco":            "unauthorized_armor",
    "oxygenmask":         "unauthorized_gear",

    "bandage":            "unauthorized_medical",
    "defibrillator":      "unauthorized_medical",
    "phone":              "unauthorized_electronics",
    "boombox":            "unauthorized_electronics",
    "bike":               "unauthorized_vehicle",

    "black_money":        "unauthorized",
    "police_cad":         "unauthorized",
    "lspd_badge":         "unauthorized",
    "coca_leaf":          "unauthorized_drug",
    "cocaine":            "unauthorized_drug",
    "weed_amnesia":       "unauthorized_drug",
    "bag_amnesia":        "unauthorized_drug",
    "poppy_plant":        "unauthorized_drug",
    "peyote":             "unauthorized_drug",
    "weed_purplepack":    "unauthorized_drug",
}

def is_allowed(item_name: str) -> bool:
    name = item_name.strip().lower().replace(" ", "_").replace("-", "_")
    return name in ALLOWED_ITEMS

def normalize(item_name: str) -> str:
    return item_name.strip().lower().replace(" ", "_").replace("-", "_")
