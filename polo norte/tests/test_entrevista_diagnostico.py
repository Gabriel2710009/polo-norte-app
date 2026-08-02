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
    processing=False,
    modal_open=False,
    modal_opened_at=None,
    modal_tipo=None,
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
        processing=processing,
        modal_open=modal_open,
        modal_opened_at=modal_opened_at,
        modal_tipo=modal_tipo,
    )


class TestSessionSerializacion(unittest.TestCase):
    def test_roundtrip_persistir_nuevos_campos(self):
        opened = datetime.now(timezone.utc) - timedelta(seconds=120)
        s = _sesion(
            processing=True,
            modal_open=True,
            modal_opened_at=opened,
            modal_tipo="REGULAR",
        )
        datos = cog._session_a_datos(s)
        self.assertTrue(datos["processing"])
        self.assertTrue(datos["modal_open"])
        self.assertEqual(datos["modal_tipo"], "REGULAR")
        self.assertEqual(datos["modal_opened_at"], opened)

        reconstruida = cog._datos_a_session(datos)
        self.assertTrue(reconstruida.processing)
        self.assertTrue(reconstruida.modal_open)
        self.assertEqual(reconstruida.modal_tipo, "REGULAR")
        self.assertEqual(reconstruida.modal_opened_at, opened)

    def test_modal_opened_at_str_desde_db(self):
        opened = datetime.now(timezone.utc) - timedelta(seconds=60)
        datos = {
            "user_id": "100",
            "staff_id": "200",
            "questions": [],
            "estado": cog.ESTADO_ACTIVA,
            "modal_open": True,
            "modal_opened_at": opened.isoformat(),
            "modal_tipo": "MAL",
            "processing": True,
        }
        s = cog._datos_a_session(datos)
        self.assertTrue(s.modal_open)
        self.assertEqual(s.modal_tipo, "MAL")
        self.assertIsNotNone(s.modal_opened_at)


class TestModalAbandonado(unittest.TestCase):
    def tearDown(self):
        cog.active_interviews.clear()

    def test_sin_modal_no_se_considera_abandonado(self):
        s = _sesion()
        self.assertFalse(cog._modal_abandonado(s))

    def test_modal_recien_abierto_no_es_abandonado(self):
        s = _sesion(
            modal_open=True,
            modal_opened_at=datetime.now(timezone.utc) - timedelta(seconds=30),
            modal_tipo="REGULAR",
        )
        self.assertFalse(cog._modal_abandonado(s))

    def test_modal_viejo_es_abandonado(self):
        s = _sesion(
            modal_open=True,
            modal_opened_at=datetime.now(timezone.utc) - timedelta(seconds=cog.MODAL_TIMEOUT + 10),
            modal_tipo="MAL",
        )
        self.assertTrue(cog._modal_abandonado(s))

    def test_modal_opened_at_naive(self):
        s = _sesion(
            modal_open=True,
            modal_opened_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=cog.MODAL_TIMEOUT + 10),
        )
        self.assertTrue(cog._modal_abandonado(s))


class TestDesbloquearModal(unittest.TestCase):
    def setUp(self):
        self.s = _sesion(
            processing=True,
            modal_open=True,
            modal_opened_at=datetime.now(timezone.utc) - timedelta(seconds=600),
            modal_tipo="REGULAR",
        )
        cog.active_interviews[self.s.user_id] = self.s

    def tearDown(self):
        cog.active_interviews.clear()

    def test_resetea_todos_los_flags(self):
        with patch.object(cog, "_persistir_sesion") as mock_persistir:
            cog._desbloquear_modal(self.s, "test")
        self.assertFalse(self.s.processing)
        self.assertFalse(self.s.modal_open)
        self.assertIsNone(self.s.modal_opened_at)
        self.assertIsNone(self.s.modal_tipo)
        self.assertFalse(self.s.answered_current)
        mock_persistir.assert_called_once_with(self.s)

    def test_persiste_en_db(self):
        with patch.object(cog.entrevistas_db, "guardar_sesion_entrevista") as mock_guardar:
            cog._desbloquear_modal(self.s, "test")
        mock_guardar.assert_called_once()
        datos = mock_guardar.call_args[0][0]
        self.assertFalse(datos["processing"])
        self.assertFalse(datos["modal_open"])


class TestViewModalPuedeDesbloquearse(unittest.TestCase):
    def tearDown(self):
        cog.active_interviews.clear()

    def test_modal_viejo_se_desbloquea(self):
        s = _sesion(
            processing=True,
            modal_open=True,
            modal_opened_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        )
        view = cog.QuestionView(s)
        self.assertTrue(view._modal_puede_desbloquearse())

    def test_modal_reciente_no_se_desbloquea(self):
        s = _sesion(
            processing=True,
            modal_open=True,
            modal_opened_at=datetime.now(timezone.utc),
        )
        view = cog.QuestionView(s)
        self.assertFalse(view._modal_puede_desbloquearse())

    def test_sin_timestamp_se_desbloquea(self):
        s = _sesion(processing=True)
        view = cog.QuestionView(s)
        self.assertTrue(view._modal_puede_desbloquearse())


