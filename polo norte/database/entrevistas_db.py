import json
import logging
import random
from datetime import datetime, timezone, timedelta
from typing import Any

import database

logger = logging.getLogger("EntrevistasDB")

_TABLE_PREGUNTAS = "preguntas_entrevista"
_TABLE_ENTREVISTAS = "entrevistas"
_TABLE_INTENTOS = "intentos_entrevista"
_TABLE_CONFIG = "configuracion_postulacion"
_TABLE_SESIONES = "sesiones_entrevista"

CATEGORIAS_VALIDAS = {"GENERAL", "ARMERIA", "CASOS_PRACTICOS"}

MAX_INTENTOS = 3


def _get_conn():
    return database.get_conn()


def _close_conn(conn):
    database.close_conn(conn)


def _row_to_dict(cur) -> dict | None:
    row = cur.fetchone()
    if row:
        cols = [desc[0] for desc in cur.description]
        return dict(zip(cols, row))
    return None


def _rows_to_list(cur) -> list[dict]:
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def init():
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_PREGUNTAS} (
                id SERIAL PRIMARY KEY,
                pregunta TEXT NOT NULL,
                categoria TEXT NOT NULL CHECK (categoria IN ('GENERAL', 'ARMERIA', 'CASOS_PRACTICOS')),
                activo BOOLEAN DEFAULT TRUE,
                creado_por TEXT NOT NULL,
                fecha_creacion TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{_TABLE_PREGUNTAS}_categoria
            ON {_TABLE_PREGUNTAS} (categoria)
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{_TABLE_PREGUNTAS}_activo
            ON {_TABLE_PREGUNTAS} (activo)
        """)
        cur.execute(f"""
            ALTER TABLE {_TABLE_PREGUNTAS} ADD COLUMN IF NOT EXISTS respuesta_esperada TEXT DEFAULT ''
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_ENTREVISTAS} (
                id SERIAL PRIMARY KEY,
                entrevistado_id TEXT NOT NULL,
                entrevistador_id TEXT NOT NULL,
                canal_id TEXT,
                fecha TIMESTAMPTZ DEFAULT NOW(),
                resultado TEXT NOT NULL CHECK (resultado IN ('APROBADO', 'NO_APROBADO')),
                intento INTEGER NOT NULL DEFAULT 1,
                total_errores INTEGER DEFAULT 0,
                aprobado_por_entrevista BOOLEAN DEFAULT TRUE,
                preguntas_used JSONB NOT NULL,
                respuestas JSONB NOT NULL,
                motivos JSONB DEFAULT '{{}}'::jsonb
            )
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{_TABLE_ENTREVISTAS}_entrevistado
            ON {_TABLE_ENTREVISTAS} (entrevistado_id)
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_INTENTOS} (
                usuario_id TEXT PRIMARY KEY,
                cantidad_intentos INTEGER DEFAULT 0,
                ultimo_intento TIMESTAMPTZ
            )
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_CONFIG} (
                id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                log_channel_id TEXT DEFAULT '0',
                errores_channel_id TEXT DEFAULT '0',
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                updated_by TEXT
            )
        """)
        cur.execute(f"""
            INSERT INTO {_TABLE_CONFIG} (id) VALUES (1)
            ON CONFLICT (id) DO NOTHING
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_SESIONES} (
                user_id TEXT PRIMARY KEY,
                staff_id TEXT NOT NULL,
                channel_id TEXT,
                guild_id TEXT,
                session_id TEXT DEFAULT '',
                questions JSONB NOT NULL,
                current_index INTEGER NOT NULL DEFAULT 0,
                answers JSONB NOT NULL DEFAULT '[]'::jsonb,
                motives JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                intento INTEGER NOT NULL DEFAULT 1,
                started_at TIMESTAMPTZ DEFAULT NOW(),
                estado TEXT NOT NULL DEFAULT 'ACTIVA'
                    CHECK (estado IN ('ACTIVA', 'EXPIRADA', 'FINALIZADA', 'ABANDONADA')),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        conn.commit()
        logger.info("Tablas de entrevistas listas (PostgreSQL)")
    except Exception as e:
        conn.rollback()
        logger.error("Error creando tablas de entrevistas: %s", e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def agregar_pregunta(pregunta: str, categoria: str, creado_por: str, respuesta_esperada: str = "") -> int:
    if categoria not in CATEGORIAS_VALIDAS:
        raise ValueError(f"Categoria inv\u00e1lida: {categoria}. V\u00e1lidas: {', '.join(sorted(CATEGORIAS_VALIDAS))}")

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"INSERT INTO {_TABLE_PREGUNTAS} (pregunta, categoria, creado_por, respuesta_esperada) VALUES (%s, %s, %s, %s) RETURNING id",
            (pregunta, categoria, creado_por, respuesta_esperada),
        )
        pregunta_id = cur.fetchone()[0]
        conn.commit()
        logger.info("Pregunta agregada: id=%s categoria=%s", pregunta_id, categoria)
        return pregunta_id
    except Exception as e:
        conn.rollback()
        logger.warning("Error agregando pregunta: %s", e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def editar_pregunta(pregunta_id: int, nueva_pregunta: str, nueva_categoria: str = "", respuesta_esperada: str = "") -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        campos = ["pregunta = %s"]
        params: list[Any] = [nueva_pregunta]
        if nueva_categoria:
            if nueva_categoria not in CATEGORIAS_VALIDAS:
                raise ValueError(f"Categoria inv\u00e1lida: {nueva_categoria}. V\u00e1lidas: {', '.join(sorted(CATEGORIAS_VALIDAS))}")
            campos.append("categoria = %s")
            params.append(nueva_categoria)
        campos.append("respuesta_esperada = %s")
        params.append(respuesta_esperada)
        params.append(pregunta_id)
        cur.execute(
            f"UPDATE {_TABLE_PREGUNTAS} SET {', '.join(campos)} WHERE id = %s",
            params,
        )
        conn.commit()
        actualizado = cur.rowcount > 0
        if actualizado:
            logger.info("Pregunta editada: id=%s", pregunta_id)
        else:
            logger.warning("Pregunta no encontrada para editar: id=%s", pregunta_id)
        return actualizado
    except Exception as e:
        conn.rollback()
        logger.warning("Error editando pregunta %s: %s", pregunta_id, e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def eliminar_pregunta(pregunta_id: int) -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"DELETE FROM {_TABLE_PREGUNTAS} WHERE id = %s",
            (pregunta_id,),
        )
        conn.commit()
        eliminado = cur.rowcount > 0
        if eliminado:
            logger.info("Pregunta eliminada: id=%s", pregunta_id)
        else:
            logger.warning("Pregunta no encontrada para eliminar: id=%s", pregunta_id)
        return eliminado
    except Exception as e:
        conn.rollback()
        logger.warning("Error eliminando pregunta %s: %s", pregunta_id, e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def obtener_pregunta(pregunta_id: int) -> dict | None:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT * FROM {_TABLE_PREGUNTAS} WHERE id = %s",
            (pregunta_id,),
        )
        return _row_to_dict(cur)
    except Exception as e:
        logger.warning("Error obteniendo pregunta id=%s: %s", pregunta_id, e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def listar_preguntas(categoria: str | None = None, solo_activas: bool = True) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        condiciones = []
        params: list[Any] = []

        if categoria:
            condiciones.append("categoria = %s")
            params.append(categoria)

        if solo_activas:
            condiciones.append("activo = TRUE")

        where = ""
        if condiciones:
            where = "WHERE " + " AND ".join(condiciones)

        cur.execute(
            f"SELECT * FROM {_TABLE_PREGUNTAS} {where} ORDER BY id DESC",
            params,
        )
        return _rows_to_list(cur)
    except Exception as e:
        logger.warning("Error listando preguntas: %s", e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def contar_preguntas_por_categoria() -> dict[str, int]:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT categoria, COUNT(*) AS total FROM {_TABLE_PREGUNTAS} "
            "WHERE activo = TRUE GROUP BY categoria",
        )
        resultados = {categoria: 0 for categoria in CATEGORIAS_VALIDAS}
        for row in cur.fetchall():
            resultados[row[0]] = row[1]
        return resultados
    except Exception as e:
        logger.warning("Error contando preguntas por categoria: %s", e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def seleccionar_preguntas_aleatorias(categoria: str, cantidad: int = 5) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT id, pregunta, categoria, respuesta_esperada FROM {_TABLE_PREGUNTAS} "
            "WHERE categoria = %s AND activo = TRUE ORDER BY RANDOM() LIMIT %s",
            (categoria, cantidad),
        )
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        logger.warning("Error seleccionando preguntas aleatorias para %s: %s", categoria, e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def guardar_entrevista(
    entrevistado_id: str,
    entrevistador_id: str,
    canal_id: str | None,
    resultado: str,
    intento: int,
    total_errores: int,
    preguntas_used: list[dict],
    respuestas: list[str],
    motivos: dict[int, str] | None = None,
) -> int:
    if resultado not in ("APROBADO", "NO_APROBADO"):
        raise ValueError(f"Resultado inv\u00e1lido: {resultado}")

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"INSERT INTO {_TABLE_ENTREVISTAS} "
            "(entrevistado_id, entrevistador_id, canal_id, resultado, intento, "
            "total_errores, aprobado_por_entrevista, preguntas_used, respuestas, motivos) "
            "VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s::jsonb, %s::jsonb, %s::jsonb) "
            "RETURNING id",
            (
                entrevistado_id,
                entrevistador_id,
                canal_id,
                resultado,
                intento,
                total_errores,
                json.dumps(preguntas_used, ensure_ascii=False, default=str),
                json.dumps(respuestas, ensure_ascii=False),
                json.dumps(motivos or {}, ensure_ascii=False, default=str),
            ),
        )
        entrevista_id = cur.fetchone()[0]
        conn.commit()
        logger.info(
            "Entrevista guardada: id=%s user=%s resultado=%s intento=%s",
            entrevista_id, entrevistado_id, resultado, intento,
        )
        return entrevista_id
    except Exception as e:
        conn.rollback()
        logger.warning("Error guardando entrevista: %s", e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def obtener_entrevistas(usuario_id: str, limite: int = 10) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT * FROM {_TABLE_ENTREVISTAS} "
            "WHERE entrevistado_id = %s ORDER BY fecha DESC LIMIT %s",
            (usuario_id, limite),
        )
        return _rows_to_list(cur)
    except Exception as e:
        logger.warning("Error obteniendo entrevistas para %s: %s", usuario_id, e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def obtener_intentos(usuario_id: str) -> int:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT cantidad_intentos FROM {_TABLE_INTENTOS} WHERE usuario_id = %s",
            (usuario_id,),
        )
        row = cur.fetchone()
        return row[0] if row else 0
    except Exception as e:
        logger.warning("Error obteniendo intentos para %s: %s", usuario_id, e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def incrementar_intento(usuario_id: str) -> int:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"INSERT INTO {_TABLE_INTENTOS} (usuario_id, cantidad_intentos, ultimo_intento) "
            "VALUES (%s, 1, NOW()) "
            f"ON CONFLICT (usuario_id) DO UPDATE SET "
            f"cantidad_intentos = {_TABLE_INTENTOS}.cantidad_intentos + 1, "
            "ultimo_intento = NOW() "
            "RETURNING cantidad_intentos",
            (usuario_id,),
        )
        nueva_cantidad = cur.fetchone()[0]
        conn.commit()
        logger.info("Intento incrementado: user=%s cantidad=%s", usuario_id, nueva_cantidad)
        return nueva_cantidad
    except Exception as e:
        conn.rollback()
        logger.warning("Error incrementando intento para %s: %s", usuario_id, e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def obtener_ultimo_intento(usuario_id: str) -> datetime | None:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT ultimo_intento FROM {_TABLE_INTENTOS} WHERE usuario_id = %s",
            (usuario_id,),
        )
        row = cur.fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.warning("Error obteniendo ultimo_intento para %s: %s", usuario_id, e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def restablecer_intentos(usuario_id: str):
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"DELETE FROM {_TABLE_INTENTOS} WHERE usuario_id = %s",
            (usuario_id,),
        )
        conn.commit()
        logger.info("Intentos restablecidos para usuario: %s", usuario_id)
    except Exception as e:
        conn.rollback()
        logger.warning("Error restableciendo intentos para %s: %s", usuario_id, e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def cargar_configuracion() -> dict[str, str]:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT * FROM {_TABLE_CONFIG} WHERE id = 1")
        row = cur.fetchone()
        if row:
            cols = [desc[0] for desc in cur.description]
            return dict(zip(cols, row))
        return {"log_channel_id": "0", "errores_channel_id": "0"}
    except Exception as e:
        logger.warning("Error cargando configuraci\u00f3n: %s", e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def actualizar_configuracion(clave: str, valor: str, actualizado_por: str):
    if clave not in ("log_channel_id", "errores_channel_id"):
        raise ValueError(f"Clave de configuraci\u00f3n inv\u00e1lida: {clave}")

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"INSERT INTO {_TABLE_CONFIG} (id, {clave}, updated_by) "
            "VALUES (1, %s, %s) "
            f"ON CONFLICT (id) DO UPDATE SET "
            f"{clave} = EXCLUDED.{clave}, "
            "updated_at = NOW(), "
            "updated_by = EXCLUDED.updated_by",
            (valor, actualizado_por),
        )
        conn.commit()
        logger.info("Configuraci\u00f3n actualizada: %s = %s (por %s)", clave, valor, actualizado_por)
    except Exception as e:
        conn.rollback()
        logger.warning("Error actualizando configuraci\u00f3n %s: %s", clave, e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def guardar_sesion_entrevista(datos: dict):
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"INSERT INTO {_TABLE_SESIONES} "
            "(user_id, staff_id, channel_id, guild_id, session_id, questions, current_index, "
            "answers, motives, intento, started_at, estado) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s::jsonb, %s, %s, %s) "
            f"ON CONFLICT (user_id) DO UPDATE SET "
            f"staff_id = EXCLUDED.staff_id, channel_id = EXCLUDED.channel_id, "
            f"guild_id = EXCLUDED.guild_id, session_id = EXCLUDED.session_id, "
            f"questions = EXCLUDED.questions, current_index = EXCLUDED.current_index, "
            f"answers = EXCLUDED.answers, motives = EXCLUDED.motives, "
            f"intento = EXCLUDED.intento, started_at = EXCLUDED.started_at, "
            f"estado = EXCLUDED.estado, updated_at = NOW()",
            (
                datos["user_id"],
                datos["staff_id"],
                datos.get("channel_id"),
                datos.get("guild_id"),
                datos.get("session_id", ""),
                json.dumps(datos.get("questions", []), ensure_ascii=False, default=str),
                datos.get("current_index", 0),
                json.dumps(datos.get("answers", []), ensure_ascii=False, default=str),
                json.dumps(datos.get("motives", {}) or {}, ensure_ascii=False, default=str),
                datos.get("intento", 1),
                datos.get("started_at"),
                datos.get("estado", "ACTIVA"),
            ),
        )
        conn.commit()
        logger.info(
            "[ENTREVISTA] Sesión persistida: user=%s estado=%s pregunta=%s session=%s",
            datos["user_id"], datos.get("estado"), datos.get("current_index", 0),
            datos.get("session_id", ""),
        )
    except Exception as e:
        conn.rollback()
        logger.warning("Error guardando sesi\u00f3n de entrevista: %s", e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def recuperar_sesion_entrevista(user_id: str) -> dict | None:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT * FROM {_TABLE_SESIONES} WHERE user_id = %s",
            (user_id,),
        )
        return _row_to_dict(cur)
    except Exception as e:
        logger.warning("Error recuperando sesi\u00f3n de entrevista para %s: %s", user_id, e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def eliminar_sesion_entrevista(user_id: str) -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"DELETE FROM {_TABLE_SESIONES} WHERE user_id = %s",
            (user_id,),
        )
        conn.commit()
        eliminado = cur.rowcount > 0
        if eliminado:
            logger.info("Sesi\u00f3n de entrevista eliminada: user=%s", user_id)
        return eliminado
    except Exception as e:
        conn.rollback()
        logger.warning("Error eliminando sesi\u00f3n de entrevista para %s: %s", user_id, e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def limpiar_sesiones_antiguas(dias: int = 7) -> int:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=dias)
        cur.execute(
            f"DELETE FROM {_TABLE_SESIONES} WHERE updated_at < %s",
            (cutoff,),
        )
        conn.commit()
        eliminadas = cur.rowcount
        if eliminadas:
            logger.info(
                "Sesiones de entrevista antiguas eliminadas (m\u00e1s de %s d\u00edas): %s",
                dias, eliminadas,
            )
        return eliminadas
    except Exception as e:
        conn.rollback()
        logger.warning("Error limpiando sesiones de entrevista antiguas: %s", e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def contar_sesiones_recuperables() -> int:
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT COUNT(*) FROM {_TABLE_SESIONES} "
            "WHERE estado IN ('ACTIVA', 'EXPIRADA')",
        )
        row = cur.fetchone()
        return row[0] if row else 0
    except Exception as e:
        logger.warning("Error contando sesiones de entrevista recuperables: %s", e)
        raise
    finally:
        cur.close()
        _close_conn(conn)


def listar_sesiones_por_canal(canal_id: str) -> list[dict]:
    """Devuelve todas las sesiones asociadas a un canal (ticket)."""
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT * FROM {_TABLE_SESIONES} "
            "WHERE channel_id = %s ORDER BY updated_at DESC",
            (canal_id,),
        )
        return _rows_to_list(cur)
    except Exception as e:
        logger.warning("Error listando sesiones por canal %s: %s", canal_id, e)
        raise
    finally:
        cur.close()
        _close_conn(conn)
