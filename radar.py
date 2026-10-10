"""Regras do Radar de jogos: o que conta como "friendslop" e como ranquear as sugestões.

Ideia: um jogo só vira sugestão se
  1. algum streamer acompanhado pelo servidor jogou ele recentemente (curadoria humana), e
  2. na Steam ele é multiplayer, cabe no bolso e não é mal avaliado.

Este módulo é "puro": não acessa internet nem banco, só recebe dados e devolve
resultados. Por isso dá para testar tudo com dados de exemplo (tests/test_radar.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

JANELA_DIAS = 30            # olha o que foi jogado nos últimos 30 dias
DIAS_RECENTE = 7            # "jogou essa semana" pesa mais no ranking
PRECO_MAX_PADRAO = 6000     # R$ 60,00 (em centavos)
APROVACAO_MINIMA = 0.70     # 70% de avaliações positivas na Steam
MIN_AVALIACOES = 50         # abaixo disso a porcentagem ainda não diz muita coisa

# IDs das categorias da Steam (aparecem em /api/appdetails → categories).
CATEGORIAS_MULTIPLAYER = {
    1,   # Multi-player
    9,   # Co-op
    24,  # Shared/Split Screen
    27,  # Cross-Platform Multiplayer
    36,  # Online PvP
    37,  # Shared/Split Screen PvP
    38,  # Online Co-op
    39,  # Shared/Split Screen Co-op
    47,  # LAN PvP
    48,  # LAN Co-op
    49,  # PvP
}
CATEGORIAS_COOP = {9, 38, 39, 48}

# Categorias da Twitch que não são jogos (não vale a pena procurar na Steam).
NAO_SAO_JOGOS = {
    "just chatting", "irl", "music", "art", "asmr", "talk shows & podcasts", "special events",
    "sports", "travel & outdoors", "pools, hot tubs, and beaches", "software and game development",
    "makers & crafting", "food & drink", "games + demos", "co-working & studying", "fitness & health",
    "science & technology", "politics", "animals, aquariums, and zoos", "slots", "virtual casino",
    "beauty & body art", "dj", "chess",
}


@dataclass(frozen=True)
class InfoJogo:
    """O que o bot sabe de um jogo, juntando Twitch e Steam."""
    game_id: str                  # ID da categoria na Twitch
    twitch_nome: str
    appid: int | None             # None = não achei na Steam
    nome: str | None              # nome na Steam
    gratis: bool
    preco_centavos: int | None    # preço atual em centavos de real (None = sem preço no Brasil)
    preco_texto: str | None       # ex.: "R$ 14,87"
    desconto: int                 # % de desconto agora (0 = sem promoção)
    multiplayer: bool
    coop: bool
    review_pct: float | None      # 0.0 a 1.0
    review_total: int
    imagem: str | None
    verificado_em: str

    @property
    def nome_exibicao(self) -> str:
        return self.nome or self.twitch_nome

    @property
    def url(self) -> str | None:
        return f"https://store.steampowered.com/app/{self.appid}/" if self.appid else None


@dataclass(frozen=True)
class Sugestao:
    info: InfoJogo
    dias_por_streamer: dict[str, int]  # login → em quantos dias diferentes jogou
    ultimo_dia: str                    # "2026-10-09"
    recente: bool                      # jogado nos últimos 7 dias

    @property
    def total_dias(self) -> int:
        return sum(self.dias_por_streamer.values())


# ---------------------------------------------------------------- nomes

def normalizar(nome: str) -> str:
    """Deixa o nome comparável: "R.E.P.O.™" → "repo", "Papers, Please" → "papers please"."""
    nome = re.sub(r"[^\w\s]", "", nome.casefold())
    return " ".join(nome.replace("_", " ").split())


def eh_jogo(nome_twitch: str) -> bool:
    return nome_twitch.casefold() not in NAO_SAO_JOGOS


def escolher_resultado(nome_twitch: str, itens: list[dict]) -> int | None:
    """Escolhe, na busca da Steam, o resultado que é o mesmo jogo da Twitch.

    Só aceita nome igual (depois de normalizar). Melhor não sugerir nada do que
    sugerir o jogo errado.
    """
    alvo = normalizar(nome_twitch)
    for item in itens:
        if item.get("type", "app") == "app" and normalizar(item.get("name", "")) == alvo:
            return int(item["id"])
    return None


# ---------------------------------------------------------------- dados da Steam

def montar_info(game_id: str, twitch_nome: str, detalhes: dict | None, avaliacoes: dict | None,
                agora_iso: str, appid: int | None = None) -> InfoJogo:
    """Junta as respostas de /api/appdetails (campo "data") e /appreviews (campo "query_summary")."""
    if not detalhes:
        return InfoJogo(game_id, twitch_nome, None, None, False, None, None, 0,
                        False, False, None, 0, None, agora_iso)

    categorias = {c.get("id") for c in detalhes.get("categories", [])}
    preco = detalhes.get("price_overview") or {}
    total = int((avaliacoes or {}).get("total_reviews") or 0)
    positivas = int((avaliacoes or {}).get("total_positive") or 0)
    return InfoJogo(
        game_id=game_id,
        twitch_nome=twitch_nome,
        appid=int(detalhes.get("steam_appid") or appid),
        nome=detalhes.get("name"),
        gratis=bool(detalhes.get("is_free")),
        preco_centavos=preco.get("final"),
        preco_texto=preco.get("final_formatted"),
        desconto=int(preco.get("discount_percent") or 0),
        multiplayer=bool(categorias & CATEGORIAS_MULTIPLAYER),
        coop=bool(categorias & CATEGORIAS_COOP),
        review_pct=(positivas / total) if total else None,
        review_total=total,
        imagem=detalhes.get("header_image"),
        verificado_em=agora_iso,
    )


def eh_friendslop(info: InfoJogo, preco_max: int | None = None) -> bool:
    """Multiplayer, barato (ou grátis) e sem avaliações ruins."""
    preco_max = PRECO_MAX_PADRAO if preco_max is None else preco_max
    if info.appid is None or not info.multiplayer:
        return False
    if not info.gratis and info.preco_centavos is not None and info.preco_centavos > preco_max:
        return False
    if info.review_total >= MIN_AVALIACOES and (info.review_pct or 0) < APROVACAO_MINIMA:
        return False
    return True


# ---------------------------------------------------------------- ranking

def ranquear(vistos: list[tuple[str, str, str]], infos: dict[str, InfoJogo], hoje: date,
             preco_max: int | None = None, limite: int = 8) -> list[Sugestao]:
    """Monta as sugestões.

    vistos: [(login do streamer, game_id da Twitch, dia "AAAA-MM-DD"), ...]
    Ordem: jogado nesta semana → jogado por mais streamers → em mais dias → melhor avaliado.
    """
    por_jogo: dict[str, dict[str, set[str]]] = {}
    for login, game_id, dia in vistos:
        por_jogo.setdefault(game_id, {}).setdefault(login, set()).add(dia)

    corte_recente = (hoje - timedelta(days=DIAS_RECENTE)).isoformat()
    sugestoes = []
    for game_id, por_login in por_jogo.items():
        info = infos.get(game_id)
        if info is None or not eh_friendslop(info, preco_max):
            continue
        ultimo = max(max(dias) for dias in por_login.values())
        sugestoes.append(Sugestao(
            info=info,
            dias_por_streamer={login: len(dias) for login, dias in por_login.items()},
            ultimo_dia=ultimo,
            recente=ultimo >= corte_recente,
        ))

    sugestoes.sort(key=lambda s: (s.recente, len(s.dias_por_streamer), s.total_dias,
                                  s.info.review_pct or 0), reverse=True)
    return sugestoes[:limite]
