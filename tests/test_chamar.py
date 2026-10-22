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


# ---------------------------------------------------------------- vagas e fila

from vagas import dividir, ganharam_vaga, posicao_na_fila

DUDA, ZE = 400, 500


def test_dividir_vagas_e_fila():
    assert dividir([ANA, BIA, CAIO], None) == ([ANA, BIA, CAIO], [])   # sem vagas: todo mundo dentro
    assert dividir([ANA, BIA, CAIO, DUDA], 2) == ([ANA, BIA], [CAIO, DUDA])
    assert posicao_na_fila([CAIO, DUDA], DUDA) == 2 and posicao_na_fila([CAIO], ANA) is None
    assert ganharam_vaga([ANA, BIA], [ANA, CAIO], quem_clicou=BIA) == [CAIO]   # Bia saiu, Caio subiu
    assert ganharam_vaga([ANA], [ANA, BIA], quem_clicou=BIA) == []             # quem clicou já sabe


def test_fila_respeita_a_ordem_dos_cliques(db):
    cid = db.criar_chamado(G, 10, ANA, "PEAK", 2)          # 2 vagas; Ana (quem chamou) já está dentro
    for quem in (BIA, CAIO, DUDA):
        db.responder_chamado(cid, quem, "bora")
    assert dividir(db.respostas_chamado(cid)["bora"], 2) == ([ANA, BIA], [CAIO, DUDA])
    assert db.responder_chamado(cid, ANA, "bora") is False  # clicou Bora de novo: não perde o lugar
    assert dividir(db.respostas_chamado(cid)["bora"], 2) == ([ANA, BIA], [CAIO, DUDA])
    db.responder_chamado(cid, BIA, "nao")                   # Bia saiu: Caio sobe
    assert dividir(db.respostas_chamado(cid)["bora"], 2) == ([ANA, CAIO], [DUDA])
    db.responder_chamado(cid, BIA, "bora")                  # Bia voltou: vai para o fim da fila
    assert dividir(db.respostas_chamado(cid)["bora"], 2) == ([ANA, CAIO], [DUDA, BIA])


def test_lembrete_da_hora_marcada(db):
    cid = db.criar_chamado(G, 10, ANA, "PEAK", None, inicio="2026-10-22T00:00:00+00:00")
    sem_hora = db.criar_chamado(G, 10, ANA, "REPO", None)
    assert db.chamados_para_lembrar("2026-10-21T23:59:00+00:00") == []
    assert [c["id"] for c in db.chamados_para_lembrar("2026-10-22T00:00:00+00:00")] == [cid]
    assert db.marcar_chamado_lembrado(cid) is True and db.marcar_chamado_lembrado(cid) is False
    assert db.chamados_para_lembrar("2026-10-23T00:00:00+00:00") == []
    assert db.chamado(sem_hora)["inicio"] is None
