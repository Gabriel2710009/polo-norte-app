import sys
import os
import json
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import config_db
from utils import config_manager
from utils.config_manager import DEFAULT_DESPEDIR_CONFIG


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


class TestConfigDbDespedir(unittest.TestCase):
    def setUp(self):
        config_db._inicializado = True

    def tearDown(self):
        config_db._inicializado = False

    def test_cargar_desde_db_dict(self):
        guardada = {"rol_despedido": 1, "plantilla_aviso": "hola"}
        p, conn = _patch_conn(rows=[(guardada,)])
        with p:
            result = config_db.cargar_despedir()
        self.assertEqual(result, guardada)
        self.assertIn("config_despedir", conn.cursor_obj.executed[0][0])

    def test_cargar_desde_db_json_string(self):
        p, _ = _patch_conn(rows=[(json.dumps({"dias": 5}),)])
        with p:
            result = config_db.cargar_despedir()
        self.assertEqual(result, {"dias": 5})

    def test_db_vacia_persiste_defaults(self):
        p, conn = _patch_conn(rows=[])
        with p:
            result = config_db.cargar_despedir(datos={"dias": 3})
        self.assertEqual(result, {"dias": 3})
        self.assertTrue(
            any("UPDATE config_despedir" in sql for sql, _ in conn.cursor_obj.executed)
        )

    def test_db_vacia_sin_defaults_devuelve_vacio(self):
        p, _ = _patch_conn(rows=[])
        with p:
            result = config_db.cargar_despedir()
        self.assertEqual(result, {})

    def test_guardar_escribe_jsonb_y_actualizado_por(self):
        p, conn = _patch_conn()
        with p:
            ok = config_db.guardar_despedir({"dias": 4}, actualizado_por="123")
        self.assertTrue(ok)
        sql, params = conn.cursor_obj.executed[0]
        self.assertIn("UPDATE config_despedir", sql)
        self.assertEqual(json.loads(params[0]), {"dias": 4})
        self.assertEqual(params[1], "123")


class TestConfigDbInitDespedir(unittest.TestCase):
    def test_init_crea_tabla_despedir(self):
        config_db._inicializado = False
        try:
            with patch.object(config_db, "_migrar_desde_json_si_vacio"), \
                 patch.object(config_db, "_migrar_notificados_json_si_vacio"):
                p, conn = _patch_conn(rows=[])
                with p:
                    config_db.init()
            sqls = [sql for sql, _ in conn.cursor_obj.executed]
            self.assertTrue(
                any("CREATE TABLE IF NOT EXISTS config_despedir" in s for s in sqls)
            )
            self.assertTrue(any("INSERT INTO config_despedir" in s for s in sqls))
        finally:
            config_db._inicializado = False


class TestLoadDespedirConfig(unittest.TestCase):
    def test_defaults_cuando_db_vacia(self):
        with patch.object(config_db, "cargar_despedir", return_value={}):
            result = config_manager.load_despedir_config()
        self.assertEqual(result, DEFAULT_DESPEDIR_CONFIG)

    def test_merge_de_claves_guardadas(self):
        guardada = {"dias": 7, "plantilla_aviso": "otra"}
        with patch.object(config_db, "cargar_despedir", return_value=guardada):
            result = config_manager.load_despedir_config()
        self.assertEqual(result["dias"], 7)
        self.assertEqual(result["plantilla_aviso"], "otra")
        self.assertEqual(
            result["rol_verificado"], DEFAULT_DESPEDIR_CONFIG["rol_verificado"]
        )

    def test_pasa_defaults_a_la_db_para_sembrar(self):
        with patch.object(config_db, "cargar_despedir", return_value={}) as cargar:
            config_manager.load_despedir_config()
        cargar.assert_called_once_with(datos=DEFAULT_DESPEDIR_CONFIG)

    def test_save_delega(self):
        with patch.object(config_db, "guardar_despedir", return_value=True) as guardar:
            ok = config_manager.save_despedir_config({"dias": 1}, actualizado_por="42")
        self.assertTrue(ok)
        guardar.assert_called_once_with({"dias": 1}, actualizado_por="42")

    def test_defaults_tienen_ids_y_placeholders(self):
        config = DEFAULT_DESPEDIR_CONFIG
        self.assertEqual(config["rol_verificado"], 1306131425304514611)
        self.assertEqual(config["rol_despedido"], 1307889268227837962)
        self.assertEqual(config["categoria_destino"], 1345688584249933926)
        self.assertEqual(config["canal_despedido"], 1343860496993878057)
        self.assertEqual(len(config["categorias_busqueda"]), 3)
        for clave in ("usuario", "motivo", "dias"):
            self.assertIn("{" + clave + "}", config["plantilla_despedir"])
        for clave in ("usuario", "motivo"):
            self.assertIn("{" + clave + "}", config["plantilla_aviso"])


if __name__ == "__main__":
    unittest.main()
