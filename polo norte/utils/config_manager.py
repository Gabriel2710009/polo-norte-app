"""Gestor de configuraciones globales del bot.

Migrado de JSON a PostgreSQL. Conserva los valores por defecto (fallback)
para mantener compatibilidad durante la migración.
"""

import logging
from database import config_db

logger = logging.getLogger("ConfigManager")


def load_config():
    """Carga la config global (owner_id)."""
    valor = config_db.cargar_global_clave("owner_id")
    if valor:
        return {"owner_id": valor}
    return {"owner_id": None}


def save_config(config: dict):
    """Guarda la config global."""
    if "owner_id" in config and config["owner_id"] is not None:
        config_db.guardar_global_clave("owner_id", str(config["owner_id"]))


# ── Configuración de aprobación ──────────────────────────────────────────

DEFAULT_APROBAR_CONFIG = {
    "roles_asignar": [
        1306126579482628106,
        1306129853111599106,
        1305968998206148760,
        1307900695264890991,
        1306131154360860674,
        1335265463441162350,
        1415042052051173516,
        1410719738484494346,
    ],
    "roles_eliminar": [
        1352785931131813930,
        1306130293987610735,
    ],
}


def load_aprobar_config() -> dict:
    """Carga desde DB. Si está vacía, migra el default JSON y lo persiste."""
    return config_db.cargar_aprobar(datos=DEFAULT_APROBAR_CONFIG)


def save_aprobar_config(config: dict):
    """Guarda a DB."""
    return config_db.guardar_aprobar(config)


# ── Configuración de bienvenida ──────────────────────────────────────────

DEFAULT_BIENVENIDA_MENSAJE = (
    "Nombre IC:\n"
    "Numero IC:\n"
    "IBAN IC: (número de cuenta de banco)\n"
    "Steam URL/Nombre:\n"
    "————————————————————————————\n"
    "\n"
    "* Iniciar/salir de servicio.\n"
    "  https://discord.com/channels/1305788361679573022/130612761267629147\n"
    "\n"
    "* Apuntar apertura.\n"
    "  https://discord.com/channels/1305788361679573022/130784003419233472\n"
    "\n"
    "* Copiar \"trabajaste un total de ...\" y pegar en aviso de horas.\n"
    "  https://discord.com/channels/1305788361679573022/130762400329598409\n"
    "\n"
    "* Restarle sumar horas (también sumar/restar en mensaje de aviso de horas).\n"
    "  https://discord.com/channels/1305788361679573022/1307624192631377970\n"
    "\n"
    "* Si no puedes horizontes pedir inactividad con antelación.\n"
    "  https://discord.com/channels/1305788361679573022/1307627063886867328\n"
    "\n"
    "* Apuntar ventas después de vender.\n"
    "  https://discord.com/channels/1305788361679573022/1305968451233614720\n"
    "\n"
    "* Restar ventas.\n"
    "  https://discord.com/channels/1305788361679573022/1305968483804853780\n"
    "\n"
    "* Leer un poco el canal de anuncios antes de trabajar.\n"
    "  https://discord.com/channels/1305788361679573022/1307626015320838204\n"
    "\n"
    "Cualquier duda podéis contactar con los <@&1306134227896481804> y con "
    "los <@&1306139424172198932> por vuestro canal privado podéis preguntar "
    "e informar lo que sea necesario."
)


def load_bienvenida_config() -> dict:
    return config_db.cargar_bienvenida(datos={"mensaje": DEFAULT_BIENVENIDA_MENSAJE})


def save_bienvenida_config(config: dict, actualizado_por: str = ""):
    return config_db.guardar_bienvenida(config, actualizado_por=actualizado_por)