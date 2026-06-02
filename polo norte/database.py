import os
import time
import logging
import psycopg2
from psycopg2 import OperationalError

DATABASE_URL = os.getenv("DATABASE_URL")
logger = logging.getLogger("Database")
_RETRIES = 3
_RETRY_DELAYS = [2, 4, 8]


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def _try(func, *args, **kwargs):
    last_exc = None
    for attempt in range(1, _RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except OperationalError as e:
            last_exc = e
            if attempt < _RETRIES:
                delay = _RETRY_DELAYS[attempt - 1]
                logger.warning(
                    "Intento %s/%s falló, reintentando en %ss: %s",
                    attempt, _RETRIES, delay, e
                )
                time.sleep(delay)
            else:
                logger.error("Error de conexión DB tras %s intentos: %s", _RETRIES, e)
        except Exception as e:
            logger.error("Error en DB: %s", e)
            raise
    raise last_exc


def init():
    logger.info("Inicializando base de datos...")
    conn = _try(get_conn)
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

    # add columns introduced in later versions (safe re-run)
    try:
        cur.execute(
            "ALTER TABLE fichaje_registros ADD COLUMN ultimo_dm_at TIMESTAMP DEFAULT NULL"
        )
        conn.commit()
    except Exception:
        conn.rollback()

    cur.close()
    conn.close()
    logger.info("Tabla fichaje_registros lista.")


def insert_clock_in(user_id: str, username: str) -> int:
    conn = _try(get_conn)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO fichaje_registros (user_id, username) VALUES (%s, %s) RETURNING id",
        (user_id, username)
    )
    row_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    logger.debug("Clock-in insertado: user=%s id=%s", user_id, row_id)
    return row_id


def get_active_clock_in(user_id: str):
    conn = _try(get_conn)
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM fichaje_registros WHERE user_id = %s AND clock_out_at IS NULL ORDER BY id DESC LIMIT 1",
        (user_id,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def close_clock_in(user_id: str) -> int | None:
    conn = _try(get_conn)
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM fichaje_registros WHERE user_id = %s AND clock_out_at IS NULL ORDER BY id DESC LIMIT 1",
        (user_id,)
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        logger.debug("No se encontró clock-in activo para %s", user_id)
        return None
    record_id = row[0]
    cur.execute("UPDATE fichaje_registros SET clock_out_at = NOW() WHERE id = %s", (record_id,))
    conn.commit()
    cur.close()
    conn.close()
    logger.debug("Clock-out cerrado: user=%s record=%s", user_id, record_id)
    return record_id


def set_taser_retirado(record_id: int):
    conn = _try(get_conn)
    cur = conn.cursor()
    cur.execute("UPDATE fichaje_registros SET taser_retirado = TRUE WHERE id = %s", (record_id,))
    conn.commit()
    cur.close()
    conn.close()
    logger.debug("Taser retirado marcado: record=%s", record_id)


def mark_taser_retirado_activo(user_id: str) -> bool:
    """Returns True if there was an active clock-in, False otherwise."""
    conn = _try(get_conn)
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM fichaje_registros WHERE user_id = %s AND clock_out_at IS NULL LIMIT 1",
        (user_id,)
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        logger.debug("Sin clock-in activo para marcar taser retirado: user=%s", user_id)
        return False
    cur.execute(
        "UPDATE fichaje_registros SET taser_retirado = TRUE WHERE id = %s AND taser_retirado = FALSE",
        (row[0],)
    )
    conn.commit()
    cur.close()
    conn.close()
    logger.debug("Taser retirado marcado (activo): user=%s", user_id)
    return True


def set_taser_devuelto(user_id: str):
    conn = _try(get_conn)
    cur = conn.cursor()
    cur.execute(
        """UPDATE fichaje_registros SET taser_devuelto = TRUE
           WHERE user_id = %s AND clock_out_at IS NOT NULL
             AND taser_retirado = TRUE AND taser_devuelto = FALSE
           ORDER BY id DESC LIMIT 1""",
        (user_id,)
    )
    conn.commit()
    cur.close()
    conn.close()
    logger.debug("Taser devuelto marcado: user=%s", user_id)


def get_pending_alerts():
    conn = _try(get_conn)
    cur = conn.cursor()
    cur.execute(
        """SELECT * FROM fichaje_registros
           WHERE taser_retirado = TRUE AND taser_devuelto = FALSE
             AND clock_out_at IS NOT NULL AND alerta_enviada = FALSE"""
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    logger.debug("Pending alerts: %s", len(rows))
    return rows


def mark_alerta_enviada(record_id: int):
    conn = _try(get_conn)
    cur = conn.cursor()
    cur.execute("UPDATE fichaje_registros SET alerta_enviada = TRUE WHERE id = %s", (record_id,))
    conn.commit()
    cur.close()
    conn.close()
    logger.debug("Alerta marcada como enviada: record=%s", record_id)


def set_ultimo_dm(record_id: int):
    conn = _try(get_conn)
    cur = conn.cursor()
    cur.execute("UPDATE fichaje_registros SET ultimo_dm_at = NOW() WHERE id = %s", (record_id,))
    conn.commit()
    cur.close()
    conn.close()
    logger.debug("ultimo_dm_at actualizado: record=%s", record_id)


def get_records_para_recordatorio():
    conn = _try(get_conn)
    cur = conn.cursor()
    cur.execute(
        """SELECT id, user_id FROM fichaje_registros
           WHERE taser_retirado = TRUE AND taser_devuelto = FALSE
             AND ultimo_dm_at IS NOT NULL
             AND ultimo_dm_at < NOW() - INTERVAL '24 hours'"""
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    logger.debug("Records para recordatorio: %s", len(rows))
    return rows
