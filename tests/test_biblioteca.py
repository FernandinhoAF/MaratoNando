"""Testes do /maratona baratos, das contas da Steam e de quem conta como jogador."""

import pytest

from biblioteca import AppSteam, app_de_detalhes, calcular_opcoes, escolher_candidatos, ler_perfil, reais
from db import Database, ErroMaratona

ANA, BIA, CAIO, DUDA = 1, 2, 3, 4
AGORA = "2026-10-10T15:00:00+00:00"


def app(appid, preco=2000, gratis=False, multi=True, nome=None):
    return AppSteam(appid, nome or f"Jogo {appid}", gratis, preco, reais(preco) if preco is not None else None,
                    0, multi, multi, AGORA)


# ---------------------------------------------------------------- perfil

@pytest.mark.parametrize("texto, esperado", [
    ("https://steamcommunity.com/id/fulano/", ("nome", "fulano")),
    ("steamcommunity.com/id/Fulano_123", ("nome", "Fulano_123")),
    ("https://steamcommunity.com/profiles/76561198000000001/", ("id", "76561198000000001")),
    ("76561198000000001", ("id", "76561198000000001")),
    ("fulano", ("nome", "fulano")),
    ("isso não é um perfil!", None),
])
def test_ler_perfil(texto, esperado):
    assert ler_perfil(texto) == esperado


def test_app_de_detalhes_reais_do_peak():
    detalhes = {"name": "PEAK", "is_free": False,
                "price_overview": {"final": 1487, "final_formatted": "R$ 14,87", "discount_percent": 38},
                "categories": [{"id": 2}, {"id": 1}, {"id": 9}, {"id": 38}]}
    a = app_de_detalhes(3527290, detalhes, AGORA)
    assert (a.nome, a.preco_centavos, a.desconto, a.multiplayer, a.coop) == ("PEAK", 1487, 38, True, True)
    assert app_de_detalhes(5, None, AGORA, "Sumiu").nome == "Sumiu"


# ---------------------------------------------------------------- cálculo

def test_custo_para_o_grupo_e_ordem():
    bibs = {ANA: {10, 20, 30, 40}, BIA: {10, 20}, CAIO: {10, 30}}
    apps = {
        10: app(10, preco=9000),                # todo mundo tem: R$ 0 mesmo sendo caro
        20: app(20, preco=1500),                # falta Caio: R$ 15
        30: app(30, preco=1000),                # falta Bia: R$ 10
        40: app(40, preco=500),                 # faltam Bia e Caio: R$ 10 (empata com o 30, mas menos gente tem)
        50: app(50, preco=None, gratis=True),   # grátis, ninguém tem: R$ 0
        60: app(60, multi=False),               # single-player: fora
    }
    opcoes = calcular_opcoes(bibs, apps, [10, 20, 30, 40, 50, 60])
    assert [(o.app.appid, o.custo) for o in opcoes] == [(10, 0), (50, 0), (30, 1000), (40, 1000), (20, 1500)]
    assert opcoes[0].todos_tem and opcoes[2].faltam == [BIA] and opcoes[3].faltam == [BIA, CAIO]


def test_jogo_sem_preco_no_brasil_so_entra_se_todos_tem():
    bibs = {ANA: {1, 2}, BIA: {1}}
    apps = {1: app(1, preco=None), 2: app(2, preco=None)}
    assert [o.app.appid for o in calcular_opcoes(bibs, apps, [1, 2])] == [1]


def test_candidatos_priorizam_quem_mais_tem_e_as_listas():
    bibs = {ANA: {1, 2, 3}, BIA: {1, 2}, CAIO: {1, 4}}
    assert escolher_candidatos(bibs) == [1, 2]                 # com 3+ pessoas, ignora jogo que só 1 tem
    assert escolher_candidatos(bibs, extras={99}) == [99, 1, 2]  # da lista entra sempre
    assert escolher_candidatos({ANA: {5, 6}}) == [5, 6]       # sozinho: tudo dele


# ---------------------------------------------------------------- banco

@pytest.fixture
def db():
    banco = Database(":memory:")
    yield banco
    banco.fechar()