class TestReasonModalTimeout(unittest.TestCase):
    def tearDown(self):
        cog.active_interviews.clear()

    def test_timeout_desbloquea(self):
        import asyncio
        s = _sesion(
            processing=True,
            modal_open=True,
            modal_opened_at=datetime.now(timezone.utc),
            modal_tipo="REGULAR",
        )
        cog.active_interviews[s.user_id] = s
        modal = cog.ReasonModal(s, "REGULAR")
        with patch.object(cog, "_persistir_sesion") as mock_persistir:
            asyncio.run(modal.on_timeout())
        self.assertFalse(s.processing)
        self.assertFalse(s.modal_open)
        self.assertIsNone(s.modal_opened_at)
        self.assertIsNone(s.modal_tipo)
        mock_persistir.assert_called_once_with(s)


class TestLimpiarModalesAbandonados(unittest.TestCase):
    def tearDown(self):
        cog.active_interviews.clear()

    def test_desbloquea_modal_viejo(self):
        import asyncio
        s_viejo = _sesion(
            user_id=100,
            modal_open=True,
            modal_opened_at=datetime.now(timezone.utc) - timedelta(seconds=cog.MODAL_TIMEOUT + 30),
            modal_tipo="MAL",
        )
        s_nuevo = _sesion(
            user_id=101,
            modal_open=True,
            modal_opened_at=datetime.now(timezone.utc),
            modal_tipo="REGULAR",
        )
        cog.active_interviews[100] = s_viejo
        cog.active_interviews[101] = s_nuevo
        with patch.object(cog, "_persistir_sesion") as mock_persistir:
            async def _correr():
                task = asyncio.create_task(cog._limpiar_modales_abandonados())
                await asyncio.sleep(0.1)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            asyncio.run(_correr())
        self.assertFalse(s_viejo.modal_open)
        self.assertTrue(s_nuevo.modal_open)
        mock_persistir.assert_called()


class TestBuildDiagnosticoEmbed(unittest.TestCase):
    def tearDown(self):
        cog.active_interviews.clear()

    def test_estado_activa(self):
        s = _sesion(current_index=1)
        cog.active_interviews[s.user_id] = s
        embed = cog._build_diagnostico_embed(s, None, None)
        self.assertIn("Diagn\u00f3stico de entrevista", embed.title)
        self.assertIn("Activa", embed.description)

    def test_muestra_progreso_y_respuestas(self):
        s = _sesion(current_index=1, answers=["BIEN"])
        embed = cog._build_diagnostico_embed(s, None, None)
        progreso = next(f for f in embed.fields if f.name == "\U0001f4cb Progreso")
        self.assertIn("2 de 2", progreso.value)
        respuestas = next(f for f in embed.fields if f.name == "\U0001f4cb Respuestas dadas")
        self.assertIn("GENERAL", respuestas.value)

    def test_modal_abierto_en_embed(self):
        s = _sesion(
            modal_open=True,
            modal_opened_at=datetime.now(timezone.utc) - timedelta(seconds=30),
            modal_tipo="REGULAR",
        )
        embed = cog._build_diagnostico_embed(s, None, None)
        modal_field = next(f for f in embed.fields if f.name == "\U0001f4c1 Modal abierto")
        self.assertIn("REGULAR", modal_field.value)

    def test_modal_vencido_en_embed(self):
        s = _sesion(
            modal_open=True,
            modal_opened_at=datetime.now(timezone.utc) - timedelta(seconds=cog.MODAL_TIMEOUT + 10),
            modal_tipo="MAL",
        )
        embed = cog._build_diagnostico_embed(s, None, None)
        modal_field = next(f for f in embed.fields if f.name == "\U0001f4c1 Modal abierto")
        self.assertIn("se desbloquear\u00e1 autom\u00e1ticamente", modal_field.value)

    def test_sin_modal(self):
        s = _sesion()
        embed = cog._build_diagnostico_embed(s, None, None)
        modal_field = next(f for f in embed.fields if f.name == "\U0001f4c1 Modal abierto")
        self.assertEqual(modal_field.value, "No")

    def test_en_memoria(self):
        s = _sesion()
        cog.active_interviews[s.user_id] = s
        embed = cog._build_diagnostico_embed(s, None, None)
        memoria = next(f for f in embed.fields if f.name == "\U0001f3ae En memoria")
        self.assertIn("interfaz viva", memoria.value)

    def test_reconstruida_desde_db(self):
        s = _sesion()
        embed = cog._build_diagnostico_embed(s, None, None)
        memoria = next(f for f in embed.fields if f.name == "\U0001f3ae En memoria")
        self.assertIn("No (reconstruida desde la DB)", memoria.value)


if __name__ == "__main__":
    unittest.main()
