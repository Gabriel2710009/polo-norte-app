import os
import sys
import unittest
from unittest.mock import Mock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs import blacklist_cog


def _mensaje_mock(content: str, embeds=None):
    msg = Mock()
    msg.content = content
    msg.embeds = embeds or []
    return msg


def _embed_mock(titulo=None, descripcion=None, fields=None):
    embed = Mock()
    embed.title = titulo
    embed.description = descripcion
    embed.fields = fields or []
    return embed


def _field_mock(name: str, value: str):
    f = Mock()
    f.name = name
    f.value = value
    return f


_SKIP_DB = True
_db_module = None

try:
    _db_url = os.environ.get("DATABASE_URL", "")
    if _db_url and _db_url.startswith("postgres"):
        from database import blacklist_db as _db_module
        _db_module.init()
        _SKIP_DB = False
except Exception as e:
    print(f"\n\u26a0\ufe0f PostgreSQL no disponible, omitiendo tests DB: {e}")
    _SKIP_DB = True


class TestDB(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db = _db_module
        cls.skip = _SKIP_DB

    def setUp(self):
        if self.skip:
            self.skipTest("PostgreSQL no disponible")
        conn = self.db._get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM blacklist_postulaciones")
        cur.execute("DELETE FROM intentos_postulacion")
        conn.commit()
        cur.close()
        self.db._close_conn(conn)

    def test_agregar_y_obtener(self):
        self.assertTrue(self.db.agregar("111", "Fabian Rodriguez", "Prueba", "999"))
        res = self.db.obtener("111")
        self.assertIsNotNone(res)
        self.assertEqual(res["discord_id"], "111")
        self.assertEqual(res["nombre_ic"], "Fabian Rodriguez")
        self.assertEqual(res["motivo"], "Prueba")
        self.assertEqual(res["staff_id"], "999")

    def test_no_existe_retorna_none(self):
        self.assertIsNone(self.db.obtener("999999"))

    def test_eliminar_existente(self):
        self.db.agregar("222", "Test", "Motivo", "999")
        self.assertTrue(self.db.eliminar("222"))
        self.assertIsNone(self.db.obtener("222"))

    def test_eliminar_inexistente(self):
        self.assertFalse(self.db.eliminar("no_existe"))

    def test_existe(self):
        self.db.agregar("444", "Test", "Razon", "999")
        self.assertTrue(self.db.existe("444"))
        self.assertFalse(self.db.existe("no_existe"))

    def test_nombre_ic_default(self):
        self.db.agregar("666", None, "Sin nombre", "999")
        res = self.db.obtener("666")
        self.assertEqual(res["nombre_ic"], "Desconocido")

    def test_rechaza_duplicado(self):
        self.assertTrue(self.db.agregar("111", "Original", "V\u00e1lido", "staff1"))
        self.assertFalse(self.db.agregar("111", "Copia", "Duplicado", "staff2"))
        res = self.db.obtener("111")
        self.assertEqual(res["motivo"], "V\u00e1lido")

    def test_listar_paginacion(self):
        for i in range(25):
            self.db.agregar(f"{i:04d}", f"Nombre{i}", f"Motivo{i}", "staff")
        p1, total = self.db.listar(1, 10)
        self.assertEqual(len(p1), 10)
        self.assertEqual(total, 25)
        p3, _ = self.db.listar(3, 10)
        self.assertEqual(len(p3), 5)

    def test_buscar_por_id(self):
        self.db.agregar("12345", "Juan Perez", "Razon", "999")
        res = self.db.buscar("12345")
        self.assertEqual(len(res), 1)

    def test_buscar_por_nombre_parcial(self):
        self.db.agregar("111", "Fabian Rodriguez", "X", "999")
        self.db.agregar("222", "Fabian Martinez", "Y", "999")
        res = self.db.buscar("Fabian")
        self.assertEqual(len(res), 2)

    def test_buscar_por_criterios_ambos(self):
        self.db.agregar("100", "Fabian Rodriguez", "X", "999")
        self.db.agregar("200", "Fabian Martinez", "Y", "999")
        res = self.db.buscar_por_criterios(discord_id="100", nombre_ic="Fabian")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["discord_id"], "100")

    def test_registrar_intento(self):
        self.db.registrar_intento("111", "ticket_abc", "Motivo")
        conn = self.db._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM intentos_postulacion")
        self.assertEqual(cur.fetchone()[0], 1)
        cur.close()
        self.db._close_conn(conn)

    def test_obtener_todos(self):
        self.db.agregar("1", "A", "X", "s")
        self.db.agregar("2", "B", "Y", "s")
        todos = self.db.obtener_todos()
        self.assertEqual(set(todos), {"1", "2"})


