from utils.validator import validate

embed_stash_illegal = """
GUARDAR \u00cdTEM EN STASH
`\U0001f60a` **Jugador**: [2002] ItsPistoolaas
`\U0001f3ae` **Identificador**: steam:1100001477b2190
`\U0001f4ac` **Discord**: <@979527536260313148> (979527536260313148)

`\U0001f522` **ID**: job_stash_399
`\U0001f52b` **Item**: x1 weapon_pistol

`\U0001f4cd` **Coordenadas**: `vec3(5.33, -1104.73, 29.79)`
"""

embed_retrieve_same = """
SACAR \u00cdTEM DE STASH
`\U0001f60a` **Jugador**: [2002] ItsPistoolaas
`\U0001f3ae` **Identificador**: steam:1100001477b2190
`\U0001f4ac` **Discord**: <@979527536260313148> (979527536260313148)

`\U0001f522` **ID**: job_stash_399
`\U0001f52b` **Item**: x1 weapon_pistol

`\U0001f4cd` **Coordenadas**: `vec3(5.33, -1104.73, 29.79)`
"""

embed_medikit_stash = """
GUARDAR \u00cdTEM EN STASH
`\U0001f60a` **Jugador**: [2002] ItsPistoolaas
`\U0001f3ae` **Identificador**: steam:1100001477b2190
`\U0001f4ac` **Discord**: <@979527536260313148> (979527536260313148)

`\U0001f522` **ID**: job_stash_399
`\U0001f52b` **Item**: x1 medikit

`\U0001f4cd` **Coordenadas**: `vec3(5.33, -1104.73, 29.79)`
"""

embed_radio_stash = """
GUARDAR \u00cdTEM EN STASH
`\U0001f60a` **Jugador**: [2370] falke912
`\U0001f3ae` **Identificador**: steam:11000014ebda902
`\U0001f4ac` **Discord**: <@1461945119635931136> (1461945119635931136)

`\U0001f522` **ID**: job_stash_399
`\U0001f52b` **Item**: x1 radio

`\U0001f4cd` **Coordenadas**: `vec3(4.25, -1105.45, 29.79)`
"""

embed_retrieve_radio = """
SACAR \u00cdTEM DE STASH
`\U0001f60a` **Jugador**: [2370] falke912
`\U0001f3ae` **Identificador**: steam:11000014ebda902
`\U0001f4ac` **Discord**: <@1461945119635931136> (1461945119635931136)

`\U0001f522` **ID**: job_stash_399
`\U0001f52b` **Item**: x1 radio

`\U0001f4cd` **Coordenadas**: `vec3(4.25, -1105.45, 29.79)`
"""

if __name__ == "__main__":
    tests = [
        ("STASH pistola ILEGAL -> debe ALERTAR", embed_stash_illegal),
        ("RETRIEVE pistola (misma sesion, <=1min) -> NO alerta", embed_retrieve_same),
        ("STASH medikit (ahora LEGAL) -> OK", embed_medikit_stash),
        ("STASH radio (LEGAL) -> OK", embed_radio_stash),
        ("RETRIEVE radio (LEGAL) -> OK", embed_retrieve_radio),
    ]

    for label, embed in tests:
        print("=" * 60)
        print(label)
        print("=" * 60)
        print(validate(embed))
        print()
