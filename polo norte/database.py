import os
import psycopg2
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init():
    conn = get_conn()
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
    cur.close()
    conn.close()

def insert_clock_in(user_id: str, username: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO fichaje_registros (user_id, username) VALUES (%s, %s) RETURNING id",
        (user_id, username)
    )
    row_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return row_id

def get_active_clock_in(user_id: str):
    conn = get_conn()
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
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM fichaje_registros WHERE user_id = %s AND clock_out_at IS NULL ORDER BY id DESC LIMIT 1",
        (user_id,)
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return None
    record_id = row[0]
    cur.execute(
        "UPDATE fichaje_registros SET clock_out_at = NOW() WHERE id = %s",
        (record_id,)
    )
    conn.commit()
    cur.close()
    conn.close()
    return record_id

def set_taser_retirado(record_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE fichaje_registros SET taser_retirado = TRUE WHERE id = %s", (record_id,))
    conn.commit()
    cur.close()
    conn.close()

def set_taser_devuelto(user_id: str):
    conn = get_conn()
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

def get_pending_alerts():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT * FROM fichaje_registros
           WHERE taser_retirado = TRUE AND taser_devuelto = FALSE
             AND clock_out_at IS NOT NULL AND alerta_enviada = FALSE"""
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def mark_alerta_enviada(record_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE fichaje_registros SET alerta_enviada = TRUE WHERE id = %s", (record_id,))
    conn.commit()
    cur.close()
    conn.close()
