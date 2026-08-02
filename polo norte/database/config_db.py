"""Tablas de configuraciones globales del bot.

Migraci�n de archivos JSON a PostgreSQL:
  - config_aprobar.json  -> config_aprobar
  - config_bienvenida.json -> config_bienvenida
  - config.json (owner_id) -> config_global
  - tickets_notificados.json -> tickets_notificados
"""

import json
import logging
import database

logger = logging.getLogger("ConfigDB")

_TABLE_APROBAR = "config_aprobar"
_TABLE_BIENVENIDA = "config_bienvenida"
_TABLE_GLOBAL = "config_global"
_TABLE_NOTIFICADOS = "tickets_notificados"


def _get_conn():
    return database.get_conn()


def _close_conn(conn):
    database.close_conn(conn)


_inicializado = False


def _asegurar_inicializacion():
    """Crea las tablas si a\u00fan no se llam\u00f3 a init().

    Elimina la dependencia de orden: cualquier lectura/escritura de estas
    tablas garantiza que existan, incluso con una base completamente vac\u00eda.
    """
    global _inicializado
    if _inicializado:
        return
    init()
    _inicializado = True


def _row_to_dict(cur) -> dict | None:
    row = cur.fetchone()
    if row:
        cols = [desc[0] for desc in cur.description]
        return dict(zip(cols, row))
    return None


