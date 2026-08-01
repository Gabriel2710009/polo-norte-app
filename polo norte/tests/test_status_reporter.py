import os
import sys
import time
import asyncio
import unittest
from unittest.mock import Mock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from services import status_reporter


def _reset():
    status_reporter._bot = None
    status_reporter._admin_user_id = status_reporter.ADMIN_USER_ID
    status_reporter._start_time = time.time()
    status_reporter._last_heartbeat = None
    status_reporter._error_count = 0
    status_reporter._warn_count = 0
    status_reporter._disconnect_count = 0
    status_reporter._hubo_error_critico = False
    status_reporter._recent_errors = []
    status_reporter._startup_sent = False
    status_reporter._loop_started = True


def _bot_mock(comandos=0, ws=True):
    bot = Mock()
    bot.tree.get_commands.return_value = [Mock() for _ in range(comandos)]
    bot.is_ws_connected.return_value = ws
    return bot


class TestReportError(unittest.TestCase):

    def setUp(self):
        _reset()

    def test_incrementa_contador(self):
        status_reporter.report_error(Exception("boom"))
        self.assertEqual(status_reporter._error_count, 1)
        status_reporter.report_error(Exception("boom2"), contexto="Comando X")
        self.assertEqual(status_reporter._error_count, 2)

    def test_guarda_resumen_con_contexto(self):
        status_reporter.report_error(Exception("Database timeout"), contexto="EntrevistasDB")
        ultimo = status_reporter._recent_errors[-1]
        self.assertIn("EntrevistasDB", ultimo)
        self.assertIn("Database timeout", ultimo)

    def test_maximo_10_errores_recientes(self):
        for i in range(15):
            status_reporter.report_error(Exception(f"e{i}"))
        self.assertEqual(len(status_reporter._recent_errors), 10)
        self.assertNotIn("e0", status_reporter._recent_errors)

    def test_error_normal_no_avisa_inmediato(self):
        with patch.object(status_reporter, "_programar_aviso_critico") as mock_aviso:
            status_reporter.report_error(Exception("normal"), contexto="cmd")
        mock_aviso.assert_not_called()
        self.assertFalse(status_reporter._hubo_error_critico)

    def test_error_critico_avisa_inmediato(self):
        with patch.object(status_reporter, "_programar_aviso_critico") as mock_aviso:
            status_reporter.report_error(Exception("grave"), contexto="DB", es_critico=True)
        mock_aviso.assert_called_once()
        self.assertTrue(status_reporter._hubo_error_critico)

    def test_operationalerror_es_critico_auto(self):
        with patch.object(status_reporter, "_programar_aviso_critico") as mock_aviso:
            status_reporter.report_error(db.OperationalError("timeout"), contexto="DB")
        self.assertTrue(status_reporter._hubo_error_critico)
        mock_aviso.assert_called_once()

    def test_warning_incrementa_contador(self):
        status_reporter.report_warning("algo raro", contexto="fichaje")
        self.assertEqual(status_reporter._warn_count, 1)


class TestMensajeInicio(unittest.TestCase):

    def setUp(self):
        _reset()

    def test_envia_mensaje_de_inicio_con_nombre(self):
        status_reporter._bot = _bot_mock(comandos=18)
        user = Mock()
        user.display_name = "Gabi"
        user.name = "gabriel"
        with patch.object(status_reporter, "_check_db", new=AsyncMock(return_value=True)), \
             patch.object(status_reporter, "_get_admin_user", new=AsyncMock(return_value=user)), \
             patch.object(status_reporter, "_send_dm", new=AsyncMock(return_value=True)) as mock_dm:
            asyncio.run(status_reporter.send_startup_message())
        mock_dm.assert_awaited_once()
        embed = mock_dm.await_args.kwargs["embed"]
        self.assertIn("Gabi", embed.title)
        self.assertEqual(embed.color.value, 0x2ECC71)
        self.assertTrue(any("18 comandos" in f.value for f in embed.fields))

    def test_db_rota_marca_error_en_embed(self):
        status_reporter._bot = _bot_mock(comandos=3)
        with patch.object(status_reporter, "_check_db", new=AsyncMock(return_value=False)), \
             patch.object(status_reporter, "_get_admin_user", new=AsyncMock(return_value=None)), \
             patch.object(status_reporter, "_send_dm", new=AsyncMock(return_value=True)) as mock_dm:
            asyncio.run(status_reporter.send_startup_message())
        embed = mock_dm.await_args.kwargs["embed"]
        self.assertEqual(embed.color.value, 0xFEE75C)
        self.assertTrue(any("ERROR" in f.value for f in embed.fields))

    def test_no_rompe_si_falla_envio(self):
        status_reporter._bot = _bot_mock(comandos=0)
        with patch.object(status_reporter, "_check_db", new=AsyncMock(return_value=True)), \
             patch.object(status_reporter, "_get_admin_user", new=AsyncMock(return_value=Mock(display_name="Gabi"))), \
             patch.object(status_reporter, "_send_dm", new=AsyncMock(side_effect=Exception("DM falló"))):
            asyncio.run(status_reporter.send_startup_message())
        self.assertTrue(status_reporter._startup_sent)

    def test_no_envia_dos_veces(self):
        status_reporter._bot = _bot_mock(comandos=0)
        with patch.object(status_reporter, "_check_db", new=AsyncMock(return_value=True)), \
             patch.object(status_reporter, "_get_admin_user", new=AsyncMock(return_value=None)), \
             patch.object(status_reporter, "_send_dm", new=AsyncMock(return_value=True)) as mock_dm:
            asyncio.run(status_reporter.send_startup_message())
            asyncio.run(status_reporter.send_startup_message())
        self.assertEqual(mock_dm.await_count, 1)


