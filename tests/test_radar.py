"""Testes do Radar de jogos, com respostas de verdade da Steam (copiadas em out/2026)."""

from datetime import date, datetime, timedelta, timezone

import pytest

from db import Database
from radar import (InfoJogo, eh_friendslop, eh_jogo, escolher_resultado, montar_info, normalizar,
                   ranquear)

AGORA = "2026-10-10T05:00:00+00:00"

# Trecho real de /api/storesearch/?term=peak&cc=BR
BUSCA_PEAK = [
    {"type": "app", "name": "PEAK", "id": 3527290, "price": {"currency": "BRL", "initial": 2399, "final": 2399}},
    {"type": "app", "name": "OPUS: Prism Peak", "id": 1947910},
    {"type": "app", "name": "Shadows Peak", "id": 359940},
]

# Trecho real de /api/appdetails?appids=3527290&cc=br (campo "data")
DETALHES_PEAK = {
    "type": "game", "name": "PEAK", "steam_appid": 3527290, "is_free": False,
    "price_overview": {"currency": "BRL", "initial": 2399, "final": 1487, "discount_percent": 38,
                       "initial_formatted": "R$ 23,99", "final_formatted": "R$ 14,87"},
    "categories": [{"id": 2, "description": "Single-player"}, {"id": 1, "description": "Multi-player"},
                   {"id": 9, "description": "Co-op"}, {"id": 38, "description": "Online Co-op"},
                   {"id": 22, "description": "Steam Achievements"}],
    "header_image": "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/3527290/header.jpg",
}

# Resumo real de /appreviews/3527290 (campo "query_summary")
AVALIACOES_PEAK = {"review_score": 8, "review_score_desc": "Very Positive",
                   "total_positive": 354968, "total_negative": 20884, "total_reviews": 375852}


def info(game_id="1", **campos) -> InfoJogo:
    base = dict(game_id=game_id, twitch_nome=f"Jogo {game_id}", appid=int(game_id), nome=f"Jogo {game_id}",
                gratis=False, preco_centavos=2000, preco_texto="R$ 20,00", desconto=0, multiplayer=True,
                coop=True, review_pct=0.9, review_total=1000, imagem=None, verificado_em=AGORA)
    base.update(campos)
    return InfoJogo(**base)


# ---------------------------------------------------------------- nomes e Steam

def test_normalizar_ignora_pontuacao_maiusculas_e_simbolos():
    assert normalizar("R.E.P.O.") == normalizar("r.e.p.o") == "repo"
    assert normalizar("Papers, Please") == "papers please"
    assert normalizar("Lethal Company™") == "lethal company"


def test_escolhe_so_resultado_com_nome_igual():
    assert escolher_resultado("PEAK", BUSCA_PEAK) == 3527290
    assert escolher_resultado("Peak", BUSCA_PEAK) == 3527290
    assert escolher_resultado("Prism", BUSCA_PEAK) is None  # parecido não basta


def test_categorias_da_twitch_que_nao_sao_jogos():
    assert not eh_jogo("Just Chatting")
    assert eh_jogo("PEAK")


def test_montar_info_com_dados_reais_do_peak():
    i = montar_info("123", "PEAK", DETALHES_PEAK, AVALIACOES_PEAK, AGORA)
    assert (i.appid, i.nome, i.preco_centavos, i.preco_texto, i.desconto) == (3527290, "PEAK", 1487, "R$ 14,87", 38)
    assert i.multiplayer and i.coop and not i.gratis
    assert i.review_total == 375852 and round(i.review_pct, 3) == 0.944
    assert i.url == "https://store.steampowered.com/app/3527290/"
    assert eh_friendslop(i)


def test_montar_info_sem_steam():
    i = montar_info("9", "Jogo Inexistente", None, None, AGORA)
    assert i.appid is None and not eh_friendslop(i)


# ---------------------------------------------------------------- filtro

