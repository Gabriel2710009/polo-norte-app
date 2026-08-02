import sys
import os
import json
from unittest.mock import patch, Mock
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import config_db


class FakeCursor:
    def __init__(self):
        self.executed = []
        self._rows = []
        self.description = None

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConn:
    def __init__(self):
        self.cursor_obj = FakeCursor()

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        pass

    def rollback(self):
        pass

    def closed(self):
        return False


def _patch_conn(rows=None, cursor=None):
    conn = FakeConn()
    if rows is not None:
        conn.cursor_obj._rows = rows
    if cursor is not None:
        conn.cursor_obj = cursor
    return patch.object(config_db, "_get_conn", return_value=conn), conn


class TestConfigDbBase(unittest.TestCase):
    """Fuerza el flag de inicializaci\u00f3n para aislar cada funci\u00f3n
    de la auto-creaci\u00f3n de tablas (que se prueba por separado)."""

    def setUp(self):
        config_db._inicializado = True

    def tearDown(self):
        config_db._inicializado = False


class TestConfigDbCargarAprobar(TestConfigDbBase):

    def test_lee_desde_db(self):
        p, conn = _patch_conn(rows=[([1, 2, 3], [4, 5])])
        with p:
            result = config_db.cargar_aprobar()
        self.assertEqual(result, {"roles_asignar": [1, 2, 3], "roles_eliminar": [4, 5]})
        self.assertIn("config_aprobar", conn.cursor_obj.executed[0][0])

    def test_db_vacia_migra_default(self):
        p, conn = _patch_conn(rows=[])
        with p:
            result = config_db.cargar_aprobar(datos_en_memoria={"roles_asignar": [10], "roles_eliminar": [20]})
        self.assertEqual(result, {"roles_asignar": [10], "roles_eliminar": [20]})
        self.assertTrue(any("UPDATE config_aprobar" in sql for sql, _ in conn.cursor_obj.executed))

    def test_db_vacia_sin_default_devuelve_vacio(self):
        p, _ = _patch_conn(rows=[])
        with p:
            result = config_db.cargar_aprobar()
        self.assertEqual(result, {"roles_asignar": [], "roles_eliminar": []})

    def test_maneja_jsonb_string(self):
        p, _ = _patch_conn(rows=[('[1,2]', '[3]')])
        with p:
            result = config_db.cargar_aprobar()
        self.assertEqual(result, {"roles_asignar": [1, 2], "roles_eliminar": [3]})


class TestConfigDbGuardarAprobar(TestConfigDbBase):

    def test_guarda_con_json(self):
        p, conn = _patch_conn()
        with p:
            ok = config_db.guardar_aprobar({"roles_asignar": [1], "roles_eliminar": [2]})
        self.assertTrue(ok)
        sql, params = conn.cursor_obj.executed[0]
        self.assertIn("UPDATE config_aprobar", sql)
        self.assertEqual(params[0], json.dumps([1]))
        self.assertEqual(params[1], json.dumps([2]))


class TestConfigDbNotificados(TestConfigDbBase):

    def test_cargar_devuelve_int(self):
        p, _ = _patch_conn(rows=[("5001",), ("5002",)])
        with p:
            result = config_db.cargar_notificados()
        self.assertEqual(result, {5001, 5002})

    def test_guardar_borra_y_reinserta(self):
        p, conn = _patch_conn()
        with p:
            config_db.guardar_notificados({1, 2, 3})
        sqls = [sql for sql, _ in conn.cursor_obj.executed]
        self.assertTrue(any("DELETE FROM tickets_notificados" in s for s in sqls))
        inserts = [params for sql, params in conn.cursor_obj.executed if "INSERT INTO tickets_notificados" in sql]
        self.assertEqual(len(inserts), 3)

    def test_agregar_notificado(self):
        p, conn = _patch_conn()
        with p:
            config_db.agregar_notificado(5001)
        sql, params = conn.cursor_obj.executed[0]
        self.assertIn("INSERT INTO tickets_notificados", sql)
        self.assertEqual(params, (5001,))

    def test_eliminar_notificado(self):
        p, conn = _patch_conn()
        with p:
            config_db.eliminar_notificado(5001)
        sql, params = conn.cursor_obj.executed[0]
        self.assertIn("DELETE FROM tickets_notificados", sql)
        self.assertEqual(params, (5001,))


class TestConfigDbGlobal(TestConfigDbBase):

    def test_cargar_global_clave(self):
        p, _ = _patch_conn(rows=[("12345",)])
        with p:
            result = config_db.cargar_global_clave("owner_id")
        self.assertEqual(result, "12345")

    def test_cargar_global_clave_vacia(self):
        p, _ = _patch_conn(rows=[])
        with p:
            result = config_db.cargar_global_clave("owner_id")
        self.assertIsNone(result)

    def test_guardar_global_clave_upsert(self):
        p, conn = _patch_conn()
        with p:
            config_db.guardar_global_clave("owner_id", "42")
        sql, params = conn.cursor_obj.executed[0]
        self.assertIn("config_global", sql)
        self.assertIn("ON CONFLICT", sql)


class TestConfigDbBienvenida(TestConfigDbBase):

    def test_cargar_bienvenida_desde_db(self):
        p, _ = _patch_conn(rows=[("Hola",)])
        with p:
            result = config_db.cargar_bienvenida()
        self.assertEqual(result, {"mensaje": "Hola"})

    def test_cargar_bienvenida_vacia_persiste_default(self):
        p, conn = _patch_conn(rows=[])
        with p:
            result = config_db.cargar_bienvenida(datos={"mensaje": "Default"})
        self.assertEqual(result, {"mensaje": "Default"})
        self.assertTrue(any("UPDATE config_bienvenida" in sql for sql, _ in conn.cursor_obj.executed))


class TestConfigDbAutoInit(unittest.TestCase):
    """Verifica que config_db crea las tablas autom\u00e1ticamente
    ante la primera lectura/escritura (base vac\u00eda)."""

    def test_asegurar_inicializacion_ejecuta_init_una_vez(self):
        config_db._inicializado = False
        init_calls = []
        orig = config_db.init

        def fake_init():
            init_calls.append(1)
            orig()

        try:
            with patch.object(config_db, "init", side_effect=fake_init), \
                 patch.object(config_db, "_migrar_desde_json_si_vacio"), \
                 patch.object(config_db, "_migrar_notificados_json_si_vacio"):
                p, conn = _patch_conn(rows=[])
                with p:
                    config_db._asegurar_inicializacion()
                    config_db._asegurar_inicializacion()
            self.assertEqual(len(init_calls), 1)
        finally:
            config_db._inicializado = False

    def test_primera_lectura_crea_tablas(self):
        config_db._inicializado = False
        try:
            with patch.object(config_db, "_migrar_desde_json_si_vacio"), \
                 patch.object(config_db, "_migrar_notificados_json_si_vacio"):
                p, conn = _patch_conn(rows=[])
                with p:
                    result = config_db.cargar_global_clave("owner_id")
                sqls = [sql for sql, _ in conn.cursor_obj.executed]
                self.assertTrue(any("CREATE TABLE IF NOT EXISTS config_global" in s for s in sqls))
                self.assertTrue(any("CREATE TABLE IF NOT EXISTS config_aprobar" in s for s in sqls))
                self.assertTrue(any("CREATE TABLE IF NOT EXISTS config_bienvenida" in s for s in sqls))
                self.assertTrue(any("CREATE TABLE IF NOT EXISTS tickets_notificados" in s for s in sqls))
        finally:
            config_db._inicializado = False


if __name__ == "__main__":
    unittest.main()
