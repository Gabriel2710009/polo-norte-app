"""
Pruebas del módulo de blacklist de postulaciones.

Las pruebas que no requieren base de datos se ejecutan directamente.
Las pruebas de base de datos requieren PostgreSQL accesible via DATABASE_URL.

Ejecutar:
    python test_blacklist.py
"""

import os
import sys
import unittest
from unittest.mock import Mock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import blacklist_cog

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────


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


# ─────────────────────────────────────────────
# Pruebas DB
# ─────────────────────────────────────────────

_SKIP_DB = True
_db_module = None

try:
    _db_url = os.environ.get("DATABASE_URL", "")
    if _db_url and _db_url.startswith("postgres"):
        import blacklist_db as _db_module
        _db_module.init()
        _SKIP_DB = False
except Exception as e:
    print(f"\n\u26a0\ufe0f PostgreSQL no disponible, omitiendo tests DB: {e}")
    _SKIP_DB = True


class TestDB(unittest.TestCase):
    """Requiere DATABASE_URL configurada. Omitir si no está disponible."""

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
        self.assertTrue(self.db.agregar("111", "Original", "Válido", "staff1"))
        self.assertFalse(self.db.agregar("111", "Copia", "Duplicado", "staff2"))
        res = self.db.obtener("111")
        self.assertEqual(res["motivo"], "Válido")

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


# ─────────────────────────────────────────────
# Pruebas de extracción de Nombre IC
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# Pruebas de inconsistencias
# ─────────────────────────────────────────────

class TestConsistencias(unittest.TestCase):
    def test_solo_rol_sin_db(self):
        self.assertTrue(blacklist_cog._detectar_inconsistencia(en_db=False, tiene_rol=True))

    def test_solo_db_sin_rol(self):
        self.assertTrue(blacklist_cog._detectar_inconsistencia(en_db=True, tiene_rol=False))

    def test_ambos_presentes(self):
        self.assertFalse(blacklist_cog._detectar_inconsistencia(en_db=True, tiene_rol=True))

    def test_ambos_ausentes(self):
        self.assertFalse(blacklist_cog._detectar_inconsistencia(en_db=False, tiene_rol=False))


# ─────────────────────────────────────────────
# Pruebas con mocks del modulo DB
# ─────────────────────────────────────────────

class TestConMocks(unittest.TestCase):
    @patch("blacklist_cog.db")
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

    @patch("blacklist_cog.db")
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


# ─────────────────────────────────────────────
# Ejecución
# ─────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2, failfast=False)
