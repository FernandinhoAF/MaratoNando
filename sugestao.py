"""Sugestões só pela Steam (botões 🔥/🆕/💸 do /o-que-jogar): como achar friendslop sem a ajuda de um streamer.

Sem um humano filtrando, quem decide são as **tags** (etiquetas que os próprios
jogadores colocam nos jogos). A busca da Steam devolve, para cada jogo, as suas
tags principais. Um jogo só passa se tiver, ao mesmo tempo:

  1. jeito de jogo para jogar junto  → Online Co-Op, Co-op, Multiplayer, Party Game…
  2. jeito de jogo para dar risada   → Funny, Comedy, Party Game, Social Deduction, Physics, Memes…
  3. nenhuma tag de jogo "sério"     → MMO, Competitive, Sports
  4. preço baixo (padrão R$ 60) e avaliações MUITO boas (80%+, com pelo menos 200 avaliações).

O critério de avaliação é mais rígido que o do radar porque aqui não tem
ninguém testando o jogo antes.

Módulo puro: recebe o HTML da busca e devolve dados, sem acessar a internet.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from radar import PRECO_MAX_PADRAO

# IDs das tags da Steam (conferidos em store.steampowered.com/tagdata/populartags/english)
TAGS_JOGAR_JUNTO = {
    3843: "Online Co-Op", 1685: "Co-op", 3859: "Multiplayer", 7178: "Party Game", 745697: "Social Deduction",
}
TAGS_DIVERSAO = {
    4136: "Funny", 1719: "Comedy", 7178: "Party Game", 7108: "Party", 745697: "Social Deduction",
    3968: "Physics", 10397: "Memes", 597: "Casual",
}
TAGS_PROIBIDAS = {
    128: "Massively Multiplayer", 1754: "MMORPG", 3878: "Competitive", 701: "Sports",
}

APROVACAO_MINIMA = 0.80
MIN_AVALIACOES = 200

# Tipos de busca das sugestões da loja → parâmetros da busca da Steam.
TIPOS = {
    "em_alta": {"filter": "topsellers"},
    "novidades": {"filter": "popularnew"},
    "promocoes": {"filter": "topsellers", "specials": "1"},
}
NOMES_TIPOS = {"em_alta": "Em alta", "novidades": "Novidades", "promocoes": "Em promoção"}


@dataclass(frozen=True)
class ItemLoja:
    appid: int
    nome: str
    preco_centavos: int | None
    preco_texto: str | None
    desconto: int
    review_pct: float | None
    review_total: int
    tags: tuple[int, ...]
    imagem: str | None

    @property
    def url(self) -> str:
        return f"https://store.steampowered.com/app/{self.appid}/"

    @property
    def gratis(self) -> bool:
        return self.preco_centavos == 0

    @property
    def tags_divertidas(self) -> list[str]:
        return [TAGS_DIVERSAO[t] for t in self.tags if t in TAGS_DIVERSAO]


# ---------------------------------------------------------------- leitura do HTML

_LINHA = re.compile(r"<a [^>]*class=\"search_result_row.*?</a>", re.S)
_APPID = re.compile(r'data-ds-appid="(\d+)"')
_TAGS = re.compile(r'data-ds-tagids="\[([\d,\s]*)\]"')
_TITULO = re.compile(r'<span class="title">(.*?)</span>', re.S)
_PRECO = re.compile(r'data-price-final="(\d+)"')
_DESCONTO = re.compile(r'data-discount="(\d+)"')
_PRECO_TEXTO = re.compile(r'class="discount_final_price[^"]*">(.*?)</div>', re.S)
_TOOLTIP = re.compile(r'data-tooltip-html="([^"]*)"')
_AVALIACOES = re.compile(r"(\d+)% of the ([\d,.]+) user reviews")
_IMAGEM = re.compile(r'<img[^>]*src="([^"]+)"')


def _texto(trecho: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", trecho)).split())


def ler_resultados(results_html: str) -> list[ItemLoja]:
    """Transforma o HTML da busca da Steam (campo "results_html") em uma lista de jogos.

    Linhas que não são de um jogo só (pacotes, bundles) são ignoradas.
    """
    itens = []
    for linha in _LINHA.findall(results_html):
        appid = _APPID.search(linha)
        titulo = _TITULO.search(linha)
        if not appid or not titulo:
            continue
        tags = _TAGS.search(linha)
        preco = _PRECO.search(linha)
        desconto = _DESCONTO.search(linha)
        preco_texto = _PRECO_TEXTO.search(linha)
        imagem = _IMAGEM.search(linha)

        review_pct, review_total = None, 0
        tooltip = _TOOLTIP.search(linha)
        if tooltip:
            m = _AVALIACOES.search(html.unescape(tooltip.group(1)))
            if m:
                review_pct = int(m.group(1)) / 100
                review_total = int(re.sub(r"[,.]", "", m.group(2)))

        itens.append(ItemLoja(
            appid=int(appid.group(1)),
            nome=_texto(titulo.group(1)),
            preco_centavos=int(preco.group(1)) if preco else None,
            preco_texto=_texto(preco_texto.group(1)) if preco_texto else None,
            desconto=int(desconto.group(1)) if desconto else 0,
            review_pct=review_pct,
            review_total=review_total,
            tags=tuple(int(t) for t in tags.group(1).split(",") if t.strip()) if tags else (),
            imagem=html.unescape(imagem.group(1)) if imagem else None,
        ))
    return itens


# ---------------------------------------------------------------- filtro

def eh_friendslop_loja(item: ItemLoja, preco_max: int | None = None) -> bool:
    preco_max = PRECO_MAX_PADRAO if preco_max is None else preco_max
    tags = set(item.tags)
    if not tags & TAGS_JOGAR_JUNTO.keys():
        return False
    if not tags & TAGS_DIVERSAO.keys():
        return False
    if tags & TAGS_PROIBIDAS.keys():
        return False
    if item.preco_centavos is None or item.preco_centavos > preco_max:
        return False
    if item.review_total < MIN_AVALIACOES or (item.review_pct or 0) < APROVACAO_MINIMA:
        return False
    return True


def filtrar(itens: list[ItemLoja], preco_max: int | None = None) -> list[ItemLoja]:
    """Mantém só os friendslop, sem repetir, na ordem da Steam (mais populares primeiro)."""
    vistos: set[int] = set()
    saida = []
    for item in itens:
        if item.appid not in vistos and eh_friendslop_loja(item, preco_max):
            vistos.add(item.appid)
            saida.append(item)
    return saida
