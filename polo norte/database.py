import os
import time
import logging
import psycopg2
import psycopg2.pool
from psycopg2 import OperationalError

DATABASE_URL = os.getenv("DATABASE_URL")
logger = logging.getLogger("Database")
_RETRIES = 6
_RETRY_DELAYS = [2, 4, 8, 16, 30, 60]
_CONN_KWARGS = {
    "connect_timeout": 30,
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 5,
}

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.SimpleConnectionPool(1, 5, DATABASE_URL, **_CONN_KWARGS)
    return _pool


def _try_conn():
    last_exc = None
    for attempt in range(1, _RETRIES + 1):
        conn = None
        try:
            conn = _get_pool().getconn()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            return conn
        except OperationalError as e:
            if conn:
                conn.close()
            last_exc = e
            if attempt < _RETRIES:
                delay = _RETRY_DELAYS[attempt - 1]
                logger.warning(
                    "Intento %s/%s falló, reintentando en %ss: %s",
                    attempt, _RETRIES, delay, e,
                )
                time.sleep(delay)
            else:
                logger.error("Error de conexión DB tras %s intentos: %s", _RETRIES, e)
        except Exception as e:
            if conn:
                conn.close()
            logger.error("Error en DB: %s", e)
            raise
    raise last_exc


def get_conn():
    return _try_conn()


def close_conn(conn):
    if conn and not conn.closed:
        _get_pool().putconn(conn)


def init():
    logger.info("Inicializando base de datos...")
    conn = _try_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS fichaje_registros (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            username TEXT DEFAULT '',
            clock_in_at TIMESTAMP NOT NULL DEFAULT NOW(),
            clock_out_at TIMESTAMP,
            taser_retirado BOOLEAN DEFAULT FALSE,
            taser_devuelto BOOLEAN DEFAULT FALSE,
            alerta_enviada BOOLEAN DEFAULT FALSE
        )
    """)
    conn.commit()

    try:
        cur.execute(
            "ALTER TABLE fichaje_registros ADD COLUMN ultimo_dm_at TIMESTAMP DEFAULT NULL"
        )
        conn.commit()
    except Exception:
        conn.rollback()

    cur.close()
    close_conn(conn)
    logger.info("Tabla fichaje_registros lista.")

    init_toggles()


def init_toggles():
    conn = _try_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS toggle_estados (
            nombre TEXT PRIMARY KEY,
            activo BOOLEAN NOT NULL DEFAULT FALSE
        )
    """)
    cur.execute(
        "INSERT INTO toggle_estados (nombre, activo) VALUES ('items', FALSE) ON CONFLICT (nombre) DO NOTHING"
    )
    cur.execute(
        "INSERT INTO toggle_estados (nombre, activo) VALUES ('fichaje', FALSE) ON CONFLICT (nombre) DO NOTHING"
    )
    cur.execute(
        "INSERT INTO toggle_estados (nombre, activo) VALUES ('taser_dm', TRUE) ON CONFLICT (nombre) DO NOTHING"
    )
    conn.commit()
    cur.close()
    close_conn(conn)
    logger.info("Tabla toggle_estados lista.")


def get_toggle(nombre: str) -> bool:
    conn = _try_conn()
    cur = conn.cursor()
    cur.execute("SELECT activo FROM toggle_estados WHERE nombre = %s", (nombre,))
    row = cur.fetchone()
    cur.close()
    close_conn(conn)
    return row[0] if row else False


def set_toggle(nombre: str, activo: bool):
    conn = _try_conn()
    cur = conn.cursor()
    cur.execute("UPDATE toggle_estados SET activo = %s WHERE nombre = %s", (activo, nombre))
    conn.commit()
    cur.close()
    close_conn(conn)
    logger.debug("Toggle %s = %s", nombre, activo)


def insert_clock_in(user_id: str, username: str) -> int:
    conn = _try_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO fichaje_registros (user_id, username) VALUES (%s, %s) RETURNING id",
        (user_id, username),
    )
    row_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    close_conn(conn)
    logger.debug("Clock-in insertado: user=%s id=%s", user_id, row_id)
    return row_id


def get_active_clock_in(user_id: str):
    conn = _try_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM fichaje_registros WHERE user_id = %s AND clock_out_at IS NULL ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    cur.close()
    close_conn(conn)
    return row


