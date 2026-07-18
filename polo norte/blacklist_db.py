import os
import logging
import psycopg2
import psycopg2.pool
from psycopg2 import OperationalError, IntegrityError
from datetime import datetime, timezone

import database

logger = logging.getLogger("BlacklistDB")

_TABLE_BL = "blacklist_postulaciones"
_TABLE_IN = "intentos_postulacion"


def _get_conn():
    return database.get_conn()


def _close_conn(conn):
    database.close_conn(conn)


def init():
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_BL} (
                discord_id TEXT PRIMARY KEY,
                nombre_ic TEXT NOT NULL DEFAULT 'Desconocido',
                numero_ic TEXT,
                iban_ic TEXT,
                steam_url TEXT,
                motivo TEXT NOT NULL,
                staff_id TEXT NOT NULL,
                fecha TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ticket_origen_id TEXT,
                expira_en TIMESTAMPTZ
            )
        """)
        for col in ("numero_ic", "iban_ic", "steam_url"):
            try:
                cur.execute(f"ALTER TABLE {_TABLE_BL} ADD COLUMN IF NOT EXISTS {col} TEXT")
            except Exception:
                conn.rollback()
                continue
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_IN} (
                id SERIAL PRIMARY KEY,
                discord_id TEXT NOT NULL,
                ticket_id TEXT NOT NULL,
                fecha TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                motivo TEXT
            )
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{_TABLE_BL}_discord_id ON {_TABLE_BL} (discord_id)
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{_TABLE_BL}_nombre_ic ON {_TABLE_BL} (nombre_ic)
        """)
        conn.commit()
        logger.info("Tablas blacklist e intentos listas (PostgreSQL)")
    except Exception as e:
        conn.rollback()
        logger.error("Error creando tablas blacklist: %s", e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def agregar(discord_id: str, nombre_ic: str, motivo: str, staff_id: str,
            ticket_origen_id: str = None, expira_en: str = None,
            numero_ic: str = None, iban_ic: str = None, steam_url: str = None):
    if not nombre_ic:
        nombre_ic = "Desconocido"
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"INSERT INTO {_TABLE_BL} (discord_id, nombre_ic, numero_ic, iban_ic, steam_url, motivo, staff_id, ticket_origen_id, expira_en) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (discord_id, nombre_ic, numero_ic, iban_ic, steam_url, motivo, staff_id, ticket_origen_id, expira_en),
        )
        conn.commit()
        logger.info("Blacklist creada: discord_id=%s", discord_id)
        return True
    except IntegrityError:
        conn.rollback()
        logger.warning("Intento de blacklist duplicado: discord_id=%s", discord_id)
        return False
    except Exception as e:
        conn.rollback()
        logger.error("Error agregando blacklist: %s", e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def eliminar(discord_id: str) -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"DELETE FROM {_TABLE_BL} WHERE discord_id = %s", (discord_id,))
        conn.commit()
        eliminado = cur.rowcount > 0
        if eliminado:
            logger.info("Blacklist eliminada: discord_id=%s", discord_id)
        return eliminado
    except Exception as e:
        conn.rollback()
        logger.error("Error eliminando blacklist: %s", e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def obtener(discord_id: str):
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT * FROM {_TABLE_BL} WHERE discord_id = %s", (discord_id,))
        row = cur.fetchone()
        if row:
            cols = [desc[0] for desc in cur.description]
            return dict(zip(cols, row))
        return None
    finally:
        cur.close()
        _close_conn(conn)


def existe(discord_id: str) -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT 1 FROM {_TABLE_BL} WHERE discord_id = %s", (discord_id,))
        return cur.fetchone() is not None
    finally:
        cur.close()
        _close_conn(conn)


def listar(pagina: int = 1, por_pagina: int = 10):
    offset = (pagina - 1) * por_pagina
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT * FROM {_TABLE_BL} ORDER BY fecha DESC LIMIT %s OFFSET %s",
            (por_pagina, offset),
        )
        cols = [desc[0] for desc in cur.description]
        registros = [dict(zip(cols, row)) for row in cur.fetchall()]

        cur.execute(f"SELECT COUNT(*) AS total FROM {_TABLE_BL}")
        total = cur.fetchone()[0]
        return registros, total
    finally:
        cur.close()
        _close_conn(conn)


def buscar(termino: str):
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT * FROM {_TABLE_BL} WHERE discord_id = %s OR nombre_ic ILIKE %s ORDER BY fecha DESC",
            (termino, f"%{termino}%"),
        )
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        cur.close()
        _close_conn(conn)


def buscar_por_criterios(discord_id: str = None, nombre_ic: str = None):
    if not discord_id and not nombre_ic:
        return []

    condiciones = []
    params = []

    if discord_id:
        condiciones.append("discord_id = %s")
        params.append(discord_id)

    if nombre_ic:
        condiciones.append("nombre_ic ILIKE %s")
        params.append(f"%{nombre_ic}%")

    sql = f"SELECT * FROM {_TABLE_BL} WHERE {' AND '.join(condiciones)} ORDER BY fecha DESC"

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        cur.close()
        _close_conn(conn)


def obtener_todos():
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT discord_id FROM {_TABLE_BL}")
        return [row[0] for row in cur.fetchall()]
    finally:
        cur.close()
        _close_conn(conn)


def registrar_intento(discord_id: str, ticket_id: str, motivo: str = None):
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"INSERT INTO {_TABLE_IN} (discord_id, ticket_id, motivo) VALUES (%s, %s, %s)",
            (discord_id, ticket_id, motivo),
        )
        conn.commit()
        logger.info("Intento de postulación registrado: discord_id=%s ticket=%s", discord_id, ticket_id)
    except Exception as e:
        conn.rollback()
        logger.error("Error registrando intento de postulación: %s", e)
    finally:
        cur.close()
        _close_conn(conn)
