import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord

from cogs import entrevistas_cog as cog


def _sesion(
    user_id=100,
    staff_id=200,
    channel_id=300,
    guild_id=400,
    questions=None,
    current_index=0,
    answers=None,
    estado=cog.ESTADO_ACTIVA,
    session_id="abc123",
    started_at=None,
    last_updated=None,
):
    return cog.InterviewSession(
        user_id=user_id,
        staff_id=staff_id,
        channel_id=channel_id,
        guild_id=guild_id,
        questions=questions or [
            {"id": 1, "pregunta": "Pregunta de prueba", "categoria": "GENERAL", "respuesta_esperada": "Respuesta esperada"},
            {"id": 2, "pregunta": "Segunda pregunta", "categoria": "ARMERIA", "respuesta_esperada": ""},
        ],
        current_index=current_index,
        answers=answers or [],
        session_id=session_id,
        estado=estado,
        started_at=started_at or datetime.now(timezone.utc),
        last_updated=last_updated,
    )


class TestHumanizarTiempo(unittest.TestCase):
    def test_segundos(self):
        self.assertEqual(cog._humanizar_tiempo(5), "5s")

    def test_minutos(self):
        self.assertEqual(cog._humanizar_tiempo(65), "1m 5s")

    def test_horas(self):
        self.assertEqual(cog._humanizar_tiempo(3665), "1h 1m")


class TestBuildPanelEmbed(unittest.TestCase):
    def test_activa(self):
        s = _sesion(current_index=1)
        embed = cog._build_panel_embed(s, None, None)
        self.assertIn("Panel de seguimiento", embed.title)
        self.assertIn("Activa", embed.description)
        self.assertIn(cog.ESTADO_EMOJI[cog.ESTADO_ACTIVA], embed.description)

    def test_pregunta_actual(self):
        s = _sesion(current_index=0)
        embed = cog._build_panel_embed(s, None, None)
        values = [f.name for f in embed.fields]
        self.assertTrue(any("Pregunta actual" in v for v in values))
        pregunta_field = next(f for f in embed.fields if "Pregunta actual" in f.name)
        self.assertIn("Pregunta 1 de 2", pregunta_field.value)

    def test_respuestas_realizadas(self):
        s = _sesion(current_index=1, answers=["BIEN"])
        embed = cog._build_panel_embed(s, None, None)
        resp = next(f for f in embed.fields if "Respuestas realizadas" in f.name)
        self.assertEqual(resp.value, "1 / 2")

    def test_expirada_recuperable(self):
        s = _sesion(estado=cog.ESTADO_EXPIRADA)
        embed = cog._build_panel_embed(s, None, None)
        self.assertIn("Expirada (recuperable)", embed.description)
        self.assertIn("/recuperar_entrevista", embed.description + "\n".join(f.value for f in embed.fields))

    def test_finalizada(self):
        s = _sesion(estado=cog.ESTADO_FINALIZADA)
        embed = cog._build_panel_embed(s, None, None)
        self.assertIn("Finalizada", embed.description)

    def test_abandonada(self):
        s = _sesion(estado=cog.ESTADO_ABANDONADA)
        embed = cog._build_panel_embed(s, None, None)
        self.assertIn("Abandonada", embed.description)

    def test_ultima_actualizacion(self):
        now = datetime.now(timezone.utc)
        s = _sesion(last_updated=now - timedelta(seconds=30))
        embed = cog._build_panel_embed(s, None, None)
        upd = next(f for f in embed.fields if "Última actualización" in f.name)
        self.assertNotEqual(upd.value, "Desconocida")
        inac = next(f for f in embed.fields if "Tiempo desde última actividad" in f.name)
        self.assertEqual(inac.value, "30s")

    def test_contenido_pregunta(self):
        s = _sesion(current_index=0)
        embed = cog._build_panel_embed(s, None, None)
        contenido = next(f for f in embed.fields if f.name == "Contenido de la pregunta")
        self.assertIn("Pregunta de prueba", contenido.value)
        esperada = next(f for f in embed.fields if f.name == "Respuesta esperada")
        self.assertIn("Respuesta esperada", esperada.value)


class TestObtenerSnapshot(unittest.TestCase):
    def tearDown(self):
        cog.active_interviews.clear()

    def test_desde_memoria(self):
        s = _sesion()
        cog.active_interviews[s.user_id] = s
        client = Mock()
        result = cog._obtener_snapshot_sesion(s.user_id, client)
        self.assertIs(result, s)
        client = client

    @patch.object(cog.entrevistas_db, "recuperar_sesion_entrevista")
    def test_desde_db(self, mock_recuperar):
        mock_recuperar.return_value = {
            "user_id": "100",
            "staff_id": "200",
            "channel_id": "300",
            "guild_id": "400",
            "session_id": "xyz789",
            "questions": [{"id": 1, "pregunta": "P", "categoria": "GENERAL"}],
            "current_index": 0,
            "answers": [],
            "motives": {},
            "intento": 1,
            "started_at": datetime.now(timezone.utc),
            "estado": cog.ESTADO_ACTIVA,
            "updated_at": datetime.now(timezone.utc),
        }
        client = Mock()
        result = cog._obtener_snapshot_sesion(100, client)
        self.assertIsNotNone(result)
        self.assertEqual(result.session_id, "xyz789")
        mock_recuperar.assert_called_once_with("100")

    def test_desde_db_inexistente(self):
        client = Mock()
        with patch.object(cog.entrevistas_db, "recuperar_sesion_entrevista", return_value=None):
            result = cog._obtener_snapshot_sesion(999, client)
        self.assertIsNone(result)


