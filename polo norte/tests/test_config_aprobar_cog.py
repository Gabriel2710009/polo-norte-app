import sys
import os
import asyncio
import unittest
from unittest.mock import Mock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs import config_aprobar_cog as cog


def _await(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestAprobarConfigViewEditarPrincipal(unittest.TestCase):

    def _view(self, channel=None, msg=None, raise_exc=None):
        client = Mock()
        client.get_channel = Mock(return_value=channel)
        msg = msg or AsyncMock()
        if channel is not None:
            if raise_exc:
                channel.fetch_message = AsyncMock(side_effect=raise_exc)
            else:
                channel.fetch_message = AsyncMock(return_value=msg)
        return cog.AprobarConfigView({"roles_asignar": [], "roles_eliminar": []}, 123, 456, client), msg

    def test_edita_mensaje_principal(self):
        channel = Mock()
        msg = AsyncMock()
        view, msg = self._view(channel=channel, msg=msg)
        embed = Mock()

        _await(view.editar_principal(embed))

        channel.fetch_message.assert_awaited_once_with(456)
        msg.edit.assert_awaited_once()

    def test_canal_no_encontrado_no_edita(self):
        view, msg = self._view(channel=None)
        _await(view.editar_principal(Mock()))
        self.assertIsNone(msg.edit.call_count or None)

    def test_notfound_se_maneja(self):
        from discord import NotFound
        channel = Mock()
        view, _ = self._view(channel=channel, raise_exc=NotFound(Mock(status=404), "not found"))
        _await(view.editar_principal(Mock()))
        channel.fetch_message.assert_awaited_once_with(456)


class TestRoleSelectViewConfirm(unittest.TestCase):

    def test_confirm_actualiza_config_y_edita_principal(self):
        parent = Mock()
        parent.editar_principal = AsyncMock()
        parent.build_embed = Mock(return_value=Mock())
        parent.config = {"roles_asignar": []}
        view = cog.RoleSelectView(parent, "roles_asignar", [])
        view.role_select = Mock()
        view.role_select.values = [Mock(id=999), Mock(id=888)]

        interaction = Mock()
        interaction.response.send_message = AsyncMock()

        _await(view.confirm.callback(interaction))

        self.assertEqual(parent.config["roles_asignar"], [999, 888])
        parent.editar_principal.assert_awaited_once()
        interaction.response.send_message.assert_awaited_once_with(
            content="Roles actualizados.", ephemeral=True, delete_after=2,
        )

    def test_confirm_sin_editar_principal_no_falla(self):
        parent = Mock()
        parent.editar_principal = AsyncMock(side_effect=Exception("boom"))
        parent.build_embed = Mock(return_value=Mock())
        parent.config = {"roles_asignar": []}
        view = cog.RoleSelectView(parent, "roles_asignar", [])
        view.role_select = Mock()
        view.role_select.values = []

        interaction = Mock()
        interaction.response.send_message = AsyncMock()

        _await(view.confirm.callback(interaction))

        interaction.response.send_message.assert_awaited_once()


class TestAprobarConfigViewGuardar(unittest.TestCase):

    def test_save_guarda_y_edita_interaccion(self):
        view = cog.AprobarConfigView(
            {"roles_asignar": [1, 2], "roles_eliminar": [3]}, 0, 0, Mock()
        )
        view.config = {"roles_asignar": [1, 2], "roles_eliminar": [3]}

        interaction = Mock()
        interaction.response.edit_message = AsyncMock()
        interaction.user.mention = "@user"

        with patch.object(cog.config_manager, "save_aprobar_config") as save:
            _await(view.save.callback(interaction))

        save.assert_called_once_with(view.config)
        interaction.response.edit_message.assert_awaited_once()

    def test_cancel_desactiva_panel(self):
        view = cog.AprobarConfigView({"roles_asignar": [], "roles_eliminar": []}, 0, 0, Mock())
        interaction = Mock()
        interaction.response.edit_message = AsyncMock()

        _await(view.cancel.callback(interaction))

        interaction.response.edit_message.assert_awaited_once()


class TestConfigAprobarComando(unittest.TestCase):

    def test_comando_abre_panel_y_captura_message_id(self):
        interaction = Mock()
        interaction.guild = Mock()
        interaction.channel_id = 123
        interaction.client = Mock()
        interaction.response.send_message = AsyncMock()
        msg = Mock(id=789)
        interaction.original_response = AsyncMock(return_value=msg)

        with patch.object(cog.config_manager, "load_aprobar_config", return_value={"roles_asignar": [], "roles_eliminar": []}):
            _await(cog.config_aprobar.callback(interaction))

        interaction.response.send_message.assert_awaited_once()
        interaction.original_response.assert_awaited_once()

    def test_comando_sin_guild_no_abre_panel(self):
        interaction = Mock()
        interaction.guild = None
        interaction.response.send_message = AsyncMock()

        _await(cog.config_aprobar.callback(interaction))

        interaction.response.send_message.assert_awaited_once_with(
            "Este comando solo puede usarse en un servidor.", ephemeral=True
        )


if __name__ == "__main__":
    unittest.main()
