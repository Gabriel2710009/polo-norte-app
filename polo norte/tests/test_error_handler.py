import sys
import os
import asyncio
import unittest
from unittest.mock import Mock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord
from discord.app_commands.errors import CommandNotFound
from utils import error_handler


def _await(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestTreeOnError(unittest.TestCase):

    def _interaction(self):
        interaction = Mock()
        interaction.response.is_done = Mock(return_value=False)
        interaction.response.send_message = AsyncMock()
        interaction.followup.send = AsyncMock()
        return interaction

    def test_command_not_found_se_trata_como_esperado(self):
        interaction = self._interaction()
        error = CommandNotFound("hola", [])

        with patch.object(error_handler, "logger") as logger, \
             patch.object(error_handler, "reportar_error") as reportar:
            _await(error_handler.tree_on_error(interaction, error))

        reportar.assert_not_called()
        interaction.response.send_message.assert_awaited_once()
        logger.info.assert_called()

    def test_command_not_found_no_duplica_respuesta(self):
        interaction = self._interaction()
        interaction.response.is_done = Mock(return_value=True)
        error = CommandNotFound("hola", [])

        _await(error_handler.tree_on_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()
        interaction.followup.send.assert_not_awaited()

    def test_error_real_se_reporta(self):
        interaction = self._interaction()
        interaction.command = Mock(name="aprobar")
        error = Exception("boom")

        with patch.object(error_handler, "reportar_error", AsyncMock()) as reportar:
            _await(error_handler.tree_on_error(interaction, error))

        reportar.assert_awaited_once()
        interaction.response.send_message.assert_awaited_once()


class TestOnCommandErrorCog(unittest.TestCase):

    def test_command_not_found_prefijo_ignorado(self):
        cog = error_handler.ErrorCog(Mock())
        ctx = Mock()
        ctx.bot = Mock()
        ctx.message.content = "/inexistente"
        error = discord.ext.commands.CommandNotFound("no existe")

        with patch.object(error_handler, "logger") as logger, \
             patch.object(error_handler, "reportar_error") as reportar:
            _await(cog.on_command_error(ctx, error))

        reportar.assert_not_called()
        logger.info.assert_called()

    def test_error_real_se_reporta(self):
        cog = error_handler.ErrorCog(Mock())
        ctx = Mock()
        ctx.bot = Mock()
        error = Exception("boom")

        with patch.object(error_handler, "reportar_error", AsyncMock()) as reportar:
            _await(cog.on_command_error(ctx, error))

        reportar.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
