"""Testes dos nomes digitados de qualquer jeito."""

from nomes import (ler_escolha, mesmo_jogo, ranquear_parecidos, semelhanca, termos_de_busca,
                   valor_escolha)

RESULTADOS_STEAM = [
    (1966720, "Lethal Company"),
    (3241660, "R.E.P.O."),
    (367520, "Hollow Knight"),
    (999, "Lethal League Blaze"),
    (5, "Company of Heroes 2"),
]


def test_erros_de_digitacao_acham_o_jogo_certo():
    assert ranquear_parecidos("letal compani", RESULTADOS_STEAM)[0] == (1966720, "Lethal Company")
    assert ranquear_parecidos("hollow night", RESULTADOS_STEAM)[0] == (367520, "Hollow Knight")
    assert ranquear_parecidos("repo", RESULTADOS_STEAM)[0] == (3241660, "R.E.P.O.")


def test_comeco_do_nome_com_erro():
    catalogo = [(2, "Dale & Dawson Stationery Supplies"), (3, "Dawnsong"), (4, "Dale Earnhardt Jr")]
    assert ranquear_parecidos("dale & dawnson", catalogo)[0] == (2, "Dale & Dawson Stationery Supplies")
    assert ranquear_parecidos("dale dawson", catalogo)[0] == (2, "Dale & Dawson Stationery Supplies")


def test_nome_incompleto():
    assert ranquear_parecidos("lethal", RESULTADOS_STEAM)[0] == (1966720, "Lethal Company")
    assert semelhanca("let", "Lethal Company") < 0.55  # curto demais para adivinhar


def test_nada_parecido_nao_sugere():
    assert ranquear_parecidos("xadrez", RESULTADOS_STEAM) == []


def test_mesmo_jogo():
    assert mesmo_jogo("R.E.P.O.", "repo")
    assert mesmo_jogo("Lethal Company", "lethal  company!")
    assert mesmo_jogo("Lethal Company", "Letal Company")
    assert not mesmo_jogo("Portal", "Portal 2")
    assert not mesmo_jogo("Left 4 Dead", "Left 4 Dead 2")
    assert not mesmo_jogo("Lethal Company", "Lethal League")


def test_termos_de_busca():
    assert termos_de_busca("letal compani") == ["letal compani", "compani", "letal"]
    assert termos_de_busca("  PEAK ") == ["PEAK", "peak"]
    assert termos_de_busca("cs") == ["cs"]


def test_escolha_do_autocompletar():
    assert ler_escolha(valor_escolha(1966720, "Lethal Company")) == ("Lethal Company", 1966720)
    assert ler_escolha("  jogo que digitei  ") == ("jogo que digitei", None)
    assert ler_escolha("1|2 Jogo|com barra") == ("2 Jogo|com barra", 1)
    assert len(valor_escolha(1, "x" * 200)) == 100


# ---------------------------------------------------------------- repetidos no banco

import pytest

from db import Database


@pytest.fixture
def db():
    banco = Database(":memory:")
    yield banco
    banco.fechar()


def test_roleta_barra_o_mesmo_jogo_escrito_diferente(db):
    m = db.criar_maratona(1, 10, 1, "M")
    db.adicionar_jogos(m.id, 1, ["R.E.P.O."], {"R.E.P.O.": 3241660})
    adicionados, recusados = db.adicionar_jogos(m.id, 2, ["repo", "Lethal Company", "Portal 2"])
    assert adicionados == ["Lethal Company", "Portal 2"]
    assert recusados == [("repo", "já está na roleta")]
    # mesmo jogo da Steam com nome diferente também é repetido
    _, recusados = db.adicionar_jogos(m.id, 3, ["REPO (2025)"], {"REPO (2025)": 3241660})
    assert recusados[0][1] == "já está na roleta"
    assert db.jogos(m.id)[0].appid == 3241660


def test_appid_conhecido_vem_do_que_o_bot_ja_descobriu(db):
    db.salvar_steam_nome("Lethal Company", 1966720)
    m = db.criar_maratona(1, 10, 1, "M")
    db.adicionar_jogos(m.id, 1, ["lethal company"])
    assert db.jogos(m.id)[0].appid == 1966720


def test_lista_barra_o_mesmo_jogo_escrito_diferente(db):
    db.salvar_na_lista(1, ["Lethal Company"])
    salvos, recusados = db.salvar_na_lista(1, ["lethal  company!", "Portal", "Portal 2"])
    assert salvos == ["Portal", "Portal 2"] and recusados[0][0] == "lethal  company!"
