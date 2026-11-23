import os
import sys
import ast
import asyncio
import unittest
from unittest.mock import Mock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs import help_cog

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAIN = os.path.join(_ROOT, "main.py")


def _await(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _arbol_main():
    with open(_MAIN, "r", encoding="utf-8") as archivo:
        return ast.parse(archivo.read())


class TestMainWiring(unittest.TestCase):
    def test_importa_los_cogs_nuevos(self):
        modulos = set()
        for nodo in ast.walk(_arbol_main()):
            if isinstance(nodo, ast.ImportFrom) and nodo.module == "cogs":
                modulos.update(alias.name for alias in nodo.names)
        self.assertIn("config_despedir_cog", modulos)
        self.assertIn("despedir_cog", modulos)

    def test_llama_a_los_setup(self):
        registrados = set()
        for nodo in ast.walk(_arbol_main()):
            if (
                isinstance(nodo, ast.Call)
                and isinstance(nodo.func, ast.Attribute)
                and nodo.func.attr == "setup"
                and isinstance(nodo.func.value, ast.Name)
            ):
                registrados.add(nodo.func.value.id)
        self.assertIn("config_despedir_cog", registrados)
        self.assertIn("despedir_cog", registrados)


class TestHelp(unittest.TestCase):
    def test_incluye_los_cuatro_comandos(self):
        titulos = " ".join(seccion["titulo"] for seccion in help_cog.SECCIONES)
        for comando in ("/expulsar", "/despedir", "/config_expulsar", "/config_despedir"):
            self.assertIn(comando, titulos)

    def test_comando_help_envia_embed_actualizado(self):
        interaction = Mock()
        interaction.response.send_message = AsyncMock()
        _await(help_cog.help_command.callback(interaction))
        embed = interaction.response.send_message.call_args.kwargs["embed"]
        nombres = " ".join(campo.name for campo in embed.fields)
        self.assertIn("/expulsar", nombres)
        self.assertIn("/config_despedir", nombres)


if __name__ == "__main__":
    unittest.main()
