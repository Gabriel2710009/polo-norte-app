import sys
import os
import asyncio
import unittest
from unittest.mock import Mock, MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord

from cogs import despedir_cog as cog


def _await(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class AsyncIter:
    def __init__(self, items):
        self.items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)


def _config():
    return {
        "rol_verificado": 1,
        "rol_despedido": 2,
        "rol_autorizado": 9,
        "categorias_busqueda": [1307135109132189766],
        "categoria_destino": 1345688584249933926,
        "canal_despedido": 1343860496993878057,
        "canal_logs": 0,
        "plantilla_aviso": "{usuario} Despedido por {motivo}",
        "limite_primeros_mensajes": 10,
        "usar_audit_log": True,
        "timeout_confirmacion": 120,
        "plantilla_despedir": "{usuario} Quedas despedido por {motivo} en {dias} días.",
        "dias": 3,
    }


def _rol(rid, nombre, posicion, default=False):
    rol = MagicMock(spec=discord.Role)
    rol.id = rid
    rol.name = nombre
    rol.mention = f"<@&{rid}>"
    rol.is_default.return_value = default
    rol.__ge__.return_value = posicion >= 100
    return rol


def _interaction():
    interaction = Mock()
    interaction.guild = Mock()
    interaction.user = Mock(spec=discord.Member)
    interaction.user.id = 42
    interaction.user.mention = "@staff"
    interaction.user.roles = []
    interaction.user.guild_permissions.administrator = True
    interaction.user.guild_permissions.manage_roles = True
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    interaction.client = Mock()
    return interaction


class TestHelpers(unittest.TestCase):
    def test_formatear_placeholders(self):
        self.assertEqual(
            cog._formatear("Hola {usuario} por {motivo}", usuario="@u", motivo="x"),
            "Hola @u por x",
        )

    def test_formatear_placeholder_desconocido_no_rompe(self):
        self.assertEqual(cog._formatear("Hola {otro}", usuario="@u"), "Hola {otro}")

    def test_resolver_canal_acepta_mencion_e_id(self):
        guild = Mock()
        canal = Mock(id=123456789012345678)
        guild.get_channel.return_value = canal
        for texto in ("<#123456789012345678>", "123456789012345678"):
            resultado, error = cog._resolver_canal(guild, texto)
            self.assertIsNone(error)
            self.assertIs(resultado, canal)

    def test_resolver_canal_rechaza_texto_y_canal_inexistente(self):
        guild = Mock()
        guild.get_channel.return_value = None
        self.assertIsNotNone(cog._resolver_canal(guild, "hola")[1])
        self.assertIsNotNone(cog._resolver_canal(guild, "999999999999999999")[1])


class TestDeteccion(unittest.TestCase):
    def test_dueno_por_overwrites_ignora_roles(self):
        member = Mock(spec=discord.Member)
        member.id = 111
        permisos = Mock(read_messages=True, send_messages=True)
        canal = Mock()
        canal.overwrites = {Mock(spec=discord.Role): permisos, member: permisos}
        self.assertTrue(cog._es_dueno_por_overwrites(canal, member))

    def test_no_dueno_sin_permisos_de_lectura(self):
        member = Mock(spec=discord.Member)
        member.id = 111
        canal = Mock()
        canal.overwrites = {member: Mock(read_messages=False, send_messages=True)}
        self.assertFalse(cog._es_dueno_por_overwrites(canal, member))

    def test_primeros_mensajes(self):
        member = Mock(spec=discord.Member)
        member.id = 111
        canal = Mock()
        canal.history = Mock(return_value=AsyncIter([
            Mock(author=Mock(id=999)),
            Mock(author=Mock(id=111)),
        ]))
        self.assertTrue(_await(cog._participa_en_primeros_mensajes(canal, member, 10)))

    def test_audit_log(self):
        guild = Mock()
        guild.audit_logs = Mock(return_value=AsyncIter([
            Mock(target=Mock(id=55), user=Mock(id=111)),
        ]))
        canal = Mock(id=55)
        member = Mock(spec=discord.Member)
        member.id = 111
        self.assertTrue(_await(cog._creador_por_audit_log(guild, canal, member)))

    def test_detecta_fuertes_y_debiles(self):
        member = Mock(spec=discord.Member)
        member.id = 111
        fuerte = Mock(spec=discord.TextChannel)
        debil = Mock(spec=discord.TextChannel)
        debil.permissions_for.return_value = Mock(read_messages=True, send_messages=True)
        categoria = Mock(spec=discord.CategoryChannel)
        categoria.channels = [fuerte, debil]
        guild = Mock()
        guild.get_channel.return_value = categoria

        with patch.object(cog, "_es_dueno_por_overwrites", side_effect=[True, False]), \
             patch.object(cog, "_participa_en_primeros_mensajes", new=AsyncMock(return_value=False)), \
             patch.object(cog, "_creador_por_audit_log", new=AsyncMock(return_value=False)):
            candidatos = _await(cog._detectar_candidatos(Mock(), guild, member, _config()))

        self.assertEqual(len(candidatos), 2)
        self.assertEqual(candidatos[0][0], fuerte)
        self.assertEqual(candidatos[1][1], "tiene acceso al canal")


