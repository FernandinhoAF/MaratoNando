"""Testes da agenda de limpeza do chat."""

from datetime import datetime, timedelta, timezone

import pytest

from db import MINUTOS_LIMPEZA_PADRAO, Database

AGORA = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    banco = Database(":memory:")
    yield banco
    banco.fechar()


def test_config_padrao_nao_apaga_nada(db):
    assert db.limpeza_config(1) == (None, MINUTOS_LIMPEZA_PADRAO)


def test_config_canal_e_minutos(db):
    db.definir_canal_bot(1, 500)
    db.definir_minutos_limpeza(1, 5)
    assert db.limpeza_config(1) == (500, 5)
    db.definir_minutos_limpeza(1, 0)
    assert db.limpeza_config(1) == (500, 0)


def test_agenda_vence_adiar_e_desagendar(db):
    db.agendar_apagar(10, 700, 1, AGORA + timedelta(minutes=10))
    db.agendar_apagar(11, 700, 1, AGORA + timedelta(minutes=1))
    assert db.mensagens_vencidas(AGORA) == []
    assert db.mensagens_vencidas(AGORA + timedelta(minutes=2)) == [(11, 700, 1)]
    assert db.adiar_apagar(11, AGORA + timedelta(minutes=30))
    assert not db.adiar_apagar(999, AGORA)  # não estava agendada
    assert db.mensagens_vencidas(AGORA + timedelta(minutes=15)) == [(10, 700, 1)]
    db.desagendar(10)
    assert db.mensagens_vencidas(AGORA + timedelta(hours=1)) == [(11, 700, 1)]


def test_desligar_ou_mudar_canal_limpa_a_agenda(db):
    db.agendar_apagar(10, 700, 1, AGORA)
    db.agendar_apagar(11, 800, 1, AGORA)
    db.definir_canal_bot(1, 800)  # o canal 800 virou o do bot: a mensagem dele fica
    assert db.mensagens_vencidas(AGORA) == [(10, 700, 1)]
    db.definir_canal_bot(1, None)  # limpeza desligada
    assert db.mensagens_vencidas(AGORA) == []