def init():
    global _inicializado
    if _inicializado:
        logger.debug("config_db ya inicializado, omitiendo")
        return
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_APROBAR} (
                id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                roles_asignar JSONB NOT NULL DEFAULT '[]'::jsonb,
                roles_eliminar JSONB NOT NULL DEFAULT '[]'::jsonb,
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                updated_by TEXT
            )
        """)
        cur.execute(f"""
            INSERT INTO {_TABLE_APROBAR} (id) VALUES (1)
            ON CONFLICT (id) DO NOTHING
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_BIENVENIDA} (
                id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                mensaje TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                updated_by TEXT
            )
        """)
        cur.execute(f"""
            INSERT INTO {_TABLE_BIENVENIDA} (id) VALUES (1)
            ON CONFLICT (id) DO NOTHING
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_GLOBAL} (
                clave TEXT PRIMARY KEY,
                valor TEXT DEFAULT ''
            )
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_NOTIFICADOS} (
                channel_id TEXT PRIMARY KEY
            )
        """)

        conn.commit()
        logger.info("Tablas de configuraci�n global listas (PostgreSQL)")
    except Exception as e:
        conn.rollback()
        logger.error("Error creando tablas de configuraci�n: %s", e)
        raise
    finally:
        cur.close()
        _close_conn(conn)

    _migrar_desde_json_si_vacio()
    _migrar_notificados_json_si_vacio()
    _inicializado = True


# --- config_aprobar ---

def _migrar_desde_json_si_vacio():
    """Migra config_aprobar.json y config_bienvenida.json a PostgreSQL
    una sola vez si las tablas est\u00e1n vac\u00edas."""
    import os
    import json as _json

    data_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
    )
    path_aprobar = os.path.join(data_dir, "config_aprobar.json")
    path_bienvenida = os.path.join(data_dir, "config_bienvenida.json")

    conn = _get_conn()
    cur = conn.cursor()
    try:
        # config_aprobar
        cur.execute(f"SELECT roles_asignar FROM {_TABLE_APROBAR} WHERE id = 1")
        row = cur.fetchone()
        if row and (row[0] or row[0] == "[]"):
            pass
        elif os.path.exists(path_aprobar):
            try:
                with open(path_aprobar, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                cur.execute(
                    f"UPDATE {_TABLE_APROBAR} SET roles_asignar = %s::jsonb, roles_eliminar = %s::jsonb, updated_at = NOW() WHERE id = %s",
                    (
                        _json.dumps(data.get("roles_asignar", [])),
                        _json.dumps(data.get("roles_eliminar", [])),
                        1,
                    ),
                )
                conn.commit()
                logger.info("Migrada config_aprobar.json a PostgreSQL")
            except Exception as e:
                logger.warning("Error migrando config_aprobar.json: %s", e)
                conn.rollback()

        # config_bienvenida
        cur.execute(f"SELECT mensaje FROM {_TABLE_BIENVENIDA} WHERE id = 1")
        row = cur.fetchone()
        if not row or not row[0]:
            if os.path.exists(path_bienvenida):
                try:
                    with open(path_bienvenida, "r", encoding="utf-8") as f:
                        data = _json.load(f)
                    cur.execute(
                        f"UPDATE {_TABLE_BIENVENIDA} SET mensaje = %s, updated_at = NOW() WHERE id = %s",
                        (data.get("mensaje", ""), 1),
                    )
                    conn.commit()
                    logger.info("Migrada config_bienvenida.json a PostgreSQL")
                except Exception as e:
                    logger.warning("Error migrando config_bienvenida.json: %s", e)
                    conn.rollback()
    finally:
        cur.close()
        _close_conn(conn)


def cargar_aprobar(datos_en_memoria: dict | None = None) -> dict:
    """Carga la configuraci�n de aprobaci�n desde PostgreSQL.

    Si ya hay datos en memoria (cache), los devuelve directamente.
    Si la DB est� vac�a, migra desde el JSON de respaldo y persiste."""
    _asegurar_inicializacion()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT roles_asignar, roles_eliminar FROM {_TABLE_APROBAR} WHERE id = 1")
        row = cur.fetchone()
        if row:
            assign = row[0] if isinstance(row[0], list) else json.loads(row[0])
            remove = row[1] if isinstance(row[1], list) else json.loads(row[1])
            return {"roles_asignar": assign, "roles_eliminar": remove}

        if datos_en_memoria is not None:
            guardar_aprobar(datos_en_memoria, via="migracion_json")
            return datos_en_memoria
        return {"roles_asignar": [], "roles_eliminar": []}
    finally:
        cur.close()
        _close_conn(conn)


def guardar_aprobar(config: dict, actualizado_por: str = "", via: str = ""):
    _asegurar_inicializacion()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"UPDATE {_TABLE_APROBAR} SET "
            "roles_asignar = %s::jsonb, roles_eliminar = %s::jsonb, "
            "updated_at = NOW(), updated_by = %s WHERE id = %s",
            (
                json.dumps(config.get("roles_asignar", [])),
                json.dumps(config.get("roles_eliminar", [])),
                actualizado_por or via,
                1,
            ),
        )
        conn.commit()
        logger.info("Config aprobar guardada (via=%s): asignar=%s eliminar=%s",
                     via or actualizado_por,
                     len(config.get("roles_asignar", [])),
                     len(config.get("roles_eliminar", [])))
        return True
    except Exception as e:
        conn.rollback()
        logger.error("Error guardando config aprobar: %s", e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


# --- config_bienvenida ---

def cargar_bienvenida(datos: dict | None = None) -> dict:
    _asegurar_inicializacion()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT mensaje FROM {_TABLE_BIENVENIDA} WHERE id = 1")
        row = cur.fetchone()
        if row and row[0]:
            return {"mensaje": row[0]}
        if datos is not None:
            guardar_bienvenida(datos, via="migracion_init")
            return datos
        return {"mensaje": ""}
    finally:
        cur.close()
        _close_conn(conn)


def guardar_bienvenida(config: dict, actualizado_por: str = "", via: str = ""):
    _asegurar_inicializacion()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"UPDATE {_TABLE_BIENVENIDA} SET mensaje = %s, updated_at = NOW(), updated_by = %s WHERE id = %s",
            (config.get("mensaje", ""), actualizado_por or via, 1),
        )
        conn.commit()
        logger.info("Bienvenida guardada (via=%s)", via or actualizado_por)
        return True
    except Exception as e:
        conn.rollback()
        logger.error("Error guardando config bienvenida: %s", e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


# --- config_global (owner_id) ---

def cargar_global_clave(clave: str) -> str | None:
    _asegurar_inicializacion()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT valor FROM {_TABLE_GLOBAL} WHERE clave = %s", (clave,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()
        _close_conn(conn)


def guardar_global_clave(clave: str, valor: str):
    _asegurar_inicializacion()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"INSERT INTO {_TABLE_GLOBAL} (clave, valor) VALUES (%s, %s) "
            "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor",
            (clave, valor),
        )
        conn.commit()
        logger.info("Config global guardada: %s = %s", clave, valor)
    except Exception as e:
        conn.rollback()
        logger.error("Error guardando config global %s: %s", clave, e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


# --- tickets_notificados ---

def _migrar_notificados_json_si_vacio():
    """Migra tickets_notificados.json -> DB una sola vez si la tabla est\u00e1 vac\u00eda."""
    import os
    import json as _json

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) FROM {_TABLE_NOTIFICADOS}")
        if cur.fetchone()[0] > 0:
            return

        data_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
        )
        path = os.path.join(data_dir, "tickets_notificados.json")
        if not os.path.exists(path):
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = _json.load(f)
        except Exception:
            return

        ids = [int(x) for x in data] if isinstance(data, list) else []
        for cid in ids:
            cur.execute(
                f"INSERT INTO {_TABLE_NOTIFICADOS} (channel_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (cid,),
            )
        conn.commit()
        logger.info("Migrados %s notificados desde JSON a PostgreSQL", len(ids))
    finally:
        cur.close()
        _close_conn(conn)


def cargar_notificados() -> set[int]:
    _asegurar_inicializacion()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT channel_id FROM {_TABLE_NOTIFICADOS}")
        ids = {int(row[0]) for row in cur.fetchall()}
        return ids
    finally:
        cur.close()
        _close_conn(conn)


def guardar_notificados(data: set[int]):
    _asegurar_inicializacion()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"DELETE FROM {_TABLE_NOTIFICADOS}")
        for cid in data:
            cur.execute(f"INSERT INTO {_TABLE_NOTIFICADOS} (channel_id) VALUES (%s)", (cid,))
        conn.commit()
        logger.info("Notificados persistidos: %s tickets", len(data))
    except Exception as e:
        conn.rollback()
        logger.error("Error guardando notificados: %s", e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def agregar_notificado(channel_id: int):
    _asegurar_inicializacion()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"INSERT INTO {_TABLE_NOTIFICADOS} (channel_id) VALUES (%s) ON CONFLICT DO NOTHING",
            (channel_id,),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error("Error agregando notificado %s: %s", channel_id, e)
    finally:
        cur.close()
        _close_conn(conn)


def eliminar_notificado(channel_id: int):
    _asegurar_inicializacion()
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"DELETE FROM {_TABLE_NOTIFICADOS} WHERE channel_id = %s", (channel_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error("Error eliminando notificado %s: %s", channel_id, e)
    finally:
        cur.close()
        _close_conn(conn)