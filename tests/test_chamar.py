"""Testes do cargo de jogos e do /chamar."""

from datetime import datetime, timedelta, timezone

import pytest

from db import MINUTOS_ENTRE_PINGS, Database

G, ANA, BIA, CAIO = 1, 100, 200, 300
AGORA = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    banco = Database(":memory:")
    yield banco
    banco.fechar()


def test_cargo_e_tempo_minimo_entre_marcacoes(db):
    assert db.cargo_jogos(G) is None
    db.definir_cargo_jogos(G, 777)
    assert db.cargo_jogos(G) == 777
    assert db.tentar_marcar_cargo(G, AGORA)
    assert not db.tentar_marcar_cargo(G, AGORA + timedelta(minutes=5))   # cedo demais
    assert db.tentar_marcar_cargo(G, AGORA + timedelta(minutes=MINUTOS_ENTRE_PINGS + 1))
    assert db.tentar_marcar_cargo(2, AGORA)  # outro servidor não é afetado


def test_chamado_respostas_e_fechamento(db):
    cid = db.criar_chamado(G, 10, ANA, "PEAK", 3)
    assert db.respostas_chamado(cid) == {"bora": [ANA], "depois": [], "nao": []}  # quem chama já está dentro
    db.responder_chamado(cid, BIA, "depois")
    db.responder_chamado(cid, CAIO, "nao")
    db.responder_chamado(cid, BIA, "bora")  # mudou de ideia
    r = db.respostas_chamado(cid)
    assert sorted(r["bora"]) == [ANA, BIA] and r["depois"] == [] and r["nao"] == [CAIO]
    assert db.marcar_chamado_fechado(cid)
    assert not db.marcar_chamado_fechado(cid)  # só avisa "fechou" uma vez
    db.encerrar_chamado(cid)
    assert db.chamado(cid)["encerrado"] == 1


def test_resposta_invalida(db):
    cid = db.criar_chamado(G, 10, ANA, None, None)
    with pytest.raises(ValueError):
        db.responder_chamado(cid, BIA, "talvez")