class TestObtenerSnapshotCanal(unittest.TestCase):
    def tearDown(self):
        cog.active_interviews.clear()

    def test_desde_memoria(self):
        s = _sesion(channel_id=300)
        cog.active_interviews[s.user_id] = s
        client = Mock()
        result = cog._obtener_snapshot_sesion_canal(300, client)
        self.assertIs(result, s)

    @patch.object(cog.entrevistas_db, "listar_sesiones_por_canal")
    def test_desde_db(self, mock_listar):
        mock_listar.return_value = [{
            "user_id": "100",
            "staff_id": "200",
            "channel_id": "300",
            "guild_id": "400",
            "session_id": "db1",
            "questions": [],
            "current_index": 0,
            "answers": [],
            "motives": {},
            "intento": 1,
            "started_at": datetime.now(timezone.utc),
            "estado": cog.ESTADO_EXPIRADA,
        }]
        client = Mock()
        result = cog._obtener_snapshot_sesion_canal(300, client)
        self.assertIsNotNone(result)
        self.assertEqual(result.session_id, "db1")

    def test_inexistente(self):
        client = Mock()
        with patch.object(cog.entrevistas_db, "listar_sesiones_por_canal", return_value=[]):
            result = cog._obtener_snapshot_sesion_canal(999, client)
        self.assertIsNone(result)


class TestPanelSeguimiento(unittest.TestCase):
    def tearDown(self):
        cog.active_interviews.clear()
        cog.active_panels.clear()

    def _panel(self):
        channel = Mock()
        channel.id = 300
        client = Mock()
        client.get_channel.return_value = channel
        panel = cog.PanelSeguimiento(
            user_id=100,
            message_id=500,
            channel_id=300,
            client=client,
        )
        return panel, channel

    def _message_mock(self):
        msg = Mock()
        msg.edit = AsyncMock()
        return msg

    def test_refresca_y_edita(self):
        s = _sesion(current_index=0)
        cog.active_interviews[s.user_id] = s
        panel, channel = self._panel()
        msg = self._message_mock()
        channel.fetch_message = AsyncMock(return_value=msg)

        result = asyncio_run(panel._refrescar())

        self.assertIs(result, msg)
        msg.edit.assert_awaited_once()
        embed = msg.edit.await_args.kwargs["embed"]
        self.assertIsNotNone(embed)

    def test_se_detiene_en_estado_terminal(self):
        s = _sesion(estado=cog.ESTADO_FINALIZADA)
        cog.active_interviews[s.user_id] = s
        panel, channel = self._panel()
        msg = self._message_mock()
        channel.fetch_message = AsyncMock(return_value=msg)

        asyncio_run(panel._refrescar())

        self.assertTrue(panel._stop)
        self.assertNotIn(s.user_id, cog.active_panels)

    def test_mensaje_borrado_detiene_panel(self):
        s = _sesion()
        cog.active_interviews[s.user_id] = s
        panel, channel = self._panel()
        channel.fetch_message = AsyncMock(side_effect=discord.NotFound(Mock(), "x"))

        asyncio_run(panel._refrescar())

        self.assertTrue(panel._stop)
        self.assertNotIn(s.user_id, cog.active_panels)

    def test_sesion_inexistente_muestra_final(self):
        panel, channel = self._panel()
        msg = self._message_mock()
        channel.fetch_message = AsyncMock(return_value=msg)
        with patch.object(cog.entrevistas_db, "recuperar_sesion_entrevista", return_value=None):
            asyncio_run(panel._refrescar())

        self.assertTrue(panel._stop)
        msg.edit.assert_awaited_once()

    def test_actualizar_y_detener_con_sesion(self):
        s = _sesion(estado=cog.ESTADO_FINALIZADA)
        panel, channel = self._panel()
        msg = self._message_mock()
        channel.fetch_message = AsyncMock(return_value=msg)
        cog.active_panels[s.user_id] = panel

        asyncio_run(panel.actualizar_y_detener(s))

        self.assertTrue(panel._stop)
        self.assertNotIn(s.user_id, cog.active_panels)
        embed = msg.edit.await_args.kwargs["embed"]
        self.assertIn("Finalizada", embed.description)


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