class TestReportePeriodico(unittest.TestCase):

    def setUp(self):
        _reset()
        status_reporter._start_time = time.time() - (14 * 3600 + 32 * 60)

    def _enviar(self, **kwargs):
        status_reporter._bot = _bot_mock(comandos=18)
        with patch.object(status_reporter, "_check_db", new=AsyncMock(return_value=True)), \
             patch.object(status_reporter, "_send_dm", new=AsyncMock(return_value=True)) as mock_dm, \
             kwargs.get("patch") or patch.object(status_reporter, "_programar_aviso_critico"):
            asyncio.run(status_reporter.send_status_report())
        return mock_dm.await_args.kwargs["embed"]

    def test_sin_errores_verde(self):
        embed = self._enviar()
        self.assertEqual(embed.color.value, 0x2ECC71)
        self.assertIn("Todo parece estar funcionando correctamente", embed.description)
        valores = " ".join(f.value for f in embed.fields)
        self.assertIn("14 horas", valores)
        self.assertIn("18", valores)

    def test_con_errores_amarillo(self):
        status_reporter.report_error(Exception("Database timeout"), contexto="EntrevistasDB")
        embed = self._enviar()
        self.assertEqual(embed.color.value, 0xFEE75C)
        self.assertIn("Se detectaron problemas", embed.description)
        self.assertIn("Errores: 1", " ".join(f.value for f in embed.fields))
        self.assertIn("Database timeout", " ".join(f.value for f in embed.fields))

    def test_con_error_critico_rojo(self):
        with patch.object(status_reporter, "_programar_aviso_critico"):
            status_reporter.report_error(Exception("grave"), contexto="DB", es_critico=True)
        embed = self._enviar()
        self.assertEqual(embed.color.value, 0xE74C3C)

    def test_contadores_se_reinician_tras_reporte(self):
        status_reporter.report_error(Exception("a"))
        status_reporter.report_error(Exception("b"))
        status_reporter.report_warning("w")
        self._enviar()
        self.assertEqual(status_reporter._error_count, 0)
        self.assertEqual(status_reporter._warn_count, 0)
        self.assertFalse(status_reporter._hubo_error_critico)

    def test_desconexiones_se_reporte_en_embed(self):
        status_reporter.register_disconnect()
        status_reporter.register_disconnect()
        embed = self._enviar()
        self.assertEqual(embed.color.value, 0xFEE75C)
        self.assertIn("Desconexiones gateway: 2", " ".join(f.value for f in embed.fields))


class TestLoopPeriodico(unittest.TestCase):

    def setUp(self):
        _reset()

    def test_loop_envia_reporte_al_cumplirse_intervalo(self):
        llamadas_sleep = {"n": 0}

        async def sleep_mock(delay):
            llamadas_sleep["n"] += 1
            if llamadas_sleep["n"] >= 2:
                raise asyncio.CancelledError

        with patch.object(status_reporter, "send_status_report", new=AsyncMock(return_value=True)) as mock_envio, \
             patch("asyncio.sleep", new=sleep_mock):
            async def run():
                task = asyncio.create_task(status_reporter._periodic_loop())
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            asyncio.run(run())
        self.assertEqual(mock_envio.await_count, 1)

    def test_setup_inicia_loop(self):
        status_reporter._loop_started = False

        async def _dummy_loop():
            await asyncio.sleep(0)

        with patch.object(status_reporter, "_periodic_loop", side_effect=_dummy_loop) as mock_loop:
            loop = asyncio.new_event_loop()
            bot = Mock()
            bot.loop = loop
            with patch.object(loop, "create_task", wraps=loop.create_task) as mock_task:
                status_reporter.setup(bot)
                loop.run_until_complete(asyncio.sleep(0))
                loop.run_until_complete(asyncio.sleep(0))
                loop.close()
            mock_loop.assert_called_once()
            self.assertEqual(status_reporter._report_interval, 30 * 60)
            self.assertEqual(status_reporter._admin_user_id, status_reporter.ADMIN_USER_ID)
            self.assertTrue(mock_task.called)

    def test_estado_guardado(self):
        status_reporter._start_time = time.time() - 60
        status_reporter.report_error(Exception("x"))
        status_reporter.register_disconnect()
        status_reporter._last_heartbeat = time.time()
        self.assertIsNotNone(status_reporter._start_time)
        self.assertEqual(status_reporter._error_count, 1)
        self.assertEqual(status_reporter._disconnect_count, 1)
        self.assertEqual(len(status_reporter._recent_errors), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2, failfast=False)