class TestAcciones(unittest.TestCase):
    def test_mover_canal(self):
        canal = Mock(spec=discord.TextChannel)
        canal.category_id = 1
        categoria = Mock(spec=discord.CategoryChannel)
        categoria.id = 1345688584249933926
        canal.guild.get_channel.return_value = categoria
        movio, error = _await(cog._mover_canal(canal, categoria.id, "avisos"))
        self.assertTrue(movio)
        self.assertIsNone(error)
        canal.edit.assert_awaited_once_with(
            category=categoria, reason="Expulsión: avisos"
        )

    def test_mover_canal_ya_esta_en_destino(self):
        canal = Mock(spec=discord.TextChannel)
        categoria = Mock(spec=discord.CategoryChannel)
        categoria.id = 5
        canal.category_id = 5
        canal.guild.get_channel.return_value = categoria
        movio, error = _await(cog._mover_canal(canal, 5, "x"))
        self.assertTrue(movio)
        canal.edit.assert_not_awaited()

    def test_reemplazar_roles_respeta_jerarquia(self):
        default = _rol(0, "@everyone", 0, default=True)
        verificado = _rol(1, "verificado", 50)
        despedido = _rol(2, "despedido", 50)
        intocable = _rol(3, "admin", 150)
        removable = _rol(4, "worker", 10)

        guild = MagicMock()
        guild.me.guild_permissions.manage_roles = True
        guild.me.top_role = _rol(99, "bot", 100)
        guild.get_role.side_effect = lambda rid: {1: verificado, 2: despedido}.get(rid)

        member = MagicMock(spec=discord.Member)
        member.guild = guild
        member.roles = [default, intocable, removable]
        member.edit = AsyncMock()

        resultado = _await(cog._reemplazar_roles(member, _config(), "avisos"))

        self.assertTrue(resultado["ok"])
        self.assertIn(removable, resultado["eliminados"])
        self.assertIn(intocable, resultado["inamovibles"])
        member.edit.assert_awaited_once()
        roles_finales = member.edit.call_args.kwargs["roles"]
        self.assertEqual(roles_finales, [despedido, verificado, intocable])

    def test_reemplazar_roles_sin_permisos(self):
        guild = MagicMock()
        guild.me.guild_permissions.manage_roles = False
        member = MagicMock(spec=discord.Member)
        member.guild = guild
        resultado = _await(cog._reemplazar_roles(member, _config(), "x"))
        self.assertFalse(resultado["ok"])
        self.assertTrue(resultado["errores"])


