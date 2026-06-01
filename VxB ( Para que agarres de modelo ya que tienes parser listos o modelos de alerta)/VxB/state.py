from collections import defaultdict
from typing import Optional

# ─── BOT REFERENCE ───────────────────────────────────────────
BOT = None  # Se setea en main.py como state.BOT = bot

# ─── OPERATIVO ────────────────────────────────────────────────
operativo_activo = {
    "activo":             False,
    "inicio":             None,
    "iniciado_por":       None,
    "registros":          [],
    "pistolas_depositos": defaultdict(int),
    "pistolas_retiros":   defaultdict(int),
    "control_msg_id":     None,
    "control_channel_id": None,
    "verify_msg_id":      None,
    "verify_channel_id":  None,
    "verify_sent_at":     None,
}

# Se marca cuando ya se ejecutó la recuperación del operativo en esta sesión.
operativo_recuperado: bool = False

# ─── RETIROS TEMPORALES (ventana de devolución) ───────────────
RETIROS_TEMPORALES: dict = {}

# ─── TIMEOUTS ─────────────────────────────────────────────────
DEVOLUCION_TIMEOUTS: dict = {}
RAZON_TIMEOUTS:      dict = {}

# ─── ALERTAS ──────────────────────────────────────────────────
ALERTAS_ACTIVAS: bool = True
OBJETOS_ALERTAR: set  = set()   # vacío = alertar todo

# ─── UMBRALES DE CANTIDAD POR OBJETO ──────────────────────────
# Si la cantidad retirada es MENOR a este umbral, no se envía alerta.
# Ejemplo: {"money": 500, "ammo-9": 100}
UMBRALES_CANTIDAD: dict = {}

# ─── ANTIRROBO ────────────────────────────────────────────────
from config import ALERTAS_ANTIRROBO_CHANNEL_ID  # noqa: E402

ANTIRROBO_CONFIG: dict = {
    "activo":                          True,
    "canal_alerta_id":                 ALERTAS_ANTIRROBO_CHANNEL_ID,
    "ventana_minutos":                 120,
    "umbral_retiros_masivos":          20,
    "umbral_desbalance_retiros":       20,
    "umbral_desbalance_depositos_max": 5,
    "umbral_ratio_retiros":            5,
    "umbral_ratio_factor":             5.0,
    "operativo_relajacion_factor":     1.8,
    "objetos_monitoreados":            set(),
    "updated_by":                      None,
}

ANTIRROBO_ALERT_CACHE: dict = {}

# ─── VERIFICACIÓN DE OPERATIVO ────────────────────────────────
VERIFICACION_OPERATIVO_CONFIG: dict = {
    "intervalo_minutos":  60,
    "timeout_minutos":    10,
    "activo":             True,
    "verify_msg_id":      None,
    "verify_channel_id":  None,
}

# ─── OPERATIVOS PROGRAMADOS ───────────────────────────────────
operativos_programados: dict = {}
ultimo_mensaje_operativo       = None

# ─── ASISTENCIA ───────────────────────────────────────────────
scheduled_event_user_cache: dict = {}

ASISTENCIA_SEMANAL_CONFIG = {
    "activo":       False,
    "activado_por": None,
    "activado_at":  None,
}

# ─── CHEMI ────────────────────────────────────────────────────
CHEMI_CONFIG = {
    "activo":          True,
    "actualizado_por": None,
    "actualizado_at":  None,
}