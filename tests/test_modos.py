"""Testes dos modos da roleta."""

import pytest

import modos
from biblioteca import AppSteam, Opcao, reais
from db import Database, ErroMaratona

AGORA = "2026-10-22T03:00:00+00:00"


def opcao(preco=2000, gratis=False, donos=(1,), faltam=(2,)):
    app = AppSteam(10, "Jogo", gratis, preco, reais(preco) if preco is not None else None, 0, True, True, AGORA)
    return Opcao(app, list(donos), list(faltam))


def test_livre_deixa_tudo():
    assert modos.passa("livre", None, None) is True
    assert modos.passa("livre", None, opcao(preco=99999)) is True


def test_ninguem_precisa_comprar():
    assert modos.passa("todos_tem", None, opcao(faltam=())) is True                # todo mundo tem
    assert modos.passa("todos_tem", None, opcao(preco=None, gratis=True)) is True  # grátis
    assert modos.passa("todos_tem", None, opcao(preco=500)) is False               # alguém teria que comprar
    assert modos.passa("todos_tem", None, None) is False                           # não deu para conferir


def test_ate_preco():
    assert modos.passa("ate_preco", 2000, opcao(preco=2000)) is True     # no limite: passa
    assert modos.passa("ate_preco", 2000, opcao(preco=2001)) is False
    assert modos.passa("ate_preco", 2000, opcao(preco=9999, faltam=())) is True    # todo mundo tem
    assert modos.passa("ate_preco", 2000, opcao(preco=None)) is False    # sem preço no Brasil
    assert modos.passa("ate_preco", 2000, None) is False


def test_validar_e_descricao():
    assert modos.validar("ate_preco", 20) == 2000
    assert modos.validar("todos_tem", 20) is None   # preço só vale no modo ate_preco
    with pytest.raises(ValueError, match="preço máximo"):
        modos.validar("ate_preco", None)
    assert modos.descricao("ate_preco", 2000) == "💸 Só jogos de até R$ 20,00 para quem não tem"
    assert modos.descricao("livre").startswith("🎲")


def test_banco_guarda_o_modo_e_sorteia_so_os_permitidos():
    db = Database(":memory:")
    m = db.criar_maratona(1, 10, 100, "Noite")
    assert m.modo == "livre" and m.modo_preco is None
    db.adicionar_jogos(m.id, 100, ["A", "B", "C"])
    ids = {j.nome: j.id for j in db.jogos(m.id)}
    m = db.definir_modo(m.id, "ate_preco", 2000)
    assert (m.modo, m.modo_preco) == ("ate_preco", 2000)
    s = db.sortear(m.id, permitidos={ids["B"]})  # só B pode sair; "restantes" conta a roleta toda
    assert s.jogo.nome == "B" and s.restantes == 2
    assert db.sortear(m.id, permitidos={ids["B"]}) is None    # B já saiu: nenhum permitido sobrou
    assert db.sortear(m.id, permitidos=set()) is None
    assert db.sortear(m.id).jogo.nome in ("A", "C")           # sem modo, qualquer um
    db.encerrar(m.id)
    with pytest.raises(ErroMaratona):
        db.definir_modo(m.id, "livre", None)
    db.fechar()
