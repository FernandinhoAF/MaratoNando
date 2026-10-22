"""Testes das notas dos jogos: regras do banco e o ranking dos favoritos."""

import pytest

from db import Database, ErroMaratona
from notas import estrelas, numero, ranking, resumo_nota

ANA, BIA, CAIO, ZE = 1, 2, 3, 99


@pytest.fixture
def db():
    banco = Database(":memory:")
    yield banco
    banco.fechar()


def maratona_com_jogos(db, guild=10, jogos=("PEAK", "R.E.P.O.")):
    m = db.criar_maratona(guild, 100, ANA, "Noite")
    db.adicionar_jogos(m.id, ANA, [jogos[0]])
    db.adicionar_jogos(m.id, BIA, list(jogos[1:]))
    db.entrar_sem_jogo(m.id, CAIO)
    return m


# ---------------------------------------------------------------- banco

def test_so_quem_jogou_da_nota_e_pode_trocar(db):
    m = maratona_com_jogos(db)
    jogo = db.sortear(m.id).jogo
    assert db.dar_nota(jogo.id, ANA, 5) == (5.0, 1)
    assert db.dar_nota(jogo.id, CAIO, 3) == (4.0, 2)   # quem entrou sem jogo também vota
    assert db.dar_nota(jogo.id, ANA, 1) == (2.0, 2)    # trocou a nota: continua 2 notas
    with pytest.raises(ErroMaratona, match="Só quem estava"):
        db.dar_nota(jogo.id, ZE, 4)
    with pytest.raises(ErroMaratona, match="1 a 5"):
        db.dar_nota(jogo.id, ANA, 6)


def test_jogo_ainda_na_roleta_nao_recebe_nota(db):
    m = maratona_com_jogos(db)
    pendente = db.jogos(m.id)[0]
    with pytest.raises(ErroMaratona):
        db.dar_nota(pendente.id, ANA, 5)


def test_nota_depois_de_encerrar_continua_valendo(db):
    m = maratona_com_jogos(db)
    jogo = db.sortear(m.id).jogo
    db.encerrar(m.id)
    assert db.dar_nota(jogo.id, BIA, 4) == (4.0, 1)


def test_pede_nota_uma_vez_so(db):
    m = maratona_com_jogos(db)
    s = db.sortear(m.id)
    assert db.marcar_nota_pedida(s.jogo.id) is True
    assert db.marcar_nota_pedida(s.jogo.id) is False
    assert db.jogo_por_ordem(m.id, 1).id == s.jogo.id
    assert db.jogo_por_ordem(m.id, 2) is None
    pendente = next(j for j in db.jogos(m.id) if not j.jogado)
    assert db.marcar_nota_pedida(pendente.id) is False  # não pede nota de jogo que não saiu


def test_notas_da_maratona_e_do_servidor(db):
    m = maratona_com_jogos(db)
    a = db.sortear(m.id).jogo
    b = db.sortear(m.id).jogo
    db.dar_nota(a.id, ANA, 4)
    db.dar_nota(a.id, BIA, 5)
    assert db.notas_da_maratona(m.id) == {a.id: (4.5, 2)}
    linhas = db.notas_do_servidor(10)
    assert sorted(linhas) == sorted([(a.id, a.nome, None, 4), (a.id, a.nome, None, 5), (b.id, b.nome, None, None)])
    assert db.notas_do_servidor(11) == []  # outro servidor não vê


# ---------------------------------------------------------------- ranking

def test_ranking_junta_o_mesmo_jogo_de_varias_maratonas():
    linhas = [
        (1, "PEAK", 3527290, 5), (1, "PEAK", 3527290, 4),
        (7, "Peak", 3527290, 5),                     # outra maratona, mesmo jogo da Steam
        (2, "Jogo Ruim", None, 2), (2, "Jogo Ruim", None, 1),
        (3, "lethal company", None, 5), (8, "Lethal  Company!", None, 4),  # sem Steam: junta pelo nome
        (4, "Só Um Voto", None, 5),                  # 1 nota só: fica de fora
        (5, "Sem Nota", None, None),
    ]
    r = ranking(linhas)
    assert [(j.nome, round(j.media, 2), j.notas, j.vezes) for j in r] == [
        ("Peak", 4.67, 3, 2),
        ("Lethal  Company!", 4.5, 2, 2),
        ("Jogo Ruim", 1.5, 2, 1),
    ]
    assert [j.nome for j in ranking(linhas, minimo=1)][:2] == ["Só Um Voto", "Peak"]


def test_empate_na_media_ganha_quem_tem_mais_notas():
    linhas = [(1, "A", None, 4), (1, "A", None, 4), (2, "B", None, 4), (2, "B", None, 4), (2, "B", None, 4)]
    assert [j.nome for j in ranking(linhas)] == ["B", "A"]


def test_textos():
    assert estrelas(4.3) == "★★★★☆"
    assert estrelas(4.5) == "★★★★★"
    assert estrelas(1.0) == "★☆☆☆☆"
    assert numero(4.26) == "4,3"
    assert numero(4.0) == "4,0"
    assert resumo_nota(0, 0) == "Ninguém deu nota ainda"
    assert resumo_nota(5, 1) == "★★★★★ 5,0 · 1 nota"
    assert resumo_nota(3.5, 4) == "★★★★☆ 3,5 · 4 notas"