@pytest.mark.parametrize("campos, esperado", [
    ({}, True),
    ({"multiplayer": False}, False),                          # só single-player
    ({"preco_centavos": 15000}, False),                       # caro demais
    ({"preco_centavos": 15000, "gratis": True}, True),        # grátis sempre passa
    ({"preco_centavos": None}, True),                         # sem preço no Brasil (ex.: em teste)
    ({"review_pct": 0.5}, False),                             # mal avaliado
    ({"review_pct": 0.5, "review_total": 10}, True),          # poucas avaliações: não julga ainda
    ({"appid": None}, False),                                 # não está na Steam
])
def test_filtro_friendslop(campos, esperado):
    assert eh_friendslop(info(**campos)) is esperado


def test_preco_maximo_configuravel():
    assert not eh_friendslop(info(preco_centavos=5000), preco_max=4000)
    assert eh_friendslop(info(preco_centavos=5000), preco_max=6000)


# ---------------------------------------------------------------- ranking

def test_ranking_prioriza_semana_streamers_e_dias():
    hoje = date(2026, 10, 10)
    vistos = [
        ("alanzoka", "1", "2026-09-20"), ("alanzoka", "1", "2026-09-21"), ("alanzoka", "1", "2026-09-22"),
        ("alanzoka", "2", "2026-10-08"),
        ("alanzoka", "3", "2026-10-09"), ("outro", "3", "2026-10-09"),
        ("alanzoka", "4", "2026-10-09"),  # single-player: fica de fora
    ]
    infos = {g: info(g) for g in ("1", "2", "3")} | {"4": info("4", multiplayer=False)}
    resultado = ranquear(vistos, infos, hoje)
    assert [s.info.game_id for s in resultado] == ["3", "2", "1"]
    assert resultado[0].dias_por_streamer == {"alanzoka": 1, "outro": 1}
    assert resultado[0].recente and not resultado[2].recente
    assert resultado[2].total_dias == 3


# ---------------------------------------------------------------- banco

@pytest.fixture
def db():
    banco = Database(":memory:")
    yield banco
    banco.fechar()


def test_streamers_por_servidor(db):
    assert db.radar_adicionar_streamer(1, "Alanzoka", "111", "alanzoka")
    assert not db.radar_adicionar_streamer(1, "alanzoka", "111", "alanzoka")  # repetido
    db.radar_adicionar_streamer(2, "alanzoka", "111", "alanzoka")
    db.radar_adicionar_streamer(2, "outro", "222", "Outro")
    assert db.radar_todos_logins() == ["alanzoka", "outro"]
    assert [r["login"] for r in db.radar_streamers(1)] == ["alanzoka"]
    assert db.radar_remover_streamer(1, "ALANZOKA")
    assert db.radar_streamers(1) == []


def test_vistos_jogos_e_fila_da_steam(db):
    db.radar_adicionar_streamer(1, "alanzoka", "111", "alanzoka")
    hoje = datetime.now(timezone.utc)
    assert db.radar_registrar_visto("alanzoka", "10", "PEAK", hoje.isoformat())
    assert not db.radar_registrar_visto("alanzoka", "10", "PEAK", hoje.isoformat())  # já visto
    db.radar_registrar_visto("alanzoka", "20", "Velho", (hoje - timedelta(days=60)).isoformat())
    assert [v[1] for v in db.radar_vistos(1)] == ["10"]           # o de 60 dias atrás fica fora
    assert db.radar_para_verificar() == [("10", "PEAK")]

    db.radar_salvar_jogo(montar_info("10", "PEAK", DETALHES_PEAK, AVALIACOES_PEAK, hoje.isoformat()))
    assert db.radar_para_verificar() == []                       # já conferido
    assert db.radar_jogos(["10"])["10"].preco_texto == "R$ 14,87"

    velho = (hoje - timedelta(days=10)).isoformat()
    db.radar_salvar_jogo(montar_info("10", "PEAK", DETALHES_PEAK, AVALIACOES_PEAK, velho))
    assert db.radar_para_verificar() == [("10", "PEAK")]         # preço velho: confere de novo


def test_config_e_avisos(db):
    assert db.radar_config(1) == (None, None)
    db.radar_definir_canal(1, 555)
    db.radar_definir_preco(1, 4000)
    assert db.radar_config(1) == (555, 4000)
    assert db.radar_servidores_com_alertas() == [(1, 555)]
    assert db.radar_marcar_avisado(1, "10")
    assert not db.radar_marcar_avisado(1, "10")
