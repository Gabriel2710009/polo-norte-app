"""
licencia.py — Verificación de licencia para ArmamentBot
"""

import asyncio
import logging
import os

import aiohttp

logger = logging.getLogger("ArmamentBot")

LICENCIA_API_URL = os.getenv("LICENCIA_API_URL")
LICENCIA_BOT_KEY = os.getenv("LICENCIA_BOT_KEY")
GUILD_ID         = os.getenv("GUILD_ID_LICENCIA")

MAX_REINTENTOS  = 3
TIMEOUT_SEGUNDOS = 20   # subido de 10 a 20
ESPERA_REINTENTO = 5    # segundos entre reintentos


async def verificar_licencia() -> bool:
    if not LICENCIA_API_URL or not LICENCIA_BOT_KEY or not GUILD_ID:
        logger.critical(
            "❌ Variables de licencia no configuradas. "
            "Asegurate de tener LICENCIA_API_URL, LICENCIA_BOT_KEY y GUILD_ID_LICENCIA en el .env"
        )
        return False

    url = f"{LICENCIA_API_URL.rstrip('/')}/verificar/{GUILD_ID}"

    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            timeout = aiohttp.ClientTimeout(total=TIMEOUT_SEGUNDOS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    url,
                    headers={"x-api-key": LICENCIA_BOT_KEY},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        msg  = f"✅ Licencia válida | Servidor: {data.get('nombre', 'N/A')}"
                        if data.get("vence"):
                            msg += f" | Vence: {data['vence'][:10]}"
                        logger.info(msg)
                        return True

                    try:
                        error = (await resp.json()).get("detail", "Sin detalle")
                    except Exception:
                        error = await resp.text()

                    logger.critical(
                        f"❌ LICENCIA INVÁLIDA — El bot no puede arrancar.\n"
                        f"   Motivo: {error}\n"
                        f"   Guild ID: {GUILD_ID}\n"
                        f"   Contactá al desarrollador para renovar tu licencia."
                    )
                    return False

        except (aiohttp.ClientConnectorError, aiohttp.ServerTimeoutError, asyncio.TimeoutError) as e:
            if intento < MAX_REINTENTOS:
                logger.warning(
                    f"⚠️ Intento {intento}/{MAX_REINTENTOS} fallido al verificar licencia: {e}. "
                    f"Reintentando en {ESPERA_REINTENTO}s..."
                )
                await asyncio.sleep(ESPERA_REINTENTO)
            else:
                logger.warning(
                    f"⚠️ No se pudo verificar la licencia tras {MAX_REINTENTOS} intentos ({e}). "
                    "Continuando de todas formas para evitar caída del bot."
                )
                # En lugar de cerrar el bot, dejamos pasar.
                # Cambiá esto a `return False` si querés comportamiento estricto.
                return True

        except Exception as e:
            logger.critical(f"❌ Error inesperado verificando licencia: {e}")
            return False

    return True