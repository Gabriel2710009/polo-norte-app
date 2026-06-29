import json
import os
import logging

logger = logging.getLogger("ConfigManager")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)

def _load_json(filename, default):
    _ensure_data_dir()
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        _save_json(filename, default)
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Error loading %s: %s", filename, e)
        return default

def _save_json(filename, data):
    _ensure_data_dir()
    path = os.path.join(DATA_DIR, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error("Error saving %s: %s", filename, e)
        return False

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

APROBAR_CONFIG_FILE = "config_aprobar.json"

def load_aprobar_config():
    return _load_json(APROBAR_CONFIG_FILE, DEFAULT_APROBAR_CONFIG)

def save_aprobar_config(config):
    return _save_json(APROBAR_CONFIG_FILE, config)

DEFAULT_BIENVENIDA_MENSAJE = (
    "Nombre IC:\n"
    "Numero IC:\n"
    "IBAN IC: (número de cuenta de banco)\n"
    "Steam URL/Nombre:\n"
    "\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\n"
    "\n"
    "* Iniciar/salir de servicio.\n"
    "  https://discord.com/channels/1305788361679573022/1307624091267629147\n"
    "\n"
    "* Apuntar apertura.\n"
    "  https://discord.com/channels/1305788361679573022/1507840034219233472\n"
    "\n"
    "* Copiar \"trabajaste un total de ...\" y pegar en aviso de horas.\n"
    "  https://discord.com/channels/1305788361679573022/1307624129335398409\n"
    "\n"
    "* Restar/sumar horas (también lo tienes que sumar/restar en el mensaje de aviso de horas).\n"
    "  https://discord.com/channels/1305788361679573022/1307624192631377970\n"
    "\n"
    "* Si no puedes realizar las horas pedir inactividad con antelación.\n"
    "  https://discord.com/channels/1305788361679573022/1307627066576867328\n"
    "\n"
    "* Apuntar ventas después de vender.\n"
    "  https://discord.com/channels/1305788361679573022/1305968451235614720\n"
    "\n"
    "* Restar ventas.\n"
    "  https://discord.com/channels/1305788361679573022/1305968486404853780\n"
    "\n"
    "* Leer un poco el canal de anuncios antes de trabajar.\n"
    "  https://discord.com/channels/1305788361679573022/1307626015320838204\n"
    "\n"
    "Cualquier duda podéis contactar con los <@&1306124327896481804> y con los <@&1306129434172198932> por vuestro canal privado podéis preguntar e informar lo que sea necesario."
)

BIENVENIDA_CONFIG_FILE = "config_bienvenida.json"

def load_bienvenida_config():
    return _load_json(BIENVENIDA_CONFIG_FILE, {"mensaje": DEFAULT_BIENVENIDA_MENSAJE})

def save_bienvenida_config(config):
    return _save_json(BIENVENIDA_CONFIG_FILE, config)

# Punto de despliegue
