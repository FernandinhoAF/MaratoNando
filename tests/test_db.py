"""Testes das regras da maratona (sem precisar do Discord).

Rode com:  python -m pytest
"""

import pytest

from db import Database, ErroMaratona, separar_nomes

GUILD, CANAL, HOST, ANA, BIA = 1, 10, 100, 200, 300


@pytest.fixture
def db():
    banco = Database(":memory:")  # banco temporário, só na memória
    yield banco
    banco.fechar()


@pytest.fixture
def maratona(db):
    return db.criar_maratona(GUILD, CANAL, HOST, "Maratona de teste")


def test_separar_nomes_limpa_linhas_e_marcadores():
    texto = "  Hollow Knight \n\n- Stardew   Valley\n1. Papers, Please\n• Hades\n1000xRESIST"
    assert separar_nomes(texto) == ["Hollow Knight", "Stardew Valley", "Papers, Please", "Hades", "1000xRESIST"]


def test_so_uma_maratona_aberta_por_servidor(db, maratona):
    with pytest.raises(ErroMaratona):
        db.criar_maratona(GUILD, CANAL, ANA, "Outra")
    db.criar_maratona(2, CANAL, ANA, "Outro servidor")  # outro servidor pode
    db.encerrar(maratona.id)
    db.criar_maratona(GUILD, CANAL, ANA, "Depois de encerrar")  # depois de encerrar, pode


def test_repetidos_sao_recusados_mesmo_com_maiusculas(db, maratona):
    adicionados, recusados = db.adicionar_jogos(maratona.id, ANA, ["Hades", "hades"])
    assert adicionados == ["Hades"]
    assert recusados == [("hades", "já está na roleta")]
    _, recusados = db.adicionar_jogos(maratona.id, BIA, ["HADES"])
    assert recusados == [("HADES", "já está na roleta")]


def test_limite_por_pessoa(db):
    m = db.criar_maratona(GUILD, CANAL, HOST, "Com limite", limite=2)
    adicionados, recusados = db.adicionar_jogos(m.id, ANA, ["A", "B", "C"])
    assert adicionados == ["A", "B"]
    assert recusados[0][0] == "C"
    adicionados, _ = db.adicionar_jogos(m.id, BIA, ["D", "E"])
    assert adicionados == ["D", "E"]  # o limite é por pessoa


def test_sorteio_passa_por_todos_sem_repetir(db, maratona):
    db.adicionar_jogos(maratona.id, ANA, ["A", "B", "C"])
    db.adicionar_jogos(maratona.id, BIA, ["D", "E"])
    sorteados = []
    while (s := db.sortear(maratona.id)) is not None:
        sorteados.append(s)
    assert sorted(s.jogo.nome for s in sorteados) == ["A", "B", "C", "D", "E"]
    assert [s.numero for s in sorteados] == [1, 2, 3, 4, 5]
    assert [s.restantes for s in sorteados] == [4, 3, 2, 1, 0]
    assert db.contagem(maratona.id) == (0, 5)
    assert db.ultimo_sorteado(maratona.id).nome == sorteados[-1].jogo.nome


def test_jogo_ja_jogado_nao_pode_ser_recolocado_nem_removido(db, maratona):
    db.adicionar_jogos(maratona.id, ANA, ["Celeste"])
    s = db.sortear(maratona.id)
    _, recusados = db.adicionar_jogos(maratona.id, BIA, ["celeste"])
    assert recusados == [("celeste", "já foi jogado nesta maratona")]
    with pytest.raises(ErroMaratona):
        db.remover_jogo(maratona.id, ANA, s.jogo.id)


def test_so_o_dono_remove_o_proprio_jogo(db, maratona):
    db.adicionar_jogos(maratona.id, ANA, ["Portal 2"])
    jogo = db.jogos_do_usuario(maratona.id, ANA)[0]
    with pytest.raises(ErroMaratona):
        db.remover_jogo(maratona.id, BIA, jogo.id)
    db.remover_jogo(maratona.id, ANA, jogo.id)
    assert db.jogos(maratona.id) == []


