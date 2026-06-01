import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL

logger = logging.getLogger("ArmamentBot")

_JUSTIFICACIONES_CONTEXT_CACHE: dict = {
    "expires_at": None,
    "contexto": None,
}
JUSTIFICACIONES_CONTEXT_TTL_SECONDS = 20


# ─── CONEXIÓN ─────────────────────────────────────────────────

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


# ─── INICIALIZACIÓN ───────────────────────────────────────────

def inicializar_base_datos():
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS registros_armas (
                id                           SERIAL PRIMARY KEY,
                tipo                         VARCHAR(20)  NOT NULL,
                timestamp                    TIMESTAMP    NOT NULL,
                nombre                       VARCHAR(255),
                id_personaje                 VARCHAR(50),
                discord_id                   VARCHAR(50),
                steamid                      VARCHAR(100),
                discord                      VARCHAR(255),
                objeto                       TEXT,
                cantidad                     INTEGER DEFAULT 1,
                almacen                      TEXT,
                en_operativo                 BOOLEAN DEFAULT FALSE,
                validado                     BOOLEAN DEFAULT FALSE,
                validado_por                 VARCHAR(255),
                fecha_validacion             TIMESTAMP,
                justificacion_validacion     TEXT,
                no_validado                  BOOLEAN DEFAULT FALSE,
                no_validado_por              VARCHAR(255),
                fecha_no_validado            TIMESTAMP,
                devuelto                     BOOLEAN DEFAULT FALSE,
                devuelto_por                 VARCHAR(255),
                fecha_devolucion             TIMESTAMP,
                razon_retiro                 TEXT,
                devolucion_request_message_id BIGINT,
                devolucion_request_channel_id BIGINT,
                alerta_message_id            BIGINT,
                alerta_channel_id            BIGINT,
                created_at                   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migraciones silenciosas
        for col_sql in [
            "ALTER TABLE registros_armas ADD COLUMN IF NOT EXISTS justificacion_validacion TEXT",
            "ALTER TABLE registros_armas ADD COLUMN IF NOT EXISTS razon_retiro TEXT",
            "ALTER TABLE registros_armas ADD COLUMN IF NOT EXISTS devuelto BOOLEAN DEFAULT FALSE",
            "ALTER TABLE registros_armas ADD COLUMN IF NOT EXISTS devuelto_por VARCHAR(255)",
            "ALTER TABLE registros_armas ADD COLUMN IF NOT EXISTS fecha_devolucion TIMESTAMP",
            "ALTER TABLE registros_armas ADD COLUMN IF NOT EXISTS devolucion_request_message_id BIGINT",
            "ALTER TABLE registros_armas ADD COLUMN IF NOT EXISTS devolucion_request_channel_id BIGINT",
            "ALTER TABLE registros_armas ADD COLUMN IF NOT EXISTS razon_message_id BIGINT",
            "ALTER TABLE registros_armas ADD COLUMN IF NOT EXISTS razon_channel_id BIGINT",
            # Nuevas columnas para rastrear mensaje de confirmación del armero
            "ALTER TABLE registros_armas ADD COLUMN IF NOT EXISTS armero_confirm_message_id BIGINT",
            "ALTER TABLE registros_armas ADD COLUMN IF NOT EXISTS armero_confirm_channel_id BIGINT",
        ]:
            cursor.execute(col_sql)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config_alertas (
                id               SERIAL PRIMARY KEY,
                alertas_activas  BOOLEAN DEFAULT TRUE,
                objetos_alertar  TEXT[],
                updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_by       VARCHAR(255)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS operativos_programados (
                id          SERIAL PRIMARY KEY,
                event_id    BIGINT UNIQUE,
                nombre      VARCHAR(255),
                fecha_hora  TIMESTAMP NOT NULL,
                mensaje_id  BIGINT,
                canal_id    BIGINT,
                tipo        VARCHAR(16) DEFAULT 'operativo',
                estado      VARCHAR(32) DEFAULT 'programado',
                inicio      TIMESTAMP NULL,
                fin         TIMESTAMP NULL,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                creado_por  VARCHAR(255),
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for col_sql in [
            "ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS event_id BIGINT UNIQUE",
            "ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS nombre VARCHAR(255)",
            "ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS fecha_hora TIMESTAMP NULL",
            "ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS canal_id BIGINT",
            "ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS tipo VARCHAR(16) DEFAULT 'operativo'",
            "ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS estado VARCHAR(32) DEFAULT 'programado'",
            "ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS inicio TIMESTAMP NULL",
            "ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS fin TIMESTAMP NULL",
            "ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        ]:
            cursor.execute(col_sql)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clip_channels (
                user_id    BIGINT PRIMARY KEY,
                channel_id BIGINT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clip_panel (
                id                SERIAL PRIMARY KEY,
                panel_channel_id  BIGINT NOT NULL,
                panel_message_id  BIGINT NOT NULL,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clip_admin_panel (
                id                SERIAL PRIMARY KEY,
                panel_channel_id  BIGINT NOT NULL,
                panel_message_id  BIGINT NOT NULL,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config_antirrobo (
                id                              SERIAL PRIMARY KEY,
                activo                          BOOLEAN DEFAULT TRUE,
                canal_alerta_id                 BIGINT  NOT NULL,
                ventana_minutos                 INTEGER DEFAULT 120,
                umbral_retiros_masivos          INTEGER DEFAULT 20,
                umbral_desbalance_retiros       INTEGER DEFAULT 20,
                umbral_desbalance_depositos_max INTEGER DEFAULT 5,
                umbral_ratio_retiros            INTEGER DEFAULT 5,
                umbral_ratio_factor             NUMERIC(10,2) DEFAULT 5.00,
                operativo_relajacion_factor     NUMERIC(10,2) DEFAULT 1.80,
                objetos_monitoreados            TEXT[],
                updated_by                      VARCHAR(255),
                updated_at                      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute(
            "ALTER TABLE config_antirrobo ADD COLUMN IF NOT EXISTS objetos_monitoreados TEXT[]"
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS antirrobo_whitelist (
                discord_id VARCHAR(50) PRIMARY KEY,
                nombre     VARCHAR(255),
                added_by   VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS estado_operativo (
                id           INTEGER PRIMARY KEY,
                activo       BOOLEAN DEFAULT FALSE,
                inicio       TIMESTAMP NULL,
                iniciado_por VARCHAR(50),
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migraciones estado_operativo
        for col_sql in [
            "ALTER TABLE estado_operativo ADD COLUMN IF NOT EXISTS control_msg_id     BIGINT NULL",
            "ALTER TABLE estado_operativo ADD COLUMN IF NOT EXISTS control_channel_id BIGINT NULL",
            "ALTER TABLE estado_operativo ADD COLUMN IF NOT EXISTS verify_msg_id      BIGINT NULL",
            "ALTER TABLE estado_operativo ADD COLUMN IF NOT EXISTS verify_channel_id  BIGINT NULL",
            "ALTER TABLE estado_operativo ADD COLUMN IF NOT EXISTS verify_sent_at     TIMESTAMP NULL",
            "ALTER TABLE estado_operativo ADD COLUMN IF NOT EXISTS pistolas_retiros   JSONB  NULL",
            "ALTER TABLE estado_operativo ADD COLUMN IF NOT EXISTS pistolas_depositos JSONB  NULL",
        ]:
            cursor.execute(col_sql)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS asistencia_semanal (
                week_start            DATE NOT NULL,
                discord_id            VARCHAR(50) NOT NULL,
                operativos_realizados  INTEGER DEFAULT 0,
                justificado           BOOLEAN DEFAULT FALSE,
                aviso_enviado         BOOLEAN DEFAULT FALSE,
                updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (week_start, discord_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS justificaciones_texto (
                id                 SERIAL PRIMARY KEY,
                discord_id         BIGINT NOT NULL,
                usuario            VARCHAR(255) NOT NULL,
                tipo               VARCHAR(32) NOT NULL,
                subtipo            VARCHAR(64) NOT NULL,
                texto              TEXT NOT NULL,
                mensaje_origen_id  BIGINT,
                canal_origen_id    BIGINT,
                created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for col_sql in [
            "ALTER TABLE asistencia_semanal ADD COLUMN IF NOT EXISTS operativos_realizados INTEGER DEFAULT 0",
            "ALTER TABLE asistencia_semanal ADD COLUMN IF NOT EXISTS justificado BOOLEAN DEFAULT FALSE",
            "ALTER TABLE asistencia_semanal ADD COLUMN IF NOT EXISTS aviso_enviado BOOLEAN DEFAULT FALSE",
            "ALTER TABLE asistencia_semanal ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        ]:
            cursor.execute(col_sql)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config_asistencia_semanal (
                id              INTEGER PRIMARY KEY,
                activo          BOOLEAN DEFAULT FALSE,
                activado_por    VARCHAR(255),
                activado_at     TIMESTAMP NULL,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for col_sql in [
            "ALTER TABLE config_asistencia_semanal ADD COLUMN IF NOT EXISTS activo BOOLEAN DEFAULT FALSE",
            "ALTER TABLE config_asistencia_semanal ADD COLUMN IF NOT EXISTS activado_por VARCHAR(255)",
            "ALTER TABLE config_asistencia_semanal ADD COLUMN IF NOT EXISTS activado_at TIMESTAMP NULL",
            "ALTER TABLE config_asistencia_semanal ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        ]:
            cursor.execute(col_sql)
        cursor.execute(
            "INSERT INTO config_asistencia_semanal (id, activo, activado_por, activado_at, updated_at) "
            "VALUES (1, FALSE, NULL, NULL, NOW()) "
            "ON CONFLICT (id) DO NOTHING"
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config_chemi (
                id              INTEGER PRIMARY KEY,
                activo          BOOLEAN DEFAULT TRUE,
                actualizado_por VARCHAR(255),
                actualizado_at  TIMESTAMP NULL,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for col_sql in [
            "ALTER TABLE config_chemi ADD COLUMN IF NOT EXISTS activo BOOLEAN DEFAULT TRUE",
            "ALTER TABLE config_chemi ADD COLUMN IF NOT EXISTS actualizado_por VARCHAR(255)",
            "ALTER TABLE config_chemi ADD COLUMN IF NOT EXISTS actualizado_at TIMESTAMP NULL",
            "ALTER TABLE config_chemi ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        ]:
            cursor.execute(col_sql)
        cursor.execute(
            "INSERT INTO config_chemi (id, activo, actualizado_por, actualizado_at, updated_at) "
            "VALUES (1, TRUE, NULL, NULL, NOW()) "
            "ON CONFLICT (id) DO NOTHING"
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS voice_channels (
                user_id    BIGINT PRIMARY KEY,
                channel_id BIGINT NOT NULL,
                role_id    BIGINT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute(
            "ALTER TABLE voice_channels ADD COLUMN IF NOT EXISTS role_id BIGINT"
        )
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS voice_admin_panel (
                id                SERIAL PRIMARY KEY,
                panel_channel_id  BIGINT NOT NULL,
                panel_message_id  BIGINT NOT NULL,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chemi_deudas (
                id                  SERIAL PRIMARY KEY,
                discord_id          VARCHAR(50) UNIQUE NOT NULL,
                nombre              VARCHAR(255),
                pistolas_retiradas   INTEGER DEFAULT 0,
                debe_devolver        INTEGER DEFAULT 0,
                activa              BOOLEAN DEFAULT TRUE,
                aviso_altos_cargos  BOOLEAN DEFAULT FALSE,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                deadline            TIMESTAMP,
                cancelada_at        TIMESTAMP NULL
            )
        """)
        for col_sql in [
            "ALTER TABLE chemi_deudas ADD COLUMN IF NOT EXISTS aviso_altos_cargos BOOLEAN DEFAULT FALSE",
            "ALTER TABLE chemi_deudas ADD COLUMN IF NOT EXISTS cancelada_at TIMESTAMP NULL",
        ]:
            cursor.execute(col_sql)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chemi_limite_resets (
                id             SERIAL PRIMARY KEY,
                discord_id     VARCHAR(50) NOT NULL,
                reseteado_por  VARCHAR(255),
                motivo         TEXT,
                reset_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chemi_contadores (
                discord_id VARCHAR(50) PRIMARY KEY,
                nombre     VARCHAR(255),
                contador   INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chemi_creditos (
                id                 SERIAL PRIMARY KEY,
                owner_discord_id   VARCHAR(50) NOT NULL,
                owner_nombre       VARCHAR(255),
                cantidad_total     INTEGER NOT NULL DEFAULT 0,
                cantidad_restante  INTEGER NOT NULL DEFAULT 0,
                estado             VARCHAR(32) DEFAULT 'pendiente',
                message_id         BIGINT,
                channel_id         BIGINT,
                created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chemi_transferencias (
                id               SERIAL PRIMARY KEY,
                credito_id       INTEGER REFERENCES chemi_creditos(id) ON DELETE SET NULL,
                from_discord_id  VARCHAR(50),
                to_discord_id    VARCHAR(50),
                cantidad         INTEGER NOT NULL DEFAULT 0,
                tipo             VARCHAR(32) NOT NULL,
                actor_discord_id VARCHAR(50),
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chemi_panel (
                id                SERIAL PRIMARY KEY,
                panel_channel_id  BIGINT NOT NULL,
                panel_message_id  BIGINT NOT NULL,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


        # Índices
        indices = [
            "CREATE INDEX IF NOT EXISTS idx_discord_id  ON registros_armas(discord_id)",
            "CREATE INDEX IF NOT EXISTS idx_timestamp   ON registros_armas(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_tipo        ON registros_armas(tipo)",
            "CREATE INDEX IF NOT EXISTS idx_operativo   ON registros_armas(en_operativo)",
            "CREATE INDEX IF NOT EXISTS idx_mensaje_id  ON operativos_programados(mensaje_id)",
            "CREATE INDEX IF NOT EXISTS idx_event_id    ON operativos_programados(event_id)",
            "CREATE INDEX IF NOT EXISTS idx_estado_op_programado ON operativos_programados(estado)",
            "CREATE INDEX IF NOT EXISTS idx_validado    ON registros_armas(validado)",
            "CREATE INDEX IF NOT EXISTS idx_devuelto    ON registros_armas(devuelto)",
            "CREATE INDEX IF NOT EXISTS idx_alerta_message           ON registros_armas(alerta_message_id)",
            "CREATE INDEX IF NOT EXISTS idx_devolucion_request_message ON registros_armas(devolucion_request_message_id)",
            "CREATE INDEX IF NOT EXISTS idx_config_alertas_updated   ON config_alertas(updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_nombre       ON registros_armas(nombre)",
            "CREATE INDEX IF NOT EXISTS idx_id_personaje ON registros_armas(id_personaje)",
            "CREATE INDEX IF NOT EXISTS idx_antirrobo_updated ON config_antirrobo(updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_chemi_deudas_discord ON chemi_deudas(discord_id)",
            "CREATE INDEX IF NOT EXISTS idx_chemi_limite_resets ON chemi_limite_resets(discord_id, reset_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_chemi_contadores_contador ON chemi_contadores(contador DESC)",
            "CREATE INDEX IF NOT EXISTS idx_chemi_creditos_owner_estado ON chemi_creditos(owner_discord_id, estado)",
            "CREATE INDEX IF NOT EXISTS idx_chemi_creditos_message ON chemi_creditos(message_id)",
            "CREATE INDEX IF NOT EXISTS idx_chemi_transferencias_credito ON chemi_transferencias(credito_id)",
        ]
        for idx in indices:
            cursor.execute(idx)

        conn.commit()
        cursor.close()
        conn.close()
        logger.info("✅ Base de datos inicializada correctamente")
    except Exception as e:
        logger.error(f"❌ Error inicializando base de datos: {e}", exc_info=True)


# ─── REGISTROS DE ARMAS ───────────────────────────────────────

def guardar_registro(datos: dict, operativo_activo: dict) -> Optional[int]:
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()

        en_operativo = operativo_activo.get("activo", False)

        cursor.execute("""
            INSERT INTO registros_armas
            (tipo, timestamp, nombre, id_personaje, discord_id, steamid, discord,
             objeto, cantidad, almacen, en_operativo)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            datos.get("tipo"),
            datos.get("timestamp"),
            datos.get("nombre"),
            datos.get("id_personaje"),
            datos.get("discord_id"),
            datos.get("steamid"),
            datos.get("discord"),
            datos.get("objeto"),
            datos.get("cantidad", 1),
            datos.get("almacen"),
            en_operativo,
        ))

        result     = cursor.fetchone()
        registro_id = result["id"] if result else None

        if registro_id:
            logger.info(
                f"✅ Guardado | ID={registro_id} | "
                f"{datos.get('tipo')} | {datos.get('objeto')} x{datos.get('cantidad', 1)} | "
                f"ID_PJ={datos.get('id_personaje', 'N/A')}"
            )

        conn.commit()
        cursor.close()
        conn.close()
        return registro_id

    except Exception as e:
        err = str(e)
        if "Max client connections" in err or "MaxClients" in err:
            logger.error(f"❌ BD sin conexiones disponibles (Supabase pool lleno) | registro no guardado")
        else:
            logger.error(f"❌ Error guardando registro: {e}", exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass
        return None


# ─── CONFIG ALERTAS ───────────────────────────────────────────

def guardar_config_alertas(
    objetos_alertar: set, alertas_activas: bool, usuario=None
) -> bool:
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM config_alertas")
        cursor.execute("""
            INSERT INTO config_alertas (alertas_activas, objetos_alertar, updated_by)
            VALUES (%s, %s, %s)
            RETURNING id
        """, (alertas_activas, list(objetos_alertar), str(usuario) if usuario else None))
        conn.commit()
        cursor.close()
        conn.close()
        logger.info(
            f"✅ Config alertas guardada | Activas: {alertas_activas} | "
            f"Objetos: {len(objetos_alertar)} | Por: {usuario}"
        )
        return True
    except Exception as e:
        logger.error(f"❌ Error guardando config_alertas: {e}", exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def cargar_config_alertas() -> tuple[bool, set]:
    """Devuelve (alertas_activas, objetos_alertar)."""
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT alertas_activas, objetos_alertar, updated_by, updated_at
            FROM config_alertas
            ORDER BY updated_at DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if row:
            alertas_activas = row["alertas_activas"]
            objetos_alertar = set(row["objetos_alertar"]) if row["objetos_alertar"] else set()
            logger.info(
                f"✅ Config alertas cargada | Activas: {alertas_activas} | "
                f"Objetos: {len(objetos_alertar)} | Por: {row['updated_by']}"
            )
            return alertas_activas, objetos_alertar

        logger.warning("⚠️ No se encontró config de alertas en BD, usando defaults")
        return True, set()

    except Exception as e:
        logger.error(f"❌ Error cargando config_alertas: {e}", exc_info=True)
        return True, set()


# ─── CONFIG ANTIRROBO ─────────────────────────────────────────

def cargar_config_antirrobo_db() -> Optional[dict]:
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT activo, canal_alerta_id, ventana_minutos, umbral_retiros_masivos,
                   umbral_desbalance_retiros, umbral_desbalance_depositos_max,
                   umbral_ratio_retiros, umbral_ratio_factor, operativo_relajacion_factor,
                   objetos_monitoreados, updated_by
            FROM config_antirrobo
            ORDER BY updated_at DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"❌ Error cargando config antirrobo: {e}", exc_info=True)
        return None


def guardar_config_antirrobo_db(cfg: dict, usuario: Optional[str] = None) -> bool:
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO config_antirrobo (
                activo, canal_alerta_id, ventana_minutos, umbral_retiros_masivos,
                umbral_desbalance_retiros, umbral_desbalance_depositos_max,
                umbral_ratio_retiros, umbral_ratio_factor, operativo_relajacion_factor,
                objetos_monitoreados, updated_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            cfg["activo"],
            cfg["canal_alerta_id"],
            cfg["ventana_minutos"],
            cfg["umbral_retiros_masivos"],
            cfg["umbral_desbalance_retiros"],
            cfg["umbral_desbalance_depositos_max"],
            cfg["umbral_ratio_retiros"],
            cfg["umbral_ratio_factor"],
            cfg["operativo_relajacion_factor"],
            list(cfg.get("objetos_monitoreados", set())),
            usuario,
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ Error guardando config antirrobo: {e}", exc_info=True)
        return False


# ─── WHITELIST ANTIRROBO ──────────────────────────────────────

def usuario_en_whitelist_antirrobo(discord_id: Optional[str]) -> bool:
    if not discord_id:
        return False
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM antirrobo_whitelist WHERE discord_id = %s LIMIT 1",
            (str(discord_id),),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return bool(row)
    except Exception as e:
        logger.error(f"❌ Error consultando whitelist antirrobo: {e}", exc_info=True)
        return False


def toggle_whitelist_antirrobo(
    discord_id: int, nombre: str, actor: str
) -> Optional[bool]:
    """True = agregado, False = eliminado, None = error."""
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM antirrobo_whitelist WHERE discord_id = %s",
            (str(discord_id),),
        )
        existe = cursor.fetchone() is not None
        if existe:
            cursor.execute(
                "DELETE FROM antirrobo_whitelist WHERE discord_id = %s",
                (str(discord_id),),
            )
            agregado = False
        else:
            cursor.execute(
                "INSERT INTO antirrobo_whitelist (discord_id, nombre, added_by) VALUES (%s, %s, %s)",
                (str(discord_id), nombre, actor),
            )
            agregado = True
        conn.commit()
        cursor.close()
        conn.close()
        return agregado
    except Exception as e:
        logger.error(f"❌ Error toggle whitelist antirrobo: {e}", exc_info=True)
        return None


def obtener_whitelist_antirrobo() -> list:
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT discord_id, nombre, added_by, created_at
            FROM antirrobo_whitelist
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return rows or []
    except Exception as e:
        logger.error(f"❌ Error obteniendo whitelist antirrobo: {e}", exc_info=True)
        return []


# ─── ESTADO OPERATIVO ─────────────────────────────────────────

def guardar_estado_operativo_db(
    activo: bool,
    inicio: Optional[datetime],
    iniciado_por: Optional[int],
    operativo_activo: dict,
) -> None:
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()

        pistolas_retiros   = dict(operativo_activo.get("pistolas_retiros")  or {})
        pistolas_depositos = dict(operativo_activo.get("pistolas_depositos") or {})

        cursor.execute("""
            INSERT INTO estado_operativo
            (id, activo, inicio, iniciado_por, updated_at,
             control_msg_id, control_channel_id, verify_msg_id, verify_channel_id, verify_sent_at, pistolas_retiros, pistolas_depositos)
            VALUES (1, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET activo             = EXCLUDED.activo,
                inicio             = EXCLUDED.inicio,
                iniciado_por       = EXCLUDED.iniciado_por,
                updated_at         = NOW(),
                control_msg_id     = EXCLUDED.control_msg_id,
                control_channel_id = EXCLUDED.control_channel_id,
                verify_msg_id      = EXCLUDED.verify_msg_id,
                verify_channel_id  = EXCLUDED.verify_channel_id,
                verify_sent_at     = EXCLUDED.verify_sent_at,
                pistolas_retiros   = EXCLUDED.pistolas_retiros,
                pistolas_depositos = EXCLUDED.pistolas_depositos
        """, (
            activo,
            inicio,
            str(iniciado_por) if iniciado_por else None,
            operativo_activo.get("control_msg_id"),
            operativo_activo.get("control_channel_id"),
            operativo_activo.get("verify_msg_id"),
            operativo_activo.get("verify_channel_id"),
            operativo_activo.get("verify_sent_at"),
            json.dumps(pistolas_retiros)   if pistolas_retiros   else None,
            json.dumps(pistolas_depositos) if pistolas_depositos else None,
        ))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error guardando estado operativo en BD: {e}", exc_info=True)


def cargar_estado_operativo_db() -> Optional[dict]:
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT activo, inicio, iniciado_por,
                   control_msg_id, control_channel_id,
                   verify_msg_id, verify_channel_id,
                   verify_sent_at,
                   pistolas_retiros, pistolas_depositos
            FROM estado_operativo WHERE id = 1
        """)
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"❌ Error cargando estado operativo desde BD: {e}", exc_info=True)
        return None


def guardar_operativo_programado_db(
    *,
    event_id: int,
    nombre: Optional[str],
    fecha_hora: datetime,
    canal_id: Optional[int] = None,
    mensaje_id: Optional[int] = None,
    creado_por: Optional[str] = None,
    tipo: str = "operativo",
    estado: str = "programado",
    inicio: Optional[datetime] = None,
    fin: Optional[datetime] = None,
) -> None:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS fecha_hora TIMESTAMP NULL")
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS event_id BIGINT UNIQUE")
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS nombre VARCHAR(255)")
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS canal_id BIGINT")
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS tipo VARCHAR(16) DEFAULT 'operativo'")
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS estado VARCHAR(32) DEFAULT 'programado'")
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS inicio TIMESTAMP NULL")
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS fin TIMESTAMP NULL")
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        cursor.execute(
            """
            INSERT INTO operativos_programados
                (event_id, nombre, fecha_hora, mensaje_id, canal_id, creado_por, tipo, estado, inicio, fin, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (event_id) DO UPDATE SET
                nombre = EXCLUDED.nombre,
                fecha_hora = EXCLUDED.fecha_hora,
                mensaje_id = COALESCE(EXCLUDED.mensaje_id, operativos_programados.mensaje_id),
                canal_id = COALESCE(EXCLUDED.canal_id, operativos_programados.canal_id),
                creado_por = COALESCE(EXCLUDED.creado_por, operativos_programados.creado_por),
                tipo = COALESCE(EXCLUDED.tipo, operativos_programados.tipo),
                estado = EXCLUDED.estado,
                inicio = COALESCE(EXCLUDED.inicio, operativos_programados.inicio),
                fin = COALESCE(EXCLUDED.fin, operativos_programados.fin),
                updated_at = NOW()
            """,
            (
                int(event_id),
                nombre,
                fecha_hora,
                mensaje_id,
                canal_id,
                creado_por,
                tipo,
                estado,
                inicio,
                fin,
            ),
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error guardando operativo programado en BD: {e}", exc_info=True)


def actualizar_operativo_programado_db(
    event_id: int,
    *,
    nombre: Optional[str] = None,
    fecha_hora: Optional[datetime] = None,
    mensaje_id: Optional[int] = None,
    canal_id: Optional[int] = None,
    creado_por: Optional[str] = None,
    tipo: Optional[str] = None,
    estado: Optional[str] = None,
    inicio: Optional[datetime] = None,
    fin: Optional[datetime] = None,
) -> None:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS fecha_hora TIMESTAMP NULL")
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS event_id BIGINT UNIQUE")
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS nombre VARCHAR(255)")
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS canal_id BIGINT")
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS tipo VARCHAR(16) DEFAULT 'operativo'")
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS estado VARCHAR(32) DEFAULT 'programado'")
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS inicio TIMESTAMP NULL")
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS fin TIMESTAMP NULL")
        cursor.execute("ALTER TABLE operativos_programados ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        cursor.execute(
            """
            UPDATE operativos_programados
            SET nombre = COALESCE(%s, nombre),
                fecha_hora = COALESCE(%s, fecha_hora),
                mensaje_id = COALESCE(%s, mensaje_id),
                canal_id = COALESCE(%s, canal_id),
                creado_por = COALESCE(%s, creado_por),
                tipo = COALESCE(%s, tipo),
                estado = COALESCE(%s, estado),
                inicio = COALESCE(%s, inicio),
                fin = COALESCE(%s, fin),
                updated_at = NOW()
            WHERE event_id = %s
            """,
            (
                nombre,
                fecha_hora,
                mensaje_id,
                canal_id,
                creado_por,
                tipo,
                estado,
                inicio,
                fin,
                int(event_id),
            ),
        )
        if cursor.rowcount == 0:
            cursor.execute(
                """
                INSERT INTO operativos_programados
                    (event_id, nombre, fecha_hora, mensaje_id, canal_id, creado_por, tipo, estado, inicio, fin, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    int(event_id),
                    nombre,
                    fecha_hora or datetime.now(),
                    mensaje_id,
                    canal_id,
                    creado_por,
                    tipo or "operativo",
                    estado or "programado",
                    inicio,
                    fin,
                ),
            )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error actualizando operativo programado en BD: {e}", exc_info=True)


def eliminar_operativo_programado_db(event_id: int) -> None:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM operativos_programados WHERE event_id = %s", (int(event_id),))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error eliminando operativo programado de BD: {e}", exc_info=True)


def listar_contexto_justificaciones_db() -> dict:
    now = datetime.utcnow()
    cached_until = _JUSTIFICACIONES_CONTEXT_CACHE.get("expires_at")
    cached_context = _JUSTIFICACIONES_CONTEXT_CACHE.get("contexto")
    if cached_until and cached_context and now <= cached_until:
        logger.debug("ℹ️ [Justificaciones] Cache hit de contexto")
        return {
            "operativos": [dict(item) for item in cached_context.get("operativos", [])],
            "eventos": [dict(item) for item in cached_context.get("eventos", [])],
        }
    logger.debug("ℹ️ [Justificaciones] Cache miss de contexto, consultando BD")

    contexto = {
        "operativos": [],
        "eventos": [],
    }

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT
                    'operativo' AS kind,
                    id::text AS item_id,
                    title AS nombre,
                    description AS descripcion,
                    created_at,
                    discord_channel_id AS canal_id,
                    discord_message_id AS mensaje_id,
                    'open' AS estado,
                    NULL::timestamp AS fecha_hora,
                    'operations' AS source
                FROM operations
                WHERE status = 'open'
                UNION ALL
                SELECT
                    tipo AS kind,
                    event_id::text AS item_id,
                    nombre,
                    '' AS descripcion,
                    created_at,
                    canal_id,
                    mensaje_id,
                    estado,
                    fecha_hora,
                    'operativos_programados' AS source
                FROM operativos_programados
                WHERE estado IN ('activo', 'programado')
                ORDER BY fecha_hora ASC NULLS LAST, created_at ASC
                """
            )
            rows = cursor.fetchall() or []
            now_utc = datetime.utcnow()
            for row in rows:
                estado = str(row.get("estado") or "").strip() or "programado"
                kind = str(row.get("kind") or "operativo").strip().lower() or "operativo"
                fecha_hora = row.get("fecha_hora")
                if kind == "evento" and estado == "programado" and fecha_hora:
                    if getattr(fecha_hora, "tzinfo", None) is not None:
                        fecha_hora = fecha_hora.astimezone(timezone.utc).replace(tzinfo=None)
                    if not (now_utc - timedelta(hours=6) <= fecha_hora <= now_utc + timedelta(hours=24)):
                        continue

                nombre = str(row.get("nombre") or "").strip() or f"Evento {row.get('item_id')}"
                label = nombre if kind == "operativo" or estado == "open" else f"{nombre} ({estado})"
                item = {
                    "tipo": kind,
                    "subtipo": nombre,
                    "label": label,
                    "descripcion": row.get("descripcion") or "",
                    "source": row.get("source") or "",
                    "id": str(row.get("item_id")),
                    "canal_id": row.get("canal_id"),
                    "mensaje_id": row.get("mensaje_id"),
                    "estado": estado,
                }
                if kind == "operativo":
                    contexto["operativos"].append(item)
                else:
                    item["fecha_hora"] = row.get("fecha_hora")
                    contexto["eventos"].append(item)
        except Exception as e:
            logger.debug(f"ℹ️ [Justificaciones] Contexto unificado no disponible o sin resultados: {e}")
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error cargando contexto de justificaciones desde BD: {e}", exc_info=True)

    if not contexto["operativos"]:
        try:
            row = cargar_estado_operativo_db()
            if row and row.get("activo"):
                inicio = row.get("inicio")
                nombre = "Operativo activo"
                if inicio:
                    nombre = f"{nombre} desde {inicio.strftime('%d/%m/%Y %H:%M')}"
                contexto["operativos"].append(
                    {
                        "tipo": "operativo",
                        "subtipo": nombre,
                        "label": nombre,
                        "descripcion": "",
                        "source": "estado_operativo",
                        "id": "1",
                    }
                )
        except Exception as e:
            logger.debug(f"ℹ️ [Justificaciones] No se pudo cargar estado operativo como fallback: {e}")

    if not contexto["eventos"]:
        try:
            import state

            for event_id, row in dict(getattr(state, "operativos_programados", {}) or {}).items():
                estado = str(row.get("estado") or "programado").strip()
                kind = str(row.get("tipo") or "evento").strip().lower() or "evento"
                fecha_hora = row.get("fecha_hora")
                if fecha_hora and getattr(fecha_hora, "tzinfo", None) is not None:
                    fecha_hora = fecha_hora.astimezone(timezone.utc).replace(tzinfo=None)
                if estado == "programado" and fecha_hora:
                    now_utc = datetime.utcnow()
                    if not (now_utc - timedelta(hours=6) <= fecha_hora <= now_utc + timedelta(hours=24)):
                        continue
                nombre = str(row.get("nombre") or "").strip() or f"Evento {event_id}"
                label = nombre if estado == "activo" else f"{nombre} ({estado})"
                contexto["eventos"].append(
                    {
                        "tipo": kind,
                        "subtipo": nombre,
                        "label": label,
                        "descripcion": "",
                        "source": "state.operativos_programados",
                        "id": str(event_id),
                        "fecha_hora": row.get("fecha_hora"),
                        "canal_id": row.get("canal_id"),
                        "mensaje_id": row.get("mensaje_id"),
                        "estado": estado,
                    }
                )
        except Exception as e:
            logger.debug(f"ℹ️ [Justificaciones] No se pudo cargar eventos programados desde memoria: {e}")

    _JUSTIFICACIONES_CONTEXT_CACHE["expires_at"] = now + timedelta(seconds=JUSTIFICACIONES_CONTEXT_TTL_SECONDS)
    _JUSTIFICACIONES_CONTEXT_CACHE["contexto"] = {
        "operativos": [dict(item) for item in contexto["operativos"]],
        "eventos": [dict(item) for item in contexto["eventos"]],
    }
    return contexto


def invalidar_cache_contexto_justificaciones_db() -> None:
    _JUSTIFICACIONES_CONTEXT_CACHE["expires_at"] = None
    _JUSTIFICACIONES_CONTEXT_CACHE["contexto"] = None


def cargar_operativos_programados_db() -> list[dict]:
    """Devuelve los operativos/eventos programados o activos persistidos en BD."""
    resultados: list[dict] = []
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT event_id, nombre, fecha_hora, canal_id, mensaje_id, tipo, estado, inicio, fin, creado_por, created_at, updated_at
            FROM operativos_programados
            WHERE estado IN ('activo', 'programado')
            ORDER BY fecha_hora ASC NULLS LAST, created_at ASC
            """
        )
        rows = cursor.fetchall() or []
        cursor.close()
        conn.close()

        now_utc = datetime.utcnow()
        for row in rows:
            estado = str(row.get("estado") or "").strip() or "programado"
            fecha_hora = row.get("fecha_hora")
            if fecha_hora and getattr(fecha_hora, "tzinfo", None) is not None:
                fecha_hora = fecha_hora.astimezone(timezone.utc).replace(tzinfo=None)
            if estado == "programado" and fecha_hora:
                if not (now_utc - timedelta(hours=6) <= fecha_hora <= now_utc + timedelta(hours=24)):
                    continue
            item = dict(row)
            item["estado"] = estado
            item["fecha_hora"] = fecha_hora or row.get("fecha_hora")
            resultados.append(item)
    except Exception as e:
        logger.error(f"❌ Error cargando operativos programados desde BD: {e}", exc_info=True)
    return resultados


# ─── CONFIG ASISTENCIA SEMANAL ───────────────────────────────

def guardar_config_asistencia_semanal_db(
    activo: bool,
    activado_por: Optional[str],
    activado_at: Optional[datetime],
) -> None:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO config_asistencia_semanal (id, activo, activado_por, activado_at, updated_at)
            VALUES (1, %s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                activo = EXCLUDED.activo,
                activado_por = EXCLUDED.activado_por,
                activado_at = EXCLUDED.activado_at,
                updated_at = NOW()
            """,
            (activo, activado_por, activado_at),
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error guardando config asistencia semanal: {e}", exc_info=True)


def cargar_config_asistencia_semanal_db() -> dict:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT activo, activado_por, activado_at
            FROM config_asistencia_semanal
            WHERE id = 1
            """
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if not row:
            return {"activo": False, "activado_por": None, "activado_at": None}
        return {
            "activo": bool(row.get("activo")),
            "activado_por": row.get("activado_por"),
            "activado_at": row.get("activado_at"),
        }
    except Exception as e:
        logger.error(f"❌ Error cargando config asistencia semanal: {e}", exc_info=True)
        return {"activo": False, "activado_por": None, "activado_at": None}


# â”€â”€â”€ CONFIG CHEMI â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def guardar_config_chemi_db(
    activo: bool,
    actualizado_por: Optional[str],
    actualizado_at: Optional[datetime],
) -> None:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO config_chemi (id, activo, actualizado_por, actualizado_at, updated_at)
            VALUES (1, %s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                activo = EXCLUDED.activo,
                actualizado_por = EXCLUDED.actualizado_por,
                actualizado_at = EXCLUDED.actualizado_at,
                updated_at = NOW()
            """,
            (activo, actualizado_por, actualizado_at),
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"âŒ Error guardando config chemi: {e}", exc_info=True)


def cargar_config_chemi_db() -> dict:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT activo, actualizado_por, actualizado_at
            FROM config_chemi
            WHERE id = 1
            """
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if not row:
            return {"activo": True, "actualizado_por": None, "actualizado_at": None}
        return {
            "activo": bool(row.get("activo")),
            "actualizado_por": row.get("actualizado_por"),
            "actualizado_at": row.get("actualizado_at"),
        }
    except Exception as e:
        logger.error(f"âŒ Error cargando config chemi: {e}", exc_info=True)
        return {"activo": True, "actualizado_por": None, "actualizado_at": None}


def get_chemi_panel_config():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT panel_channel_id, panel_message_id FROM chemi_panel ORDER BY id DESC LIMIT 1"
        )
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result
    except Exception as e:
        logger.error(f"❌ Error obteniendo config panel chemi: {e}")
        return None


def set_chemi_panel_config(panel_channel_id: int, panel_message_id: int):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chemi_panel")
        cursor.execute(
            "INSERT INTO chemi_panel (panel_channel_id, panel_message_id) VALUES (%s, %s)",
            (panel_channel_id, panel_message_id),
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error guardando config panel chemi: {e}")


def reiniciar_asistencia_semanal_semana_db(week_start: str) -> None:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM asistencia_semanal WHERE week_start = %s", (week_start,))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error reiniciando asistencia semanal {week_start}: {e}", exc_info=True)


# ─── CLIPS DB HELPERS ─────────────────────────────────────────

def get_clip_panel_config():
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT panel_channel_id, panel_message_id FROM clip_panel ORDER BY id DESC LIMIT 1"
        )
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result
    except Exception as e:
        logger.error(f"❌ Error obteniendo config panel clips: {e}")
        return None


def set_clip_panel_config(panel_channel_id: int, panel_message_id: int):
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM clip_panel")
        cursor.execute(
            "INSERT INTO clip_panel (panel_channel_id, panel_message_id) VALUES (%s, %s)",
            (panel_channel_id, panel_message_id),
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error guardando config panel clips: {e}")


def get_clip_admin_panel_config():
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT panel_channel_id, panel_message_id FROM clip_admin_panel ORDER BY id DESC LIMIT 1"
        )
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result
    except Exception as e:
        logger.error(f"❌ Error obteniendo config panel admin clips: {e}")
        return None


def set_clip_admin_panel_config(panel_channel_id: int, panel_message_id: int):
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM clip_admin_panel")
        cursor.execute(
            "INSERT INTO clip_admin_panel (panel_channel_id, panel_message_id) VALUES (%s, %s)",
            (panel_channel_id, panel_message_id),
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error guardando config panel admin clips: {e}")


def get_clip_channel_record(user_id: int):
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, channel_id FROM clip_channels WHERE user_id = %s",
            (user_id,),
        )
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result
    except Exception as e:
        logger.error(f"❌ Error obteniendo canal clips: {e}")
        return None


def get_clip_channel_record_by_channel_id(channel_id: int):
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, channel_id FROM clip_channels WHERE channel_id = %s",
            (channel_id,),
        )
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result
    except Exception as e:
        logger.error(f"❌ Error obteniendo canal clips por channel_id: {e}")
        return None


def upsert_clip_channel(user_id: int, channel_id: int):
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO clip_channels (user_id, channel_id)
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET channel_id = EXCLUDED.channel_id
        """, (user_id, channel_id))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error guardando canal clips: {e}")


def delete_clip_channel(user_id: int):
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM clip_channels WHERE user_id = %s", (user_id,))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error borrando canal clips: {e}")


def delete_clip_channel_by_channel_id(channel_id: int):
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM clip_channels WHERE channel_id = %s", (channel_id,))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error borrando canal clips por channel_id: {e}")


def get_all_clip_channel_records() -> list:
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, channel_id FROM clip_channels")
        result = cursor.fetchall()
        cursor.close()
        conn.close()
        return result or []
    except Exception as e:
        logger.error(f"❌ Error obteniendo lista canales clips: {e}")
        return []
def get_voice_channel_record(user_id: int):
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, channel_id, role_id FROM voice_channels WHERE user_id = %s",
            (user_id,),
        )
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result
    except Exception as e:
        logger.error(f"❌ Error obteniendo canal de voz: {e}")
        return None
 
 
def get_voice_channel_record_by_channel_id(channel_id: int):
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, channel_id, role_id FROM voice_channels WHERE channel_id = %s",
            (channel_id,),
        )
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result
    except Exception as e:
        logger.error(f"❌ Error obteniendo canal de voz por channel_id: {e}")
        return None
 
 
def upsert_voice_channel(user_id: int, channel_id: int, role_id: int = None):
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO voice_channels (user_id, channel_id, role_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE
            SET channel_id = EXCLUDED.channel_id,
                role_id    = EXCLUDED.role_id
        """, (user_id, channel_id, role_id))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error guardando canal de voz: {e}")
 
 
def delete_voice_channel(user_id: int):
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM voice_channels WHERE user_id = %s", (user_id,))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error borrando canal de voz: {e}")
 
 
def delete_voice_channel_by_channel_id(channel_id: int):
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM voice_channels WHERE channel_id = %s", (channel_id,))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error borrando canal de voz por channel_id: {e}")
 
 
def get_voice_admin_panel_config():
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT panel_channel_id, panel_message_id FROM voice_admin_panel ORDER BY id DESC LIMIT 1"
        )
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result
    except Exception as e:
        logger.error(f"❌ Error obteniendo config panel admin voz: {e}")
        return None
 
 
def set_voice_admin_panel_config(panel_channel_id: int, panel_message_id: int):
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM voice_admin_panel")
        cursor.execute(
            "INSERT INTO voice_admin_panel (panel_channel_id, panel_message_id) VALUES (%s, %s)",
            (panel_channel_id, panel_message_id),
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error guardando config panel admin voz: {e}")