def test_jogadores_inclui_quem_entrou_sem_jogo(db):
    m = db.criar_maratona(1, 10, ANA, "M")
    db.adicionar_jogos(m.id, ANA, ["A"])
    db.adicionar_jogos(m.id, BIA, ["B"])
    assert db.entrar_sem_jogo(m.id, CAIO)
    assert not db.entrar_sem_jogo(m.id, ANA)   # já está (tem jogo)
    assert not db.entrar_sem_jogo(m.id, CAIO)  # já entrou
    assert db.jogadores(m.id) == [ANA, BIA, CAIO]
    assert db.extras(m.id) == [CAIO]
    db.adicionar_jogos(m.id, CAIO, ["C"])      # quem entrou sem jogo pode colocar depois
    assert db.extras(m.id) == [] and db.jogadores(m.id) == [ANA, BIA, CAIO]
    with pytest.raises(ErroMaratona):
        db.sair_sem_jogo(m.id, BIA)            # tem jogo: continua jogador
    db.entrar_sem_jogo(m.id, DUDA)
    db.sair_sem_jogo(m.id, DUDA)
    assert DUDA not in db.jogadores(m.id)


def test_contas_e_bibliotecas(db):
    db.vincular_steam(ANA, "76561198000000001", "Ana", vinculado_por=ANA)
    db.vincular_steam(BIA, "76561198000000002", "Bia", vinculado_por=ANA)  # vinculada pelo admin
    db.salvar_biblioteca(ANA, True, [10, 20])
    db.salvar_biblioteca(BIA, False, [])  # privada
    assert db.bibliotecas([ANA, BIA, CAIO]) == {ANA: {10, 20}}
    assert db.conta_steam(BIA)["vinculado_por"] == ANA
    db.vincular_steam(ANA, "76561198000000009", "Ana2", vinculado_por=ANA)  # trocou de conta
    assert db.bibliotecas([ANA]) == {} and db.conta_steam(ANA)["atualizado_em"] is None
    assert db.desvincular_steam(BIA) and not db.desvincular_steam(BIA)


def test_apps_e_listas(db):
    db.salvar_app(app(10, nome="PEAK"))
    assert db.apps([10, 11])[10]["nome"] == "PEAK"
    db.salvar_na_lista(ANA, ["PEAK"])
    db.salvar_steam_nome("PEAK", 10)
    assert db.appids_das_listas_de([ANA, BIA]) == {10}
    assert db.appids_das_listas_de([BIA]) == set()


# ---------------------------------------------------------------- resumo "quem tem"

def test_resumo_grupo():
    from biblioteca import Opcao, resumo_grupo
    peak = app(10, preco=1487, nome="PEAK")
    assert resumo_grupo(Opcao(peak, [ANA, BIA], [])) == "✅ Todo mundo já tem"
    um = resumo_grupo(Opcao(peak, [ANA], [BIA]))
    assert um == "✅ 1 tem · 🛒 falta para <@2> (**R$ 14,87** no total)"
    dois = resumo_grupo(Opcao(peak, [ANA], [BIA, CAIO]), sem_steam=[DUDA])
    assert "falta para <@2> <@3> (R$ 14,87 cada · **R$ 29,74** no total)" in dois
    assert "Não sei de <@4>" in dois
    assert "falta para 2 pessoas" in resumo_grupo(Opcao(peak, [ANA], [BIA, CAIO]), mencoes=False)
    assert resumo_grupo(Opcao(peak, [], [ANA])).startswith("❌ Ninguém tem ainda · 🛒 falta para <@1>")
    gratis = app(20, preco=None, gratis=True)
    assert resumo_grupo(Opcao(gratis, [ANA], [BIA])).startswith("🆓 Grátis")


def test_rodape_do_card():
    from biblioteca import Opcao, rodape_card
    peak = app(10, preco=1487, nome="PEAK")
    assert rodape_card(Opcao(peak, [ANA, BIA], [])) == "TODO MUNDO JÁ TEM"
    assert rodape_card(Opcao(peak, [ANA, BIA, CAIO], [DUDA])) == "3 TÊM · FALTA PRA 1 PESSOA: R$ 14,87"
    assert rodape_card(Opcao(peak, [ANA], [BIA, CAIO])) == "1 TEM · FALTA PRA 2 PESSOAS: R$ 29,74"
    assert rodape_card(Opcao(peak, [], [ANA])) == "NINGUÉM TEM · FALTA PRA 1 PESSOA: R$ 14,87"
    assert rodape_card(Opcao(app(20, preco=None, gratis=True), [ANA], [BIA])) == "GRÁTIS NA STEAM"
    assert rodape_card(Opcao(app(30, preco=None), [ANA], [BIA])) == "1 TEM · SEM PREÇO NO BRASIL"