class TestExtraerNombreIC(unittest.TestCase):
    def test_misma_linea(self):
        msgs = [_mensaje_mock("Nombre IC: Fabian Rodriguez")]
        self.assertEqual(blacklist_cog._extraer_nombre_ic(msgs), "Fabian Rodriguez")

    def test_linea_siguiente(self):
        msgs = [_mensaje_mock("Nombre IC:\nFabian Rodriguez")]
        self.assertEqual(blacklist_cog._extraer_nombre_ic(msgs), "Fabian Rodriguez")

    def test_con_flecha(self):
        msgs = [_mensaje_mock("\u2192 Nombre IC: Fabian Rodriguez")]
        self.assertEqual(blacklist_cog._extraer_nombre_ic(msgs), "Fabian Rodriguez")

    def test_flecha_pegada(self):
        msgs = [_mensaje_mock("\u2192Nombre IC: Fabian Rodriguez")]
        self.assertEqual(blacklist_cog._extraer_nombre_ic(msgs), "Fabian Rodriguez")

    def test_minusculas(self):
        msgs = [_mensaje_mock("nombre ic: Fabian Rodriguez")]
        self.assertEqual(blacklist_cog._extraer_nombre_ic(msgs), "Fabian Rodriguez")

    def test_mayusculas(self):
        msgs = [_mensaje_mock("NOMBRE IC: Fabian Rodriguez")]
        self.assertEqual(blacklist_cog._extraer_nombre_ic(msgs), "Fabian Rodriguez")

    def test_sin_colon(self):
        msgs = [_mensaje_mock("Nombre IC\nFabian Rodriguez")]
        self.assertEqual(blacklist_cog._extraer_nombre_ic(msgs), "Fabian Rodriguez")

    def test_espacio_antes_colon(self):
        msgs = [_mensaje_mock("Nombre IC : Fabian Rodriguez")]
        self.assertEqual(blacklist_cog._extraer_nombre_ic(msgs), "Fabian Rodriguez")

    def test_flecha_y_salto(self):
        msgs = [_mensaje_mock("\u2192 Nombre IC:\nFabian Rodriguez")]
        self.assertEqual(blacklist_cog._extraer_nombre_ic(msgs), "Fabian Rodriguez")

    def test_contenido_previo(self):
        msgs = [_mensaje_mock("Hola.\nNombre IC: Fabian Rodriguez\nEdad: 25")]
        self.assertEqual(blacklist_cog._extraer_nombre_ic(msgs), "Fabian Rodriguez")

    def test_doble_salto(self):
        msgs = [_mensaje_mock("Nombre IC:\n\nFabian Rodriguez")]
        self.assertEqual(blacklist_cog._extraer_nombre_ic(msgs), "Fabian Rodriguez")

    def test_sin_nombre_ic(self):
        msgs = [_mensaje_mock("Hola quiero postularme")]
        self.assertIsNone(blacklist_cog._extraer_nombre_ic(msgs))

    def test_desde_embed_field(self):
        field = _field_mock("Nombre IC", "Fabian Rodriguez")
        embed = _embed_mock(fields=[field])
        msgs = [_mensaje_mock("", embeds=[embed])]
        self.assertEqual(blacklist_cog._extraer_nombre_ic(msgs), "Fabian Rodriguez")

    def test_desde_embed_field_name(self):
        field = _field_mock("Nombre IC", "Carlos Perez")
        embed = _embed_mock(fields=[field])
        msgs = [_mensaje_mock("", embeds=[embed])]
        self.assertEqual(blacklist_cog._extraer_nombre_ic(msgs), "Carlos Perez")


