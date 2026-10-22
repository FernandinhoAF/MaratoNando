"""Testes do /perfil: horas, mais jogados, gêneros favoritos e números nas maratonas."""

from db import Database
from perfil import generos_favoritos, horas, mais_jogados

# (appid, minutos no total, minutos nas últimas 2 semanas)
BIBLIOTECA = [(10, 6000, 300), (20, 3000, 0), (30, 1000, 600), (40, 0, 0), (50, 45, 45)]
GENEROS = {10: ["Action", "Indie"], 20: ["Strategy"], 30: ["Action", "Early Access"], 50: ["Casual"]}


def test_horas():
    assert horas(30) == "30 min"
    assert horas(125) == "2h"
    assert horas(123456) == "2.057h"


def test_mais_jogados():
    assert mais_jogados(BIBLIOTECA, 3) == [(10, 6000), (20, 3000), (30, 1000)]
    assert mais_jogados(BIBLIOTECA, 3, recentes=True) == [(30, 600), (10, 300), (50, 45)]
    assert (40, 0) not in mais_jogados(BIBLIOTECA, 10)  # nunca aberto


def test_generos_pelo_tempo_jogado():
    # tempo dos jogos com gênero conhecido: 6000 + 3000 + 1000 + 45 = 10045
    # Ação = 6000 + 1000 = 7000 (70%); Estratégia = 3000 (30%); Indie = 6000 (60%); Casual = 45 (0%)
    assert generos_favoritos(BIBLIOTECA, GENEROS) == [("Ação", 70), ("Indie", 60), ("Estratégia", 30)]
    assert all(g != "Acesso antecipado" for g, _ in generos_favoritos(BIBLIOTECA, GENEROS, limite=10))
    assert generos_favoritos(BIBLIOTECA, {}) == []
    assert generos_favoritos([], GENEROS) == []


def test_banco_guarda_horas_e_generos():
    db = Database(":memory:")
    db.vincular_steam(1, "76561198000000001", "x", 1)
    db.salvar_biblioteca(1, True, [10, 20], {10: (600, 60)})
    assert sorted(db.biblioteca_com_tempo(1)) == [(10, 600, 60), (20, 0, 0)]
    db.salvar_generos(10, ["Action", "Indie"])
    db.salvar_generos(20, [])                           # a loja não informa: guarda vazio (não pergunta de novo)
    assert db.generos_de([10, 20, 30]) == {10: ["Action", "Indie"], 20: []}
    db.fechar()


def test_perfil_no_servidor():
    db = Database(":memory:")
    m = db.criar_maratona(1, 10, 100, "Noite")
    db.adicionar_jogos(m.id, 100, ["PEAK", "Hades", "REPO"])
    db.entrar_sem_jogo(m.id, 200)
    jogo = db.sortear(m.id).jogo
    db.dar_nota(jogo.id, 200, 5)
    db.registrar_reclamacao(m.id, 200, 100)
    db.salvar_na_lista(200, ["Portal 2"])
    db.encerrar(m.id)
    outra = db.criar_maratona(2, 10, 100, "Outro servidor")
    db.adicionar_jogos(outra.id, 200, ["X"])

    p = db.perfil_no_servidor(1, 200)
    assert (p["maratonas"], p["colocou"], p["sairam"], p["reclamacoes"]) == (1, 0, 0, 1)
    assert (p["notas"], p["media_notas"], p["favorito"]) == (1, 5.0, jogo.nome)
    assert p["lista"] == 1  # Portal 2 (os jogos que voltaram da roleta foram para a lista do 100, não do 200)
    host = db.perfil_no_servidor(1, 100)
    assert (host["maratonas"], host["colocou"], host["sairam"]) == (1, 3, 1)
    db.fechar()
