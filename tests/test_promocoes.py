"""Testes dos avisos de promoção dos jogos das listas."""

import sqlite3

import pytest

from db import Database

G1, G2, ANA, BIA, CAIO = 1, 2, 100, 200, 300


@pytest.fixture
def db():
    banco = Database(":memory:")
    yield banco
    banco.fechar()


def test_ciclo_de_vida_da_promocao(db):
    assert db.atualizar_promocao(10, "PEAK", 0, None, None) is None          # sem promoção
    assert db.atualizar_promocao(10, "PEAK", 20, "R$ 19,19", "R$ 23,99") == "nova"
    assert db.atualizar_promocao(10, "PEAK", 20, "R$ 19,19", "R$ 23,99") is None  # mesma promoção: não repete
    assert db.atualizar_promocao(10, "PEAK", 38, "R$ 14,87", "R$ 23,99") == "maior"
    assert db.atualizar_promocao(10, "PEAK", 25, "R$ 17,99", "R$ 23,99") is None  # diminuiu: não avisa
    assert db.promocao(10)["desconto"] == 25
    assert db.atualizar_promocao(10, "PEAK", 0, "R$ 23,99", None) is None    # acabou
    assert db.promocao(10) is None
    assert db.atualizar_promocao(10, "PEAK", 30, "R$ 16,79", "R$ 23,99") == "nova"  # voltou: avisa de novo


def test_nomes_das_listas_viram_jogos_da_steam(db):
    db.salvar_na_lista(ANA, ["PEAK", "Jogo Inventado"])
    db.salvar_na_lista(BIA, ["peak"])
    assert sorted(db.nomes_sem_steam()) == ["Jogo Inventado", "PEAK"]  # "peak" e "PEAK" são o mesmo
    db.salvar_steam_nome("PEAK", 10)
    db.salvar_steam_nome("Jogo Inventado", None)
    assert db.nomes_sem_steam() == []  # não achado: só tenta de novo daqui a 2 semanas
    assert db.appids_das_listas() == {10: "PEAK"}
    assert db.status_steam_da_lista(ANA) == {"peak": 10, "jogo inventado": None}


def test_interessados_por_servidor(db):
    for pessoa in (ANA, BIA, CAIO):
        db.salvar_na_lista(pessoa, ["PEAK"])
    db.salvar_steam_nome("PEAK", 10)
    db.registrar_usuario_servidor(ANA, G1)
    db.registrar_usuario_servidor(BIA, G1)
    db.registrar_usuario_servidor(BIA, G2)
    db.registrar_usuario_servidor(BIA, G2)  # repetido não duplica
    assert db.interessados(G1, 10) == [ANA, BIA]
    assert db.interessados(G2, 10) == [BIA]  # Caio não usa o bot em nenhum servidor
    assert db.interessados(G1, 99) == []


def test_promocoes_aparecem_na_lista(db):
    db.salvar_na_lista(ANA, ["PEAK", "Outro"])
    db.salvar_steam_nome("PEAK", 10)
    db.atualizar_promocao(10, "PEAK", 38, "R$ 14,87", "R$ 23,99")
    promos = db.promocoes_da_lista(ANA)
    assert list(promos) == ["peak"] and promos["peak"]["desconto"] == 38


def test_config_de_avisos(db):
    assert db.avisar_promocoes(G1)  # ligado por padrão
    assert db.servidores_para_promocoes() == []  # mas precisa do canal do bot
    db.definir_canal_bot(G1, 555)
    assert db.servidores_para_promocoes() == [(G1, 555)]
    db.definir_avisar_promocoes(G1, False)
    assert not db.avisar_promocoes(G1) and db.servidores_para_promocoes() == []


def test_banco_antigo_ganha_a_coluna_nova(tmp_path):
    caminho = tmp_path / "antigo.db"
    conn = sqlite3.connect(caminho)  # banco "da versão anterior", sem avisar_promocoes
    conn.executescript(
        "CREATE TABLE limpeza_config (guild_id INTEGER PRIMARY KEY, canal_bot INTEGER, minutos INTEGER);"
        "INSERT INTO limpeza_config VALUES (1, 555, 10);"
    )
    conn.commit()
    conn.close()
    banco = Database(caminho)
    assert banco.limpeza_config(1) == (555, 10)  # nada se perdeu
    assert banco.servidores_para_promocoes() == [(1, 555)]
    banco.fechar()


def test_quem_jogou_maratona_conta_como_usuario_do_servidor(tmp_path):
    caminho = tmp_path / "m.db"
    banco = Database(caminho)
    m = banco.criar_maratona(G1, 10, ANA, "M")
    banco.adicionar_jogos(m.id, BIA, ["X"])
    banco.fechar()
    banco = Database(caminho)  # ao abrir de novo, a migração preenche usuarios_servidores
    banco.salvar_na_lista(BIA, ["PEAK"])
    banco.salvar_steam_nome("PEAK", 10)
    assert banco.interessados(G1, 10) == [BIA]
    banco.fechar()