class TestExtraerDatosIC(unittest.TestCase):
    def test_extraer_todos_los_campos(self):
        content = (
            "Nombre IC: Fatido Rodriguez\n"
            "Numero IC: 4809639162\n"
            "IBAN IC: NA20 1821 8817 7121 6519\n"
            "Steam URL: https://steamcommunity.com/profiles/76561199877636058/"
        )
        msgs = [_mensaje_mock(content)]
        datos = blacklist_cog._extraer_datos_ic(msgs)
        self.assertEqual(datos["nombre_ic"], "Fatido Rodriguez")
        self.assertEqual(datos["numero_ic"], "4809639162")
        self.assertEqual(datos["iban_ic"], "NA20 1821 8817 7121 6519")
        self.assertEqual(datos["steam_url"], "https://steamcommunity.com/profiles/76561199877636058/")

    def test_extraer_solo_nombre(self):
        msgs = [_mensaje_mock("Nombre IC: Juan Perez\nOtro: cosa")]
        datos = blacklist_cog._extraer_datos_ic(msgs)
        self.assertEqual(datos["nombre_ic"], "Juan Perez")
        self.assertIsNone(datos["numero_ic"])
        self.assertIsNone(datos["iban_ic"])
        self.assertIsNone(datos["steam_url"])

    def test_numero_con_variantes(self):
        msgs = [_mensaje_mock("N\u00famero IC: 12345")]
        datos = blacklist_cog._extraer_datos_ic(msgs)
        self.assertEqual(datos["numero_ic"], "12345")

    def test_iban_con_variantes(self):
        msgs = [_mensaje_mock("iban: NA20 1821")]
        datos = blacklist_cog._extraer_datos_ic(msgs)
        self.assertEqual(datos["iban_ic"], "NA20 1821")

    def test_steam_url_simple(self):
        msgs = [_mensaje_mock("https://steamcommunity.com/profiles/123")]
        datos = blacklist_cog._extraer_datos_ic(msgs)
        self.assertIn("steam_url", datos)
        self.assertIsNotNone(datos["steam_url"])

    def test_error_ortografico_nombre(self):
        msgs = [_mensaje_mock("nombre ic: Carlos Lopez")]
        datos = blacklist_cog._extraer_datos_ic(msgs)
        self.assertEqual(datos["nombre_ic"], "Carlos Lopez")

    def test_campos_con_flecha(self):
        content = "\u2192 Nombre IC: Ana Garcia\n\u2192 Numero: 555\n\u2192 Steam: https://steamcommunity.com/id/ana"
        msgs = [_mensaje_mock(content)]
        datos = blacklist_cog._extraer_datos_ic(msgs)
        self.assertEqual(datos["nombre_ic"], "Ana Garcia")
        self.assertEqual(datos["numero_ic"], "555")

    def test_campos_desde_embed(self):
        fields = [
            _field_mock("Nombre IC", "Pedro Martinez"),
            _field_mock("N\u00famero IC", "111222"),
        ]
        embed = _embed_mock(fields=fields)
        msgs = [_mensaje_mock("", embeds=[embed])]
        datos = blacklist_cog._extraer_datos_ic(msgs)
        self.assertEqual(datos["nombre_ic"], "Pedro Martinez")
        self.assertEqual(datos["numero_ic"], "111222")


class TestResolverRegex(unittest.TestCase):
    def test_mention_normal(self):
        m = blacklist_cog._MENTION_PATTERN.match("<@123456789012345678>")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "123456789012345678")

    def test_mention_nickname(self):
        m = blacklist_cog._MENTION_PATTERN.match("<@!123456789012345678>")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "123456789012345678")

    def test_mention_no_match_sin_corchetes(self):
        self.assertIsNone(blacklist_cog._MENTION_PATTERN.match("123"))

    def test_id_valido(self):
        m = blacklist_cog._ID_PATTERN.match("123456789012345678")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "123456789012345678")

    def test_id_muy_corto(self):
        self.assertIsNone(blacklist_cog._ID_PATTERN.match("12345"))

    def test_id_con_letras(self):
        self.assertIsNone(blacklist_cog._ID_PATTERN.match("1234567890abcdefgh"))

    def test_string_vacio(self):
        self.assertIsNone(blacklist_cog._ID_PATTERN.match(""))
        self.assertIsNone(blacklist_cog._MENTION_PATTERN.match(""))


class TestConsistencias(unittest.TestCase):
    def test_solo_rol_sin_db(self):
        self.assertTrue(blacklist_cog._detectar_inconsistencia(en_db=False, tiene_rol=True))

    def test_solo_db_sin_rol(self):
        self.assertTrue(blacklist_cog._detectar_inconsistencia(en_db=True, tiene_rol=False))

    def test_ambos_presentes(self):
        self.assertFalse(blacklist_cog._detectar_inconsistencia(en_db=True, tiene_rol=True))

    def test_ambos_ausentes(self):
        self.assertFalse(blacklist_cog._detectar_inconsistencia(en_db=False, tiene_rol=False))


class TestConMocks(unittest.TestCase):
    @patch("cogs.blacklist_cog.db")
    def test_obtener_retorna_dict(self, mock_db):
        mock_db.obtener.return_value = {
            "discord_id": "111",
            "nombre_ic": "Test",
            "motivo": "Razon",
            "staff_id": "999",
            "fecha": "2025-01-01T00:00:00+00:00",
        }
        res = mock_db.obtener("111")
        self.assertEqual(res["nombre_ic"], "Test")
        self.assertEqual(res["motivo"], "Razon")

    @patch("cogs.blacklist_cog.db")
    def test_agregar_retorna_bool(self, mock_db):
        mock_db.agregar.return_value = True
        self.assertTrue(mock_db.agregar("111", "Test", "X", "999"))
        mock_db.agregar.return_value = False
        self.assertFalse(mock_db.agregar("111", "Test", "X", "999"))

    def test_tickets_notificados(self):
        blacklist_cog._tickets_notificados.clear()
        self.assertEqual(len(blacklist_cog._tickets_notificados), 0)
        blacklist_cog._tickets_notificados.add(999)
        self.assertIn(999, blacklist_cog._tickets_notificados)


if __name__ == "__main__":
    unittest.main(verbosity=2, failfast=False)
