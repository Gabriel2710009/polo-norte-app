from validator import validate

embed1 = """
GUARDAR ÍTEM EN STASH
😊 Jugador: [531] huguito
🎮 Identificador: steam:1100001625d7ceb
💬 Discord: @Huguito Lopez (1121102825271873627)

🔢 ID: job_stash_399
🔫 Item: x1 WEAPON_STUNGUN

📍 Coordenadas: vec3(4.38, -1105.09, 29.79)

LaNaranjaRP × Álvaro © Todos los derechos reservados
"""

embed2 = """
SACAR ÍTEM DE STASH
😊 Jugador: [531] huguito
🎮 Identificador: steam:1100001625d7ceb
💬 Discord: @Huguito Lopez (1121102825271873627)

🔢 ID: job_stash_399
🔫 Item: x1 ballbarry_cupcake

📍 Coordenadas: vec3(4.38, -1105.09, 29.79)

LaNaranjaRP × Álvaro © Todos los derechos reservados
"""

if __name__ == "__main__":
    print("="*50)
    print("EMBED 1: WEAPON_STUNGUN (taser)")
    print("="*50)
    print(validate(embed1))
    print()
    print("="*50)
    print("EMBED 2: ballbarry_cupcake (comida)")
    print("="*50)
    print(validate(embed2))
