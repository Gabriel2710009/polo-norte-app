import os
import sys
import asyncio
import unittest
from unittest.mock import Mock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs import blacklist_cog


class TestCheckTicketBlacklist(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(cls.loop)

    @classmethod
    def tearDownClass(cls):
        cls.loop.close()

    def setUp(self):
        blacklist_cog._tickets_notificados.clear()
        self._cat_id_original = blacklist_cog.POSTULACIONES_CATEGORY_ID
        blacklist_cog.POSTULACIONES_CATEGORY_ID = 9999

        self.bot = Mock()
        self.bot.user.id = 999

        self.canal = Mock(spec=blacklist_cog.discord.TextChannel)
        self.canal.id = 5001
        self.canal.category_id = blacklist_cog.POSTULACIONES_CATEGORY_ID
        self.canal.mention = "<#5001>"
        self.canal.guild = Mock()
        self.canal.guild.id = 1

    def tearDown(self):
        blacklist_cog.POSTULACIONES_CATEGORY_ID = self._cat_id_original

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def test_notifica_si_creador_en_blacklist(self):
        with patch("cogs.blacklist_cog._identificar_creador", return_value=111):
            with patch.object(self.canal.guild, "get_member", return_value=Mock(id=111, mention="<@111>")):
                with patch("cogs.blacklist_cog._es_staff", return_value=False):
                    with patch("cogs.blacklist_cog.db.obtener", return_value={
                        "discord_id": "111", "nombre_ic": "Test",
                        "motivo": "Scam", "staff_id": "999", "fecha": "2026-01-01",
                    }):
                        with patch("cogs.blacklist_cog.db.registrar_intento"):
                            with patch("cogs.blacklist_cog._notificar_blacklist", new_callable=AsyncMock) as notify:
                                with patch("cogs.blacklist_cog.log_actions.log_info"):
                                    ok = self._run(
                                        blacklist_cog.check_ticket_blacklist(
                                            self.canal, self.bot, origen="test"
                                        )
                                    )
                                    self.assertTrue(ok)
                                    notify.assert_awaited_once()

    def test_omite_si_ya_notificado(self):
        blacklist_cog._tickets_notificados.add(5001)
        with patch("cogs.blacklist_cog._notificar_blacklist", new_callable=AsyncMock) as notify:
            ok = self._run(
                blacklist_cog.check_ticket_blacklist(self.canal, self.bot, origen="test")
            )
            self.assertFalse(ok)
            notify.assert_not_called()

    def test_omite_si_es_staff(self):
        with patch("cogs.blacklist_cog._identificar_creador", return_value=111):
            with patch.object(self.canal.guild, "get_member", return_value=Mock(id=111)):
                with patch("cogs.blacklist_cog._es_staff", return_value=True):
                    with patch("cogs.blacklist_cog._notificar_blacklist", new_callable=AsyncMock) as notify:
                        ok = self._run(
                            blacklist_cog.check_ticket_blacklist(
                                self.canal, self.bot, origen="test"
                            )
                        )
                        self.assertFalse(ok)
                        notify.assert_not_called()

    def test_no_notifica_sin_creador(self):
        with patch("cogs.blacklist_cog._identificar_creador", return_value=None):
            with patch.object(self.canal, "history") as mock_hist:
                mock_hist.return_value.__aiter__.return_value = iter([])
                with patch.object(self.canal.guild, "audit_logs") as mock_audit:
                    mock_audit.return_value.__aiter__.return_value = iter([])
                    with patch("cogs.blacklist_cog._notificar_blacklist", new_callable=AsyncMock) as notify:
                        ok = self._run(
                            blacklist_cog.check_ticket_blacklist(
                                self.canal, self.bot, origen="test"
                            )
                        )
                        self.assertFalse(ok)
                        notify.assert_not_called()


class TestLimpieza(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(cls.loop)

    @classmethod
    def tearDownClass(cls):
        cls.loop.close()

    def setUp(self):
        blacklist_cog._tickets_notificados.clear()

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def test_elimina_solo_canales_inexistentes(self):
        blacklist_cog._tickets_notificados.update([1, 2, 3])
        bot = Mock()
        bot.get_channel = Mock(side_effect=lambda cid: Mock(id=cid) if cid == 2 else None)

        with patch("cogs.blacklist_cog._persistir_notificados") as persist:
            self._run(blacklist_cog._limpiar_notificados_antiguos(bot))
            self.assertEqual(blacklist_cog._tickets_notificados, {2})
            persist.assert_called_once()

    def test_no_persiste_si_no_hay_cambios(self):
        blacklist_cog._tickets_notificados.update([1])
        bot = Mock()
        bot.get_channel = Mock(return_value=Mock(id=1))

        with patch("cogs.blacklist_cog._persistir_notificados") as persist:
            self._run(blacklist_cog._limpiar_notificados_antiguos(bot))
            self.assertEqual(blacklist_cog._tickets_notificados, {1})
            persist.assert_not_called()

    def test_set_vacio_no_hace_nada(self):
        bot = Mock()
        with patch("cogs.blacklist_cog._persistir_notificados") as persist:
            self._run(blacklist_cog._limpiar_notificados_antiguos(bot))
            persist.assert_not_called()


class TestPersistencia(unittest.TestCase):

    def setUp(self):
        blacklist_cog._tickets_notificados.clear()

    def tearDown(self):
        blacklist_cog._tickets_notificados.clear()

    def test_ciclo_completo(self):
        stored = []

        with patch("database.config_db.guardar_notificados") as guardar, \
             patch("database.config_db.cargar_notificados") as cargar:
            guardar.side_effect = lambda ids: stored.extend(ids)
            cargar.side_effect = lambda: set(stored)

            blacklist_cog._tickets_notificados.update([100, 200, 300])
            blacklist_cog._persistir_notificados()
            blacklist_cog._tickets_notificados.clear()
            blacklist_cog._cargar_notificados()
            self.assertEqual(blacklist_cog._tickets_notificados, {100, 200, 300})

    def test_sin_registro_no_borra_existente(self):
        with patch("database.config_db.cargar_notificados", return_value=set()):
            blacklist_cog._tickets_notificados.add(777)
            blacklist_cog._cargar_notificados()
            self.assertIn(777, blacklist_cog._tickets_notificados)

    def test_db_inaccesible_no_borra_existente(self):
        with patch(
            "database.config_db.cargar_notificados",
            side_effect=Exception("sin DB"),
        ):
            blacklist_cog._tickets_notificados.add(777)
            blacklist_cog._cargar_notificados()
            self.assertEqual(blacklist_cog._tickets_notificados, {777})


class TestScanOpenTickets(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(cls.loop)

    @classmethod
    def tearDownClass(cls):
        cls.loop.close()

    def setUp(self):
        blacklist_cog._tickets_notificados.clear()
        self._cat_id_original = blacklist_cog.POSTULACIONES_CATEGORY_ID
        blacklist_cog.POSTULACIONES_CATEGORY_ID = 9999

    def tearDown(self):
        blacklist_cog.POSTULACIONES_CATEGORY_ID = self._cat_id_original

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def test_ejecuta_limpieza_y_escaneo(self):
        bot = Mock()
        bot.wait_until_ready = AsyncMock()
        bot.guilds = []
        bot.get_channel = Mock(return_value=None)

        with patch.object(blacklist_cog, "_limpiar_notificados_antiguos", new_callable=AsyncMock) as clean:
            self._run(blacklist_cog.scan_open_tickets(bot))
            clean.assert_awaited_once()

    def test_early_return_si_categoria_no_configurada(self):
        blacklist_cog.POSTULACIONES_CATEGORY_ID = 0
        bot = Mock()
        bot.wait_until_ready = AsyncMock()
        bot.guilds = []

        with patch.object(blacklist_cog, "_limpiar_notificados_antiguos", new_callable=AsyncMock) as clean:
            self._run(blacklist_cog.scan_open_tickets(bot))
            clean.assert_awaited_once()

    def test_itera_guilds_y_canales(self):
        categoria = Mock()
        categoria.channels = []

        guild = Mock()
        guild.id = 2
        guild.get_channel = Mock(return_value=categoria)

        bot = Mock()
        bot.wait_until_ready = AsyncMock()
        bot.guilds = [guild]
        bot.get_channel = Mock(return_value=None)

        with patch.object(blacklist_cog, "_limpiar_notificados_antiguos", new_callable=AsyncMock):
            self._run(blacklist_cog.scan_open_tickets(bot))
            guild.get_channel.assert_called_with(blacklist_cog.POSTULACIONES_CATEGORY_ID)

    def test_itera_canales_y_llama_check(self):
        canal1 = Mock(spec=blacklist_cog.discord.TextChannel)
        canal1.id = 100
        canal1.category_id = 9999

        categoria = Mock()
        categoria.channels = [canal1]

        guild = Mock()
        guild.id = 2
        guild.get_channel = Mock(return_value=categoria)

        bot = Mock()
        bot.wait_until_ready = AsyncMock()
        bot.guilds = [guild]
        bot.get_channel = Mock(return_value=None)

        with patch.object(blacklist_cog, "_limpiar_notificados_antiguos", new_callable=AsyncMock):
            with patch.object(blacklist_cog, "check_ticket_blacklist", new_callable=AsyncMock) as check:
                self._run(blacklist_cog.scan_open_tickets(bot))
                check.assert_awaited_once_with(canal1, bot, origen="startup_scan")


if __name__ == "__main__":
    unittest.main(verbosity=2, failfast=False)