def close_clock_in(user_id: str) -> int | None:
    conn = _try_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM fichaje_registros WHERE user_id = %s AND clock_out_at IS NULL ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        close_conn(conn)
        logger.debug("No se encontró clock-in activo para %s", user_id)
        return None
    record_id = row[0]
    cur.execute("UPDATE fichaje_registros SET clock_out_at = NOW() WHERE id = %s", (record_id,))
    conn.commit()
    cur.close()
    close_conn(conn)
    logger.debug("Clock-out cerrado: user=%s record=%s", user_id, record_id)
    return record_id


def set_taser_retirado(record_id: int):
    conn = _try_conn()
    cur = conn.cursor()
    cur.execute("UPDATE fichaje_registros SET taser_retirado = TRUE WHERE id = %s", (record_id,))
    conn.commit()
    cur.close()
    close_conn(conn)
    logger.debug("Taser retirado marcado: record=%s", record_id)


def mark_taser_retirado_activo(user_id: str) -> bool:
    conn = _try_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM fichaje_registros WHERE user_id = %s AND clock_out_at IS NULL LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        close_conn(conn)
        logger.debug("Sin clock-in activo para marcar taser retirado: user=%s", user_id)
        return False
    cur.execute(
        "UPDATE fichaje_registros SET taser_retirado = TRUE WHERE id = %s AND taser_retirado = FALSE",
        (row[0],),
    )
    conn.commit()
    cur.close()
    close_conn(conn)
    logger.debug("Taser retirado marcado (activo): user=%s", user_id)
    return True


def set_taser_devuelto(user_id: str):
    conn = _try_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, alerta_enviada FROM fichaje_registros WHERE user_id = %s AND clock_out_at IS NOT NULL"
        " AND taser_retirado = TRUE AND taser_devuelto = FALSE ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    if row:
        record_id, tuvo_alerta = row[0], row[1]
        cur.execute(
            "UPDATE fichaje_registros SET taser_devuelto = TRUE, ultimo_dm_at = NOW() WHERE id = %s",
            (record_id,),
        )
        conn.commit()
        cur.close()
        close_conn(conn)
        logger.debug("Taser devuelto marcado: user=%s record=%s", user_id, record_id)
        return {"record_id": record_id, "tuvo_alerta": tuvo_alerta}
    cur.close()
    close_conn(conn)
    logger.debug("Taser devuelto: no se encontró record activo para user=%s", user_id)
    return None


def get_pending_alerts():
    conn = _try_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT * FROM fichaje_registros
           WHERE taser_retirado = TRUE AND taser_devuelto = FALSE
             AND clock_out_at IS NOT NULL AND alerta_enviada = FALSE"""
    )
    rows = cur.fetchall()
    cur.close()
    close_conn(conn)
    logger.debug("Pending alerts: %s", len(rows))
    return rows


def mark_alerta_enviada(record_id: int):
    conn = _try_conn()
    cur = conn.cursor()
    cur.execute("UPDATE fichaje_registros SET alerta_enviada = TRUE WHERE id = %s", (record_id,))
    conn.commit()
    cur.close()
    close_conn(conn)
    logger.debug("Alerta marcada como enviada: record=%s", record_id)


def set_ultimo_dm(record_id: int):
    conn = _try_conn()
    cur = conn.cursor()
    cur.execute("UPDATE fichaje_registros SET ultimo_dm_at = NOW() WHERE id = %s", (record_id,))
    conn.commit()
    cur.close()
    close_conn(conn)
    logger.debug("ultimo_dm_at actualizado: record=%s", record_id)


def get_username(user_id: str) -> str | None:
    conn = _try_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT username FROM fichaje_registros WHERE user_id = %s AND username != '' ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    cur.close()
    close_conn(conn)
    return row[0] if row else None


def get_records_para_recordatorio():
    conn = _try_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT id, user_id FROM fichaje_registros
           WHERE taser_retirado = TRUE AND taser_devuelto = FALSE
             AND ultimo_dm_at IS NOT NULL
             AND ultimo_dm_at < NOW() - INTERVAL '24 hours'"""
    )
    rows = cur.fetchall()
    cur.close()
    close_conn(conn)
    logger.debug("Records para recordatorio: %s", len(rows))
    return rows