class TestComandos(unittest.TestCase):
    def test_despedir_envia_texto_formateado_en_canal_actual(self):
        interaction = _interaction()
        usuario = Mock(spec=discord.Member)
        usuario.id = 7
        usuario.mention = "@u"
        canal = Mock(spec=discord.TextChannel)
        canal.mention = "#actual"
        canal.send = AsyncMock()
        canal.permissions_for.return_value = Mock(send_messages=True)
        interaction.channel = canal

        with patch.object(cog.config_manager, "load_despedir_config", return_value=_config()), \
             patch.object(cog, "_enviar_auditoria", new=AsyncMock()):
            _await(cog.despedir.callback(interaction, usuario, "avisos"))

        canal.send.assert_awaited_once()
        texto = canal.send.call_args.args[0]
        self.assertIn("@u", texto)
        self.assertIn("avisos", texto)
        self.assertIn("3 días", texto)
        interaction.edit_original_response.assert_awaited_once()

    def test_despedir_sin_guild(self):
        interaction = _interaction()
        interaction.guild = None
        _await(cog.despedir.callback(interaction, Mock(), "x"))
        interaction.response.send_message.assert_awaited_once()

    def test_despedir_sin_permiso(self):
        interaction = _interaction()
        interaction.user.guild_permissions.administrator = False
        interaction.user.guild_permissions.manage_roles = False
        with patch.object(cog.config_manager, "load_despedir_config", return_value=_config()):
            _await(cog.despedir.callback(interaction, Mock(), "x"))
        interaction.response.send_message.assert_awaited_once()

    def test_expulsar_muestra_confirmacion_cuando_hay_candidatos(self):
        interaction = _interaction()
        usuario = Mock(spec=discord.Member)
        usuario.id = 7
        usuario.mention = "@u"
        usuario.bot = False
        canal = Mock(spec=discord.TextChannel)
        with patch.object(cog.config_manager, "load_despedir_config", return_value=_config()), \
             patch.object(cog, "_detectar_candidatos", new=AsyncMock(return_value=[(canal, "razón")])):
            _await(cog.expulsar.callback(interaction, usuario, "avisos"))
        kwargs = interaction.edit_original_response.call_args.kwargs
        self.assertIsInstance(kwargs["view"], cog.ConfirmarView)

    def test_expulsar_pide_canal_cuando_no_hay_candidatos(self):
        interaction = _interaction()
        usuario = Mock(spec=discord.Member)
        usuario.id = 7
        usuario.mention = "@u"
        usuario.bot = False
        with patch.object(cog.config_manager, "load_despedir_config", return_value=_config()), \
             patch.object(cog, "_detectar_candidatos", new=AsyncMock(return_value=[])):
            _await(cog.expulsar.callback(interaction, usuario, "avisos"))
        kwargs = interaction.edit_original_response.call_args.kwargs
        self.assertIsInstance(kwargs["view"], cog.CanalManualView)

    def test_expulsar_rechaza_bot(self):
        interaction = _interaction()
        usuario = Mock(spec=discord.Member)
        usuario.bot = True
        with patch.object(cog.config_manager, "load_despedir_config", return_value=_config()):
            _await(cog.expulsar.callback(interaction, usuario, "x"))
        interaction.response.send_message.assert_awaited_once()


class TestVistas(unittest.TestCase):
    def _estado(self, candidatos):
        usuario = Mock(spec=discord.Member)
        usuario.mention = "@u"
        return cog.EstadoExpulsion(
            staff_id=42, usuario=usuario, motivo="avisos",
            config=_config(), candidatos=candidatos,
        )

    def test_saltar_avanza_y_luego_pide_canal(self):
        c1, c2 = Mock(), Mock()
        estado = self._estado([(c1, "a"), (c2, "b")])
        vista = cog.ConfirmarView(estado, Mock())
        interaction = _interaction()
        interaction.response.edit_message = AsyncMock()

        _await(vista.saltar.callback(interaction))
        self.assertEqual(estado.indice, 1)
        _await(vista.saltar.callback(interaction))
        kwargs = interaction.response.edit_message.call_args.kwargs
        self.assertIsInstance(kwargs["view"], cog.CanalManualView)

    def test_interaction_check_solo_staff(self):
        estado = self._estado([])
        vista = cog.ConfirmarView(estado, Mock())
        interaction = _interaction()
        interaction.user.id = 999
        self.assertFalse(_await(vista.interaction_check(interaction)))
        interaction.response.send_message.assert_awaited_once()

    def test_procesar_confirmado_orquesta_acciones(self):
        canal = Mock(spec=discord.TextChannel)
        canal.mention = "#canal"
        canal_aviso = Mock()
        canal_aviso.send = AsyncMock()
        bot = Mock()
        bot.get_channel.return_value = canal_aviso

        estado = self._estado([(canal, "a")])
        interaction = _interaction()
        interaction.client = bot
        vista = Mock()
        vista.children = []

        roles = {
            "ok": True, "eliminados": [], "asignados": [],
            "inamovibles": [], "errores": [],
        }
        with patch.object(cog, "_mover_canal", new=AsyncMock(return_value=(True, None))), \
             patch.object(cog, "_reemplazar_roles", new=AsyncMock(return_value=roles)), \
             patch.object(cog, "_enviar_auditoria", new=AsyncMock()):
            _await(cog._procesar_confirmado(interaction, estado, canal, vista))

        canal_aviso.send.assert_awaited_once()
        texto = canal_aviso.send.call_args.args[0]
        self.assertIn("@u", texto)
        self.assertIn("avisos", texto)
        self.assertTrue(estado.procesado)
        interaction.edit_original_response.assert_awaited()

    def test_procesar_confirmado_no_repite(self):
        estado = self._estado([])
        estado.procesado = True
        interaction = _interaction()
        _await(cog._procesar_confirmado(interaction, estado, Mock(), Mock()))
        interaction.response.send_message.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
