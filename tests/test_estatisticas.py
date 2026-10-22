"""Testes das estatísticas do grupo (/stats)."""

from datetime import datetime, timezone

import pytest

import estatisticas as est
from db import Database

ANA, BIA, CAIO = 1, 2, 3


def test_resumo_conta_horas_so_das_encerradas_e_com_limite():
    maratonas = [
        ("2026-09-05T23:00:00+00:00", "2026-09-06T02:30:00+00:00"),  # 3h30
        ("2026-10-01T22:00:00+00:00", "2026-10-05T22:00:00+00:00"),  # esqueceram aberta 4 dias: conta 12h
        ("2026-10-20T22:00:00+00:00", None),                          # ainda aberta: não conta
    ]
    jogos = [(ANA, "PEAK", None, 1), (BIA, "Hades", None, None), (BIA, "REPO", None, 2)]
    r = est.resumo(maratonas, jogos)
    assert r.maratonas == 3 and r.jogos_jogados == 2
    assert r.horas == pytest.approx(15.5)
    assert r.desde == "2026-09-05T23:00:00+00:00"


def test_mais_sorteados_junta_o_mesmo_jogo():
    jogos = [
        (ANA, "PEAK", 3527290, 1), (BIA, "Peak", 3527290, 2),   # mesmo jogo da Steam
        (ANA, "lethal company", None, 3), (CAIO, "Lethal Company!", None, 1),
        (BIA, "Hades", None, 2),
        (CAIO, "Nunca Saiu", None, None),                       # não foi sorteado: não conta
    ]
    assert est.mais_sorteados(jogos) == [("Lethal Company!", 2), ("Peak", 2), ("Hades", 1)]
    assert est.mais_sorteados(jogos, limite=1) == [("Lethal Company!", 2)]


def test_por_pessoa():
    jogos = [(ANA, "A", None, 1), (ANA, "B", None, None), (BIA, "C", None, 2),
             (BIA, "D", None, 3), (BIA, "E", None, None), (CAIO, "F", None, None)]
    assert est.por_pessoa(jogos) == [est.Pessoa(BIA, 3, 2), est.Pessoa(ANA, 2, 1), est.Pessoa(CAIO, 1, 0)]


def test_por_mes_usa_horario_de_brasilia_e_virada_de_ano():
    hoje = datetime(2027, 1, 15, 12, tzinfo=timezone.utc)
    criadas = [
        "2026-12-01T02:00:00+00:00",  # 30/11 às 23h em Brasília → novembro
        "2026-12-20T22:00:00+00:00",
        "2027-01-10T22:00:00+00:00",
        "2026-05-01T22:00:00+00:00",  # mais de 6 meses atrás: fora do gráfico
    ]
    assert est.por_mes(criadas, hoje) == [("ago/26", 0), ("set/26", 0), ("out/26", 0),
                                          ("nov/26", 1), ("dez/26", 1), ("jan/27", 1)]


def test_barras_e_horas():
    assert est.barras([("set/26", 2), ("out/26", 4), ("nov/26", 0)], largura=8) == (
        "set/26 ▇▇▇▇ 2\nout/26 ▇▇▇▇▇▇▇▇ 4\nnov/26 0")
    assert est.barras([("a", 1), ("b", 100)], largura=10).splitlines()[0] == "a ▇ 1"  # nunca some uma barra
    assert est.barras([("a", 0)]) == "a 0"
    assert est.horas_texto(3 + 40 / 60) == "3h40"
    assert est.horas_texto(0.5) == "30 min"
    assert est.horas_texto(27) == "27h"


def test_consultas_do_banco_separam_por_servidor():
    db = Database(":memory:")
    m = db.criar_maratona(10, 100, ANA, "Noite")
    db.adicionar_jogos(m.id, ANA, ["PEAK", "Hades"])
    jogo = db.sortear(m.id).jogo
    db.registrar_reclamacao(m.id, BIA, ANA)
    db.encerrar(m.id)
    outro = db.criar_maratona(20, 200, CAIO, "Outro servidor")
    db.adicionar_jogos(outro.id, CAIO, ["Portal 2"])

    assert len(db.stats_maratonas(10)) == 1 and db.stats_maratonas(10)[0][1] is not None
    assert sorted(db.stats_jogos(10)) == sorted([(ANA, "PEAK", None, 1 if jogo.nome == "PEAK" else None),
                                                 (ANA, "Hades", None, 1 if jogo.nome == "Hades" else None)])
    assert db.stats_mural(10) == [(BIA, 1)]
    assert db.stats_mural(20) == [] and db.stats_jogos(20) == [(CAIO, "Portal 2", None, None)]
    db.fechar()