def test_participantes_e_contagem(db, maratona):
    db.adicionar_jogos(maratona.id, ANA, ["A", "B"])
    db.adicionar_jogos(maratona.id, BIA, ["C"])
    assert [(p.user_id, p.total) for p in db.participantes(maratona.id)] == [(ANA, 2), (BIA, 1)]
    assert db.contagem(maratona.id) == (3, 0)


def test_mural_da_vergonha_e_anti_spam(db, maratona):
    db.adicionar_jogos(maratona.id, ANA, ["A"])
    db.sortear(maratona.id)
    assert db.registrar_reclamacao(maratona.id, BIA, ANA) == 1
    with pytest.raises(ErroMaratona):  # mesma denúncia logo em seguida
        db.registrar_reclamacao(maratona.id, BIA, ANA)
    assert db.registrar_reclamacao(maratona.id, BIA, HOST) == 2  # outra pessoa pode dedurar
    assert db.registrar_reclamacao(maratona.id, ANA, ANA) == 1   # confissão
    assert db.mural(maratona.id) == [(BIA, 2), (ANA, 1)]


def test_maratona_encerrada_nao_aceita_mais_nada(db, maratona):
    db.adicionar_jogos(maratona.id, ANA, ["A"])
    db.encerrar(maratona.id)
    for acao in (
        lambda: db.adicionar_jogos(maratona.id, ANA, ["B"]),
        lambda: db.sortear(maratona.id),
        lambda: db.registrar_reclamacao(maratona.id, ANA, BIA),
        lambda: db.encerrar(maratona.id),
    ):
        with pytest.raises(ErroMaratona):
            acao()


def test_dados_sobrevivem_a_reinicio(tmp_path):
    caminho = tmp_path / "teste.db"
    banco = Database(caminho)
    m = banco.criar_maratona(GUILD, CANAL, HOST, "Persistente")
    banco.adicionar_jogos(m.id, ANA, ["A", "B"])
    banco.sortear(m.id)
    banco.fechar()

    banco = Database(caminho)  # "reiniciou o bot"
    assert banco.maratona_aberta(GUILD).nome == "Persistente"
    assert banco.contagem(m.id) == (1, 1)
    banco.fechar()


# ---------------------------------------------------------------- lista pessoal

def test_lista_pessoal_salva_recusa_repetidos_e_remove(db):
    salvos, recusados = db.salvar_na_lista(ANA, ["Lethal Company", "lethal company", "Peak"])
    assert salvos == ["Lethal Company", "Peak"]
    assert recusados == [("lethal company", "já está na sua lista")]
    assert [i.nome for i in db.lista(ANA)] == ["Lethal Company", "Peak"]
    assert db.lista(BIA) == []  # cada um tem a sua
    item = db.lista(ANA)[0]
    with pytest.raises(ErroMaratona):
        db.remover_da_lista(BIA, item.id)  # não dá pra apagar da lista dos outros
    db.remover_da_lista(ANA, item.id)
    assert [i.nome for i in db.lista(ANA)] == ["Peak"]


def test_lista_fora_da_maratona_esconde_o_que_ja_esta_na_roleta(db, maratona):
    db.salvar_na_lista(ANA, ["A", "B", "C"])
    db.adicionar_jogos(maratona.id, ANA, ["A"])
    db.adicionar_jogos(maratona.id, BIA, ["b"])  # outra pessoa já colocou "B"
    assert [i.nome for i in db.lista_fora_da_maratona(ANA, maratona.id)] == ["C"]


def test_jogo_sorteado_sai_da_lista_e_sobras_voltam_ao_encerrar(db, maratona):
    db.salvar_na_lista(ANA, ["A"])
    db.adicionar_jogos(maratona.id, ANA, ["A"])
    db.sortear(maratona.id)
    assert db.lista(ANA) == []  # já jogou, sai da lista

    db.adicionar_jogos(maratona.id, BIA, ["X", "Y"])  # escritos direto, sem lista
    db.encerrar(maratona.id)
    assert sorted(i.nome for i in db.lista(BIA)) == ["X", "Y"]  # não saíram, voltaram pra lista


def test_lista_tem_limite(db):
    from db import MAX_LISTA
    db.salvar_na_lista(ANA, [f"Jogo {n}" for n in range(MAX_LISTA)])
    salvos, recusados = db.salvar_na_lista(ANA, ["Mais um"])
    assert salvos == [] and len(recusados) == 1
