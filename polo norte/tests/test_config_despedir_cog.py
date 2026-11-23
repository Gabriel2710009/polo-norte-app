import sys
import os
import asyncio
import unittest
from unittest.mock import Mock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs import config_despedir_cog as cog


def _await(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _config():
    return {
        "rol_verificado": 1,
        "rol_despedido": 2,
        "rol_autorizado": 9,
        "categorias_busqueda": [3, 4, 5],
        "categoria_destino": 6,
        "canal_despedido": 7,
        "canal_logs": 0,
        "plantilla_aviso": "{usuario} Despedido por {motivo}",
        "limite_primeros_mensajes": 10,
        "usar_audit_log": True,
        "timeout_confirmacion": 120,
        "plantilla_despedir": "{usuario} Quedas despedido por {motivo} en {dias} días.",
        "dias": 3,
    }


def _view(panel="expulsar", config=None):
    config = config if config is not None else _config()
    mensaje = AsyncMock()
    channel = Mock()
    channel.fetch_message = AsyncMock(return_value=mensaje)
    client = Mock()
    client.get_channel = Mock(return_value=channel)
    view = cog.ConfigDespedirView(config, panel, 123, 456, client)
    return view, client, channel, mensaje


def _interaction():
    interaction = Mock()
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.user.id = 42
    interaction.user.mention = "@staff"
    return interaction


class TestBuildEmbed(unittest.TestCase):
    def test_expulsar_muestra_todas_las_variables(self):
        view, _, _, _ = _view()
        embed = view.build_embed()
        campos = {f.name: f.value for f in embed.fields}
        self.assertIn("<@&1>", campos["Rol verificado"])
        self.assertIn("<@&2>", campos["Rol despedido"])
        self.assertIn("<@&9>", campos["Rol autorizado"])
        self.assertIn("<#3>", campos["Categorías de búsqueda"])
        self.assertIn("<#6>", campos["Categoría destino"])
        self.assertIn("<#7>", campos["Canal despedido"])
        self.assertIn(".env", campos["Canal de logs"])
        self.assertIn("{motivo}", campos["Plantilla del aviso"])
        self.assertEqual(campos["Primeros mensajes a revisar"], "10")
        self.assertEqual(campos["Usar audit log"], "Sí")
        self.assertEqual(campos["Timeout de confirmación"], "120s")

    def test_despedir_muestra_plantilla_y_dias(self):
        view, _, _, _ = _view(panel="despedir")
        campos = {f.name: f.value for f in view.build_embed().fields}
        self.assertEqual(campos["Días del plazo"], "3")
        self.assertIn("{usuario}", campos["Plantilla de /despedir"])

    def test_editar_principal(self):
        view, _, channel, mensaje = _view()
        _await(view.editar_principal())
        channel.fetch_message.assert_awaited_once_with(456)
        mensaje.edit.assert_awaited_once()


class TestSelects(unittest.TestCase):
    def test_role_select_single(self):
        parent = Mock()
        parent.config = {"rol_despedido": 0}
        parent.refrescar = AsyncMock()
        vista = cog.RoleSelectView(parent, "rol_despedido")
        vista.select = Mock(values=[Mock(id=777)])
        interaction = _interaction()

        _await(vista.confirm.callback(interaction))

        self.assertEqual(parent.config["rol_despedido"], 777)
        parent.refrescar.assert_awaited_once()

    def test_role_select_sin_seleccion_deja_cero(self):
        parent = Mock()
        parent.config = {"rol_despedido": 5}
        parent.refrescar = AsyncMock()
        vista = cog.RoleSelectView(parent, "rol_despedido")
        vista.select = Mock(values=[])

        _await(vista.confirm.callback(_interaction()))

        self.assertEqual(parent.config["rol_despedido"], 0)

    def test_channel_select_multiple(self):
        parent = Mock()
        parent.config = {"categorias_busqueda": []}
        parent.refrescar = AsyncMock()
        vista = cog.ChannelSelectView(parent, "categorias_busqueda", [], single=False)
        vista.select = Mock(values=[Mock(id=11), Mock(id=22)])
        interaction = _interaction()

        _await(vista.confirm.callback(interaction))

        self.assertEqual(parent.config["categorias_busqueda"], [11, 22])


class TestValueModal(unittest.TestCase):
    def test_texto_guarda_strip(self):
        parent = Mock()
        parent.config = {}
        parent.refrescar = AsyncMock()
        modal = cog.ValueModal(parent, "plantilla_aviso", "t", "l", "viejo")
        modal.campo = Mock(value="  nuevo texto  ")

        _await(modal.on_submit(_interaction()))

        self.assertEqual(parent.config["plantilla_aviso"], "nuevo texto")
        parent.refrescar.assert_awaited_once()

    def test_entero_valido(self):
        parent = Mock()
        parent.config = {}
        parent.refrescar = AsyncMock()
        modal = cog.ValueModal(parent, "dias", "t", "l", 3, integer=True)
        modal.campo = Mock(value="7")

        _await(modal.on_submit(_interaction()))

        self.assertEqual(parent.config["dias"], 7)

    def test_entero_invalido_no_guarda(self):
        parent = Mock()
        parent.config = {}
        parent.refrescar = AsyncMock()
        modal = cog.ValueModal(parent, "dias", "t", "l", 3, integer=True)
        modal.campo = Mock(value="abc")
        interaction = _interaction()

        _await(modal.on_submit(interaction))

        self.assertNotIn("dias", parent.config)
        parent.refrescar.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()


class TestAccionesPanel(unittest.TestCase):
    def test_toggle_audit_log(self):
        view, _, _, _ = _view()
        interaction = _interaction()
        _await(view._toggle_audit_log(interaction))
        self.assertFalse(view.config["usar_audit_log"])

    def test_guardar_guarda_config_y_cierra_panel(self):
        view, _, _, _ = _view()
        interaction = _interaction()
        with patch.object(cog.config_manager, "save_despedir_config") as guardar:
            _await(view._guardar(interaction))
        guardar.assert_called_once_with(view.config, actualizado_por="42")
        interaction.response.edit_message.assert_awaited_once()

    def test_cancelar_cierra_panel(self):
        view, _, _, _ = _view()
        interaction = _interaction()
        _await(view._cancelar(interaction))
        interaction.response.edit_message.assert_awaited_once()


class TestComandos(unittest.TestCase):
    def test_sin_guild_no_abre_panel(self):
        interaction = _interaction()
        interaction.guild = None
        _await(cog.config_expulsar.callback(interaction))
        interaction.response.send_message.assert_awaited_once()

    def test_sin_permiso_no_abre_panel(self):
        interaction = _interaction()
        interaction.guild = Mock()
        with patch.object(cog, "_tiene_permiso", return_value=False):
            _await(cog.config_expulsar.callback(interaction))
        interaction.response.send_message.assert_awaited_once()

    def test_abre_panel_y_captura_message_id(self):
        interaction = _interaction()
        interaction.guild = Mock()
        interaction.channel_id = 123
        interaction.client = Mock()
        interaction.original_response = AsyncMock(return_value=Mock(id=789))
        with patch.object(cog, "_tiene_permiso", return_value=True), \
             patch.object(cog.config_manager, "load_despedir_config", return_value=_config()):
            _await(cog.config_despedir.callback(interaction))
        interaction.response.send_message.assert_awaited_once()
        interaction.original_response.assert_awaited_once()

    def test_setup_registra_dos_comandos(self):
        bot = Mock()
        bot.tree.add_command = Mock()
        _await(cog.setup(bot))
        self.assertEqual(bot.tree.add_command.call_count, 2)


if __name__ == "__main__":
    unittest.main()
