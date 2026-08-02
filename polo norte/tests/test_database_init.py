import sys
import os
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from database import entrevistas_db


class TestLimpiarSesionesInconsistentes(unittest.TestCase):

    def _fake_conn(self):
        conn = Mock()
        cur = Mock()
        cur.rowcount = 2
        conn.cursor.return_value = cur
        return conn, cur

    def test_borra_estados_terminales_y_repara_modal_stale(self):
        conn, cur = self._fake_conn()
        with patch.object(entrevistas_db, "_get_conn", return_value=conn), \
             patch.object(entrevistas_db, "_close_conn"):
            total = entrevistas_db.limpiar_sesiones_inconsistentes()

        sqls = [c.args[0] for c in cur.execute.call_args_list]
        terminal = [s for s in sqls if "DELETE FROM sesiones_entrevista" in s
                    and "FINALIZADA" in s]
        reparar = [s for s in sqls if "UPDATE sesiones_entrevista" in s
                   and "modal_open = FALSE" in s
                   and "modal_opened_at <" in s]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(len(reparar), 1)

    def test_repara_no_borra_progreso_activo(self):
        conn, cur = self._fake_conn()
        with patch.object(entrevistas_db, "_get_conn", return_value=conn), \
             patch.object(entrevistas_db, "_close_conn"):
            entrevistas_db.limpiar_sesiones_inconsistentes()

        reparar_sql = [c.args[0] for c in cur.execute.call_args_list
                       if "UPDATE sesiones_entrevista" in c.args[0]][0]
        self.assertIn("modal_open = FALSE", reparar_sql)
        self.assertIn("processing = FALSE", reparar_sql)
        self.assertNotIn("DELETE", reparar_sql)

    def test_maneja_error_db(self):
        conn, cur = self._fake_conn()
        cur.execute.side_effect = Exception("db down")
        with patch.object(entrevistas_db, "_get_conn", return_value=conn), \
             patch.object(entrevistas_db, "_close_conn"):
            with self.assertRaises(Exception):
                entrevistas_db.limpiar_sesiones_inconsistentes()


class TestDatabaseInit(unittest.TestCase):

    def setUp(self):
        database._initialized = False
        database._pool = None

    def tearDown(self):
        database._initialized = False
        database._pool = None

    def test_segunda_llamada_retorna_sin_trabajo(self):
        # Primera llamada: trabajo real
        with patch.object(database, "_try_conn", return_value=Mock()), \
             patch.object(database, "init_toggles") as init_toggles, \
             patch("database.config_db.init") as config_db_init:
            database.init()
            init_toggles.assert_called_once()
            config_db_init.assert_called_once()
            self.assertTrue(database._initialized)

        # Segunda llamada: retorno temprano, cero trabajo
        with patch.object(database, "_try_conn") as try_conn, \
             patch.object(database, "init_toggles") as init_toggles, \
             patch("database.config_db.init") as config_db_init:
            database.init()
            try_conn.assert_not_called()
            init_toggles.assert_not_called()
            config_db_init.assert_not_called()

    def test_init_marca_flag(self):
        with patch.object(database, "_try_conn", return_value=Mock()), \
             patch.object(database, "init_toggles"), \
             patch("database.config_db.init"):
            self.assertFalse(database._initialized)
            database.init()
            self.assertTrue(database._initialized)


class TestDatabaseInitToggles(unittest.TestCase):

    def test_init_toggles_idempotente_con_on_conflict(self):
        # Los INSERT usan ON CONFLICT DO NOTHING: re-ejecutar es seguro
        # y no duplica filas. Verificamos que cada toggle tenga su INSERT.
        conn = Mock()
        cur = Mock()
        conn.cursor.return_value = cur

        with patch.object(database, "_try_conn", return_value=conn), \
             patch.object(database, "close_conn"):
            database.init_toggles()

        inserts = [call for call in cur.execute.call_args_list
                   if "INSERT INTO toggle_estados" in call.args[0]]
        self.assertEqual(len(inserts), 3)
        nombres = set()
        for call in inserts:
            sql = call.args[0]
            for nombre in ("items", "fichaje", "taser_dm"):
                if f"VALUES ('{nombre}'" in sql:
                    nombres.add(nombre)
        self.assertEqual(nombres, {"items", "fichaje", "taser_dm"})


if __name__ == "__main__":
    unittest.main()
